import ast
import asyncio
import hashlib
import io
import json
import tempfile
import threading
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import numpy as np
from fastapi.testclient import TestClient
from app import community_queue, gpu_bridge, live_rolling, store
from app.remote_tasks import enqueue


class LiveTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
        self.p=patch.object(store,'DATA',self.data);self.p.start();store.init()
        community_queue.create('https://test.trycloudflare.com');self.worker=community_queue.current()
        n=json.loads((self.worker[1]/'worker.ipynb').read_text(encoding='utf-8'))
        source=''.join(n['cells'][-1]['source'])
        token=next(ast.literal_eval(node.value) for node in ast.parse(source).body if isinstance(node,ast.Assign)
            and isinstance(node.targets[0],ast.Name) and node.targets[0].id=='WORKER_KEY')
        self.headers={'Authorization':'Bearer '+token}
        self.bridge_patch=patch.object(gpu_bridge,'ROOT',self.data/'gpu-bridge');self.bridge_patch.start()
        self.client=TestClient(gpu_bridge.bridge).__enter__()
        self.route='/worker/'+self.worker[0]+'/next'
        self.client.post('/worker/'+self.worker[0]+'/heartbeat',headers=self.headers,json={'features':['live_asr','live_refine']})
    def tearDown(self):
        self.client.__exit__(None,None,None);self.bridge_patch.stop();self.p.stop();self.tmp.cleanup()
    def test_inline_returns_authenticated_audio_in_same_response_and_legacy_still_works(self):
        job=enqueue(dict(kind='live_asr'),b'pcm fixture')
        self.assertEqual(self.client.get(self.route+'?inline=1').status_code,401)
        self.assertEqual(self.client.get(self.route,headers=self.headers).json()['job'],job.name)
        response=self.client.get(self.route+'?inline=1',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['X-VoxMeet-Job'],job.name)
        import zipfile
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.read('audio.wav'),b'pcm fixture')
    def test_inline_empty_and_exclusion_do_not_redownload_busy_background_job(self):
        job=enqueue(dict(kind='live_refine'),b'fixture')
        self.assertEqual(self.client.get(self.route+'?inline=1&wait=0.01&exclude='+job.name,headers=self.headers).status_code,204)
        self.assertEqual(self.client.get(self.route+'?inline=1&exclude=../oops',headers=self.headers).status_code,400)
    def test_pending_index_does_not_scan_historical_jobs_and_removes_closed_markers(self):
        job=enqueue(dict(kind='live_asr'),b'fixture')
        with patch.object(Path,'glob',side_effect=AssertionError('Historical scan')):
            self.assertEqual(gpu_bridge.pending_job(self.worker[0],self.worker[1]),job.name)
            (job/'closed').touch()
            self.assertIsNone(gpu_bridge.pending_job(self.worker[0],self.worker[1]))
        self.assertFalse((self.worker[1]/'pending'/job.name).exists())
    def test_speaker_capability_is_advertised_only_after_warmup_ready(self):
        notebook=json.loads((store.ROOT/'colab/community_worker.ipynb').read_text(encoding='utf-8'))
        source=''.join(notebook['cells'][-1]['source'])
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='heartbeat')
        class Stop:
            done=False
            def is_set(self):return self.done
            def wait(self,seconds):self.done=True
            def set(self):self.done=True
        state=threading.Event();sent=[]
        def post(*args,**kwargs):sent.append(kwargs['json']['features']);return types.SimpleNamespace(status_code=200)
        namespace=dict(requests=types.SimpleNamespace(post=post,RequestException=RuntimeError),
            stop=Stop(),WORKER_URL='unused',HEADERS={},refinement_ready=state)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<heartbeat>','exec'),namespace)
        namespace['heartbeat']();self.assertNotIn('live_refine',sent[-1])
        state.set();namespace['stop']=Stop();namespace['heartbeat']();self.assertIn('live_refine',sent[-1])
    def test_live_stop_does_not_wait_for_or_enqueue_speaker_refinement(self):
        jobs=[];received=asyncio.Event()
        class Socket:
            def __init__(self):self.left=100;self.sent=[]
            async def receive(self):
                await asyncio.sleep(.001)
                if self.left:
                    self.left-=1
                    return dict(type='websocket.receive',bytes=np.full((6400,2),1000,dtype='<i2').tobytes())
                received.set();return dict(type='websocket.receive',text='{"type":"stop"}')
            async def send_json(self,data):self.sent.append(data)
        socket=Socket()
        def submit(task,audio):
            with wave.open(io.BytesIO(audio)) as reader:duration=reader.getnframes()/16000
            jobs.append((task,duration));return len(jobs)-1
        async def wait(job,**kwargs):
            task,duration=jobs[job]
            if task['kind']=='live_refine':
                await received.wait();return dict(tracks=[])
            await asyncio.sleep(.001)
            return dict(segments=[dict(start=.1,end=duration-.05,text='Hello.')],asr_model='turbo')
        with patch('app.remote_tasks.require_worker'),patch('app.community_queue.status',return_value={'features':['live_refine','shared_turns']}), \
                patch('app.remote_tasks.enqueue',submit),patch('app.remote_tasks.wait',wait):
            asyncio.run(live_rolling.capture(socket,dict(sources=[False,True],language='en')))
        saved=next(m for m in socket.sent if m['type']=='saved')
        item=store.get(saved['id'])
        self.assertTrue(jobs)
        self.assertTrue(all(task['kind']=='live_asr' for task,_ in jobs))
        self.assertEqual(item['live_speaker_mode'],'source_only')
        self.assertEqual(item['live_refinement_status'],'disabled')


if __name__=='__main__':unittest.main()
