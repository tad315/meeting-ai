import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import community_queue as queue, gpu_bridge, step_one, store
from app.gpu_exchange import REVISION


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.patches = [patch.object(store,'DATA',self.data),
                        patch.object(gpu_bridge,'ROOT',self.data/'gpu-bridge')]
        for p in self.patches: p.start()
        self.client = TestClient(step_one.app).__enter__()
        self.bridge = TestClient(gpu_bridge.bridge)
        self.sid = 'a'*32
        self.key = 'test-worker-secret'
        self.folder = queue.root()/self.sid
        self.folder.mkdir(parents=True)
        (self.folder/'session.json').write_text(json.dumps(dict(kind='worker',expires=time.time()+3600,
            token_hash=hashlib.sha256(self.key.encode()).hexdigest())))
        (queue.root()/'current-worker').write_text(self.sid)
        self.headers = {'Authorization':'Bearer '+self.key}
        self.bridge.post('/worker/'+self.sid+'/heartbeat',headers=self.headers)
        self.item = store.create('fixture','upload','local')
        folder = self.data/self.item['id']; folder.mkdir(exist_ok=True)
        audio = folder/'audio.wav'; audio.write_bytes(b'fixture')
        self.item.update(audio=str(audio),duration=10,status='uploaded',segments=[])
        store.save(self.item)

    def tearDown(self):
        for event in step_one.cancel_events.values(): event.set()
        self.client.__exit__(None,None,None)
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def next_job(self):
        for _ in range(50):
            r=self.bridge.get('/worker/'+self.sid+'/next',headers=self.headers)
            if r.json()['job']: return r.json()['job']
            time.sleep(.02)
        self.fail('No queued job')

    def wait_result(self):
        for _ in range(150):
            item=store.get(self.item['id'])
            if item['status'] in ('ready','error','cancelled'): return item
            time.sleep(.03)
        self.fail('Job did not finish')

    def start(self):
        return self.client.post('/api/recordings/'+self.item['id']+'/transcribe',json={'provider':'community','num_speakers':2})

    def payload(self):
        return dict(schema='voxmeet-gpu-v1', recording_id=self.item['id'],revision=0,
            audio_sha256=hashlib.sha256(b'fixture').hexdigest(),
            provenance={'diarization_revision':REVISION},
            utterances=[dict(id='U0001',start=1,end=2,speaker='SPEAKER_00',text='Hello.')])

    def test_authenticated_queue_and_automatic_apply(self):
        self.assertEqual(self.bridge.get('/worker/'+self.sid+'/next').status_code,401)
        self.assertEqual(self.start().status_code,202)
        job=self.next_job()
        import io,zipfile
        response=self.bridge.get('/exchange/'+job+'/audio',headers=self.headers)
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            self.assertEqual(json.loads(z.read('job.json'))['num_speakers'],2)
        raw=json.dumps(self.payload())
        route='/exchange/'+job+'/result'
        self.assertEqual(self.bridge.post(route,headers=self.headers,content=raw).status_code,200)
        self.assertEqual(self.bridge.post(route,headers=self.headers,content=raw).status_code,200)
        item=self.wait_result()
        self.assertEqual(item['status'],'ready',item.get('error'))
        self.assertEqual(item['segments'][0]['text'],'Hello.')
        self.assertEqual(item['revision'],1)
        self.assertTrue(list((self.data/self.item['id']).glob('transcript-before-gpu-*.json')))

    def test_stale_result_rejected(self):
        self.start(); job=self.next_job()
        edited=store.get(self.item['id']); edited['revision']=1; store.save(edited)
        self.bridge.post('/exchange/'+job+'/result',headers=self.headers,json=self.payload())
        item=self.wait_result()
        self.assertEqual(item['status'],'error')
        self.assertEqual(item['revision'],1)
        self.assertEqual(item['segments'],[])

    def test_offline_rejected_without_legacy_fallback(self):
        (self.folder/'heartbeat').unlink()
        self.assertEqual(self.start().status_code,400)
        self.assertEqual(store.get(self.item['id'])['status'],'uploaded')

    def test_cancel_closes_exchange(self):
        self.start(); job=self.next_job()
        self.client.post('/api/recordings/'+self.item['id']+'/cancel')
        self.assertEqual(self.wait_result()['status'],'cancelled')
        self.assertEqual(self.bridge.post('/exchange/'+job+'/result',headers=self.headers,json=self.payload()).status_code,410)

    def test_worker_error_preserves_old_data(self):
        self.start();job=self.next_job()
        self.bridge.post('/exchange/'+job+'/failed',headers=self.headers)
        self.assertEqual(self.wait_result()['status'],'error')
        self.assertEqual(store.get(self.item['id'])['revision'],0)
