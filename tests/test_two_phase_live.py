import ast
import asyncio
import copy
import hashlib
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import numpy as np
from fastapi.testclient import TestClient
from app import live_word_turns as words, live_rolling, community_queue, gpu_bridge, store


class WordTurnTests(unittest.TestCase):
    def sample(self):
        return dict(id='stable',start=0,end=4,text='5 a.m. Really? Yes.',speaker='1',source_channel=1,
            words=[dict(word='5',text='5',start=0,end=.2),dict(word='a.m.',text='a.m.',start=.2,end=.5),
                dict(word='Really?',text='Really?',start=.6,end=1),dict(word='Yes.',text='Yes.',start=1.1,end=1.5)])
    def test_sentences_keep_abbreviations_words_and_source_identity(self):
        original=self.sample();snapshot=copy.deepcopy(original)
        turns=words.sentence_turns(original)
        self.assertEqual([t['text'] for t in turns],['5 a.m. Really?','Yes.'])
        self.assertEqual(turns[0]['id'],'stable')
        self.assertEqual(turns[1]['id'],words.sentence_turns(original)[1]['id'])
        self.assertEqual(sum(len(t['words']) for t in turns),4)
        self.assertTrue(all(t['speaker']=='1' for t in turns))
        self.assertEqual(original,snapshot)
        self.assertFalse(words.sentence_end('Mr.'))
    def test_acoustic_spans_change_metadata_not_text_or_reference_id(self):
        turn=self.sample()
        for word,(start,end) in zip(turn['words'],[(0,.4),(.5,.9),(1,1.4),(1.5,1.9)]):word.update(start=start,end=end)
        snapshot=copy.deepcopy(turn)
        tracks=[dict(start=0,end=.95,speaker='speaker_1'),dict(start=.95,end=2,speaker='speaker_2')]
        result=words.label_turn(turn,tracks,0,5,1)
        self.assertEqual(result['id'],turn['id'])
        self.assertEqual(result['text'],turn['text'])
        self.assertEqual([s['speaker'] for s in result['speaker_spans']],['1_speaker_1','1_speaker_2'])
        self.assertIsNone(result['speaker'])
        self.assertEqual(turn,snapshot)
    def test_unknown_coverage_and_context_edges_never_force_a_person(self):
        turn=self.sample()
        self.assertEqual(words.label_turn(turn,[dict(start=3,end=4,speaker='speaker_1'),dict(start=2,end=3,speaker=None)],0,5,1),turn)
        self.assertEqual(words.label_turn(turn,[dict(start=0,end=1,speaker='speaker_1')],2,5,1),turn)
        self.assertEqual(words.label_turn(turn,[],0,5,0),turn)
    def test_tracks_reject_out_of_bounds_and_arbitrary_labels(self):
        for track in [dict(start=0,end=99,speaker='speaker_1'),dict(start=0,end=1,speaker='admin'),dict(start=0,end=1,speaker='speaker_99')]:
            with self.assertRaises(ValueError):words.validate_tracks({'tracks':[track]},5)
        self.assertEqual(words.validate_tracks({'tracks':[dict(start=0,end=1,speaker=None)]},5)[0]['speaker'],None)


class BackgroundIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.data=Path(self.tmp.name)
        self.p=patch.object(store,'DATA',self.data);self.p.start();store.init()
    def tearDown(self):self.p.stop();self.tmp.cleanup()
    def test_live_uses_source_labels_without_refinement_and_preserves_audio(self):
        jobs=[]
        class Socket:
            def __init__(self):self.remaining=10;self.sent=[]
            async def receive(self):
                await asyncio.sleep(.002)
                if self.remaining:
                    self.remaining-=1
                    return dict(type='websocket.receive',bytes=np.full((6400,2),1000,dtype='<i2').tobytes())
                return dict(type='websocket.receive',text='{"type":"stop"}')
            async def send_json(self,data):self.sent.append(data)
        socket=Socket()
        def enqueue(task,audio):
            with wave.open(io.BytesIO(audio)) as reader:duration=reader.getnframes()/16000
            jobs.append((task,duration));return len(jobs)-1
        async def wait(job,**kwargs):
            task,duration=jobs[job]
            await asyncio.sleep(.002)
            if task['kind']=='live_refine':
                self.assertTrue(any(t.get('committed') for m in socket.sent if m['type']=='update' for t in m['turns']))
                return dict(tracks=[dict(start=0,end=duration,speaker='speaker_1',evidence=dict(policy='per-track-purity-v1',accepted=True,within_track_similarity=.8,support_seconds=4))])
            return dict(asr_model='turbo',segments=[dict(start=.1,end=max(.2,duration-.05),text='Hello.')])
        with patch('app.remote_tasks.require_worker'),patch('app.community_queue.status',return_value={'features':['live_refine','shared_turns']}), \
                patch('app.remote_tasks.enqueue',enqueue),patch('app.remote_tasks.wait',wait):
            asyncio.run(live_rolling.capture(socket,dict(sources=[False,True],language='en')))
        saved=next(m for m in socket.sent if m['type']=='saved')
        item=store.get(saved['id'])
        self.assertTrue(jobs)
        self.assertTrue(all(task['kind']=='live_asr' for task,_ in jobs))
        self.assertEqual(item['live_speaker_mode'],'source_only')
        self.assertEqual(item['segments'][0]['text'],'Hello.')
        self.assertEqual(item['segments'][0]['speaker'],'1')
        self.assertTrue(item['segments'][0]['words'])
        committed=[t for m in socket.sent if m['type']=='update' for t in m['turns'] if t.get('committed')]
        self.assertEqual(committed[0]['id'],item['segments'][0]['id'])
        self.assertAlmostEqual(item['duration'],4)

    def test_refresh_worker_keeps_credentials_and_session(self):
        community_queue.create('https://test.trycloudflare.com')
        worker=community_queue.current();before=(worker[1]/'worker.ipynb').read_text(encoding='utf-8')
        community_queue.refresh_notebook(worker)
        after=json.loads((worker[1]/'worker.ipynb').read_text(encoding='utf-8'))
        self.assertEqual(community_queue.current()[0],worker[0])
        self.assertFalse((worker[1]/'closed').exists())
        source=''.join(after['cells'][-1]['source'])
        credentials={n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(source).body
            if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('WORKER_KEY','WORKER_URL')}
        self.assertEqual(hashlib.sha256(credentials['WORKER_KEY'].encode()).hexdigest(),worker[2]['token_hash'])
        self.assertTrue(credentials['WORKER_URL'].endswith('/worker/'+worker[0]))
        self.assertIn('def refine_speakers',source)
        self.assertIn('background_assistant.start()',source)
        self.assertNotIn('refinement_warmup.start()',source)

    def test_bridge_accepts_new_capability_but_prioritizes_asr(self):
        community_queue.create('https://test.trycloudflare.com');worker=community_queue.current()
        notebook=json.loads((worker[1]/'worker.ipynb').read_text(encoding='utf-8'))
        source=''.join(notebook['cells'][-1]['source'])
        token=next(ast.literal_eval(n.value) for n in ast.parse(source).body if isinstance(n,ast.Assign)
            and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='WORKER_KEY')
        headers={'Authorization':'Bearer '+token}
        with patch.object(gpu_bridge,'ROOT',self.data/'gpu-bridge'),TestClient(gpu_bridge.bridge) as client:
            self.assertEqual(client.post('/worker/'+worker[0]+'/heartbeat',headers=headers,
                json={'features':['live_asr','live_refine']}).status_code,200)
            from app.remote_tasks import enqueue
            background=enqueue(dict(kind='live_refine'),b'fixture')
            caption=enqueue(dict(kind='live_asr'),b'fixture')
            self.assertEqual(client.get('/worker/'+worker[0]+'/next',headers=headers).json()['job'],caption.name)


if __name__=='__main__':unittest.main()
