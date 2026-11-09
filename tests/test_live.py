import tempfile, unittest, wave, json, time
import asyncio
from pathlib import Path
from unittest.mock import patch
import numpy as np
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app import store,step_one,live

class FakeRecognizer:
    def create_stream(self):return self.Stream()
    class Stream:
        def accept_waveform(self,rate,data):pass
        def input_finished(self):pass
    def is_ready(self,s):return False
    def get_result(self,s):return 'test speech'
    def is_endpoint(self,s):return False
    def reset(self,s):pass

class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(store,'DATA',Path(self.tmp.name));self.p.start()
        self.client=TestClient(step_one.app);self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None);self.p.stop();self.tmp.cleanup()
    def test_channels_preserved_and_mix_no_overflow(self):
        item=store.create('test','upload','live-local')
        session=live.Session(item,FakeRecognizer(),[True,True])
        packet=np.full((3200,2),30000,dtype='<i2').tobytes()
        session.feed(packet);session.finish()
        with wave.open(item['audio']) as f:
            self.assertEqual(f.getnframes(),3200)
            self.assertEqual(np.frombuffer(f.readframes(3200),dtype='<i2')[0],30000)
        with wave.open(item['live_sources']) as f:self.assertEqual(f.getnchannels(),2)
        self.assertEqual(store.get(item['id'])['status'],'ready')
    def test_bad_packet_and_limit_preserve_recording(self):
        item=store.create('test','upload','live-local');s=live.Session(item,FakeRecognizer(),[True,False])
        with self.assertRaises(ValueError):s.feed(b'bad')
        with patch.object(live,'MAX_SECONDS',0),self.assertRaises(ValueError):s.feed(b'\0'*4)
        s.finish(interrupted=True)
        self.assertIn('interrupted',store.get(item['id'])['warning'])
    def test_websocket_save_and_reject_edits_during_capture(self):
        with patch.object(live,'create_recognizer',return_value=FakeRecognizer()):
            with self.client.websocket_connect('/api/live/stream',headers={'origin':'http://testserver'}) as ws:
                ws.send_json({'sources':[True,False]});self.assertEqual(ws.receive_json()['type'],'loading')
                mid=ws.receive_json()['id']
                ws.send_bytes(b'\0'*12800);self.assertEqual(ws.receive_json()['frames'],3200)
                self.assertEqual(self.client.post(f'/api/recordings/{mid}/questions',json={'question':'What?'}).status_code,400)
                ws.send_json({'type':'stop'});self.assertEqual(ws.receive_json()['type'],'saved')
        item=store.get(mid);self.assertEqual(item['status'],'ready');self.assertEqual(item['duration'],.2)
        self.assertEqual(len(item['segments']),1)
    def test_foreign_origin_rejected(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/api/live/stream',headers={'origin':'https://example.org'}):pass
    def test_disconnect_saves_partial(self):
        with patch.object(live,'create_recognizer',return_value=FakeRecognizer()):
            with self.client.websocket_connect('/api/live/stream',headers={'origin':'http://testserver'}) as ws:
                ws.send_json({'sources':[True,True]});ws.receive_json();mid=ws.receive_json()['id']
                ws.send_bytes(b'\0'*12800);ws.receive_json()
            for _ in range(100):
                if store.get(mid)['status']=='ready':break
                time.sleep(.01)
            self.assertEqual(store.get(mid)['status'],'ready')
            self.assertIn('interrupted',store.get(mid)['warning'])

    def test_resume_appends_audio_and_preserves_transcript_and_chat(self):
        with patch.object(live,'create_recognizer',return_value=FakeRecognizer()):
            def capture(resume=None):
                with self.client.websocket_connect('/api/live/stream',headers={'origin':'http://testserver'}) as ws:
                    ws.send_json({'sources':[True,False],'resume_id':resume});ws.receive_json();ready=ws.receive_json()
                    ws.send_bytes(np.full((3200,2),100,dtype='<i2').tobytes());ws.receive_json()
                    ws.send_json({'type':'stop'});ws.receive_json();return ready['id']
            mid=capture();original=store.get(mid)['segments'][0]
            store.patch(mid,qa=[{'question':'old question'}],summary={'text':'old summary'})
            self.assertEqual(capture(mid),mid)
        item=store.get(mid);self.assertEqual(item['segments'][0],original)
        self.assertEqual(item['duration'],.4);self.assertEqual(item['qa'][0]['question'],'old question')
        with wave.open(item['audio']) as f:self.assertEqual(f.getnframes(),6400)

    def test_live_assistant_finishes_while_audio_keeps_arriving(self):
        class EndpointRecognizer(FakeRecognizer):
            def is_endpoint(self,s):return True
        async def qa(snapshot,question,provider):
            await asyncio.sleep(.05)
            return {'answerable':True,'points':[{'text':'test speech','evidence':[{'id':snapshot['segments'][0]['id'],'quote':'test speech'}]}],'scope':'full_transcript'}
        async def summary(snapshot,provider):
            await asyncio.sleep(.05)
            return {'revision':snapshot['revision'],'sections':[],'label':'draft'}
        with patch.object(live,'create_recognizer',return_value=EndpointRecognizer()),patch.object(step_one,'answer_meeting_question',qa),patch.object(step_one,'draft_summary',summary):
            with self.client.websocket_connect('/api/live/stream',headers={'origin':'http://testserver'}) as ws:
                ws.send_json({'sources':[True,False]});ws.receive_json();mid=ws.receive_json()['id']
                ws.send_bytes(b'\0'*12800);ws.receive_json()
                self.assertEqual(self.client.post(f'/api/recordings/{mid}/questions',json={'question':'What?'}).status_code,202)
                for _ in range(20):
                    ws.send_bytes(b'\0'*12800);ws.receive_json();time.sleep(.01)
                    if store.get(mid).get('qa_status')=='ready':break
                self.assertEqual(store.get(mid)['qa_status'],'ready')
                self.assertEqual(self.client.post(f'/api/recordings/{mid}/summary',json={'provider':'ollama'}).status_code,202)
                for _ in range(20):
                    ws.send_bytes(b'\0'*12800);ws.receive_json();time.sleep(.01)
                    if store.get(mid).get('summary_status')=='ready':break
                self.assertEqual(store.get(mid)['summary_status'],'ready')
                ws.send_json({'type':'stop'});ws.receive_json()
        item=store.get(mid);self.assertTrue(item['qa']);self.assertTrue(item['summary_history'])
        self.assertGreater(len(item['segments']),2);self.assertLess(item['qa'][0]['through_seconds'],item['duration'])
