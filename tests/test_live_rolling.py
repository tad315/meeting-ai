import asyncio
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import numpy as np
from app import live_rolling, store


class RollingCaptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.p=patch.object(store,'DATA',Path(self.tmp.name));self.p.start();store.init()
    def tearDown(self):self.p.stop();self.tmp.cleanup()

    def run_capture(self, packets=10, arrival=.01, inference=.001, fail=False, coalesce=False):
        calls=[]
        input_finished=asyncio.Event()
        class Socket:
            def __init__(self):self.left=packets;self.sent=[]
            async def receive(self):
                await asyncio.sleep(arrival)
                if self.left:
                    self.left-=1
                    return dict(type='websocket.receive',bytes=np.full((6400,2),1000,dtype='<i2').tobytes())
                input_finished.set()
                return dict(type='websocket.receive',text='{"type":"stop"}')
            async def send_json(self,data):self.sent.append(data)
        socket=Socket()
        def enqueue(task,audio):calls.append(task);return len(calls)
        async def wait(job,**kwargs):
            if coalesce:await input_finished.wait()
            await asyncio.sleep(inference)
            if fail:raise ValueError('Worker offline')
            return dict(asr_model='turbo',segments=[dict(start=.1,end=.8,
                text='Paul' if job==1 else "Paul, you are late again.",speaker='speaker_8')])
        with patch('app.remote_tasks.require_worker'),patch('app.remote_tasks.enqueue',enqueue),patch('app.remote_tasks.wait',wait):
            asyncio.run(live_rolling.capture(socket,dict(sources=[False,True],language='en')))
        saved=next(m for m in socket.sent if m['type']=='saved')
        return socket.sent,calls,store.get(saved['id'])

    def test_partial_is_revised_in_place_and_only_settled_once(self):
        messages,calls,item=self.run_capture()
        drafts=[t for m in messages if m['type']=='update' for t in m['turns'] if not t.get('committed')]
        self.assertGreaterEqual(len(drafts),2)
        self.assertEqual(drafts[0]['id'],drafts[1]['id'])
        self.assertNotEqual(drafts[0]['text'],drafts[1]['text'])
        self.assertTrue(all('session' not in task for task in calls))
        self.assertTrue(all(t['speaker']=='1' for t in drafts))
        self.assertEqual(len(item['segments']),1)
        self.assertEqual(item['segments'][0]['id'],drafts[0]['id'])
        self.assertIn('late again',item['segments'][0]['text'])
        self.assertEqual(item['live_caption_mode'],'revisable')
        self.assertEqual(item['status'],'ready')

    def test_slow_inference_coalesces_snapshots_and_keeps_every_audio_frame(self):
        messages,calls,item=self.run_capture(packets=20,arrival=.001,inference=.08,coalesce=True)
        self.assertLessEqual(len(calls),3)
        self.assertAlmostEqual(item['duration'],8)
        self.assertEqual(len(item['segments']),1)
        with wave.open(item['live_sources'],'rb') as reader:
            self.assertEqual(reader.getnframes(),128000)
            samples=np.frombuffer(reader.readframes(reader.getnframes()),dtype='<i2').reshape(-1,2)
            self.assertTrue(np.all(samples[:,0]==0))
            self.assertTrue(np.any(samples[:,1]!=0))
        with wave.open(item['audio'],'rb') as reader:
            mono=np.frombuffer(reader.readframes(reader.getnframes()),dtype='<i2')
            np.testing.assert_array_equal(mono,samples[:,1])
        self.assertTrue(any(t.get('committed') for m in messages if m['type']=='update' for t in m['turns']))

    def test_worker_failure_keeps_audio_and_marks_incomplete(self):
        messages,calls,item=self.run_capture(packets=4,arrival=.001,fail=True)
        self.assertEqual(item['status'],'ready')
        self.assertTrue(item['live_transcript_incomplete'])
        self.assertAlmostEqual(item['duration'],1.6)
        self.assertTrue(Path(item['audio']).is_file())

    def test_endpoint_requires_context_and_quiet_tail(self):
        self.assertIsNone(live_rolling.endpoint(np.full(16000,1000,dtype='<i2')))
        self.assertIsNone(live_rolling.endpoint(np.full(3*16000,1000,dtype='<i2')))
        signal=np.concatenate([np.full(2*16000,1000,dtype='<i2'),np.zeros(16000,dtype='<i2')])
        self.assertAlmostEqual(live_rolling.endpoint(signal)/16000,2.6)

    def test_partial_validation_rejects_invalid_timestamp_and_ignores_fake_speaker(self):
        result={'segments':[dict(start=.1,end=.8,text='hello',speaker='speaker_99')]}
        turn=live_rolling.snapshot_turn(result,1,5,1,'stable-id')
        self.assertEqual(turn['speaker'],'1')
        self.assertEqual(turn['id'],'stable-id')
        self.assertAlmostEqual(turn['start'],5.1)
        with self.assertRaises(ValueError):
            live_rolling.snapshot_turn({'segments':[dict(start=0,end=99,text='invalid')]},0,0,1,'id')


if __name__=='__main__':unittest.main()
