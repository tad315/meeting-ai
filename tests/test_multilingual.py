import asyncio
import hashlib
import io
import json
import tempfile
import time
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch, AsyncMock
import numpy as np
from fastapi.testclient import TestClient
from app import store, community_queue, gpu_bridge, remote_tasks, step_one
from app.live_gpu import validated_turns, capture
from app.meeting_qa import select_evidence, answer
from app.demo_features import draft_summary


class MultilingualTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=Path(self.tmp.name)
        self.patches=[patch.object(store,'DATA',self.data),patch.object(gpu_bridge,'ROOT',self.data/'gpu-bridge')]
        for p in self.patches:p.start()
        store.init()
        self.bridge=TestClient(gpu_bridge.bridge)
        self.sid='b'*32;self.key='test-secret'
        folder=community_queue.root()/self.sid;folder.mkdir(parents=True)
        (folder/'session.json').write_text(json.dumps(dict(kind='worker',expires=time.time()+1000,
            token_hash=hashlib.sha256(self.key.encode()).hexdigest())))
        (community_queue.root()/'current-worker').write_text(self.sid)
        self.headers={'Authorization':'Bearer '+self.key}
        self.bridge.post('/worker/'+self.sid+'/heartbeat',headers=self.headers,
            json={'features':['multilingual','llm','live_asr']})

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_llm_authenticated_roundtrip_and_closure(self):
        job=remote_tasks.enqueue(dict(kind='llm',messages=[{'role':'user','content':'Ngân sách bao nhiêu?'}]))
        endpoint='/exchange/'+job.name
        self.assertEqual(self.bridge.get(endpoint+'/audio').status_code,401)
        payload=self.bridge.get(endpoint+'/audio',headers=self.headers)
        with zipfile.ZipFile(io.BytesIO(payload.content)) as z:
            self.assertIn('Ngân sách',json.loads(z.read('task.json'))['messages'][0]['content'])
        self.bridge.post(endpoint+'/result',headers=self.headers,json={'job_id':job.name,'text':'Hai triệu đồng.'})
        result=asyncio.run(remote_tasks.wait(job))
        self.assertEqual(result['text'],'Hai triệu đồng.')
        self.assertTrue((job/'closed').exists())
        self.assertEqual(self.bridge.get(endpoint+'/audio',headers=self.headers).status_code,410)

    def test_wrong_job_and_timeout_never_accepted(self):
        job=remote_tasks.enqueue(dict(kind='llm'))
        (job/'result.json').write_text(json.dumps({'job_id':'wrong','text':'wrong'}))
        with self.assertRaises(ValueError):asyncio.run(remote_tasks.wait(job))
        job=remote_tasks.enqueue(dict(kind='llm'))
        with self.assertRaises(ValueError):asyncio.run(remote_tasks.wait(job,timeout=-1))
        self.assertTrue((job/'closed').exists())

    def test_old_worker_cannot_claim_new_features(self):
        (community_queue.root()/self.sid/'features.json').unlink()
        with self.assertRaises(ValueError):remote_tasks.enqueue(dict(kind='llm'))

    def test_unicode_retrieval_keeps_neighbors(self):
        rows=[dict(id=str(i),text='Nội dung khác',start=i,end=i+1,speaker='0') for i in range(25)]
        rows[18]['text']='Ngân sách mua máy chủ là bao nhiêu?'
        rows[19]['text']='Hai mươi triệu đồng.'
        selected=select_evidence('Ngân sách máy chủ bao nhiêu?',rows)
        self.assertIn(rows[18],selected);self.assertIn(rows[19],selected)

    def test_vietnamese_summary_category_and_evidence(self):
        item=dict(language='vi',revision=1,segments=[dict(id='a',start=0,end=2,text='Chúng ta thống nhất họp thứ hai.')])
        async def fake(messages,*args,**kw):
            self.assertIn('Vietnamese',messages[0]['content'])
            return json.dumps({'bullets':[dict(category='decision',text='Thống nhất họp thứ hai.',ids=['a'])]})
        with patch('app.demo_features.chat',fake):result=asyncio.run(draft_summary(item,'kaggle'))
        self.assertEqual(result['sections'][0]['bullets'][0]['category'],'decision')
        with patch('app.demo_features.chat',AsyncMock(return_value='{"bullets":[{"category":"decision","text":"Sai","ids":["missing"]}]}')):
            with self.assertRaises(ValueError):asyncio.run(draft_summary(item,'kaggle'))

    def test_live_overlap_not_repeated_and_invalid_time_rejected(self):
        result={'segments':[{'start':0,'end':.5,'text':'cũ'}, {'start':1.1,'end':1.4,'text':'Xin'}, {'start':1.4,'end':2,'text':'chào.'}]}
        rows=validated_turns(result,0,5,6,1)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['text'],'Xin chào.')
        self.assertEqual(rows[0]['start'],6.1)
        with self.assertRaises(ValueError):validated_turns({'segments':[dict(start=0,end=99,text='sai')]},0,0,6,0)

    def test_live_records_and_keeps_vietnamese_result(self):
        class Socket:
            def __init__(self):
                packet=np.ones((6400,2),dtype='<i2').tobytes()
                self.messages=[{'bytes':packet,'type':'websocket.receive'} for _ in range(16)]
                self.messages.append({'text':'{"type":"stop"}','type':'websocket.receive'})
                self.sent=[]
            async def receive(self):return self.messages.pop(0)
            async def send_json(self,payload):self.sent.append(payload)
        def enqueue(task,audio):
            with wave.open(io.BytesIO(audio),'rb') as wav:self.assertEqual(wav.getframerate(),16000)
            self.assertEqual(task['language'],'vi')
            return 'job'
        async def wait(*args,**kwargs):return {'segments':[dict(start=1.1,end=1.3,text='Xin chào.') ]}
        socket=Socket()
        # Final partial is 1.4 seconds, so the returned timestamp remains valid.
        with patch('app.remote_tasks.enqueue',enqueue),patch('app.remote_tasks.wait',wait):
            asyncio.run(capture(socket,dict(sources=[True,False],language='vi')))
        saved=[m for m in socket.sent if m['type']=='saved'][0]
        item=store.get(saved['id'])
        self.assertEqual(item['status'],'ready');self.assertEqual(item['language'],'vi')
        self.assertTrue(item['segments']);self.assertAlmostEqual(item['duration'],6.4)
        with wave.open(item['audio'],'rb') as wav:self.assertEqual(wav.getnframes(),102400)

    def test_upload_language_passed_and_cpu_vi_rejected(self):
        with TestClient(step_one.app) as client:
            item=store.create('Việt','upload','local');folder=self.data/item['id'];folder.mkdir()
            (folder/'audio.wav').write_bytes(b'test')
            store.patch(item['id'],audio=str(folder/'audio.wav'),duration=1,status='uploaded')
            route='/api/recordings/'+item['id']+'/transcribe'
            self.assertEqual(client.post(route,json={'provider':'local','language':'vi'}).status_code,400)
            def discard(coroutine):coroutine.close()
            with patch('app.step_one.launch',discard):
                self.assertEqual(client.post(route,json={'provider':'community','language':'vi','num_speakers':2}).status_code,202)
            item=store.get(item['id']);job=community_queue.enqueue(item)
            with zipfile.ZipFile(job/'job.zip') as z:
                metadata=json.loads(z.read('job.json'))
                self.assertEqual(metadata['language'],'vi');self.assertEqual(metadata['num_speakers'],2)
            step_one.locks.discard(item['id']);step_one.cancel_events.pop(item['id'],None)
