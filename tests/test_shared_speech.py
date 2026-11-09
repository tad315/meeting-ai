import ast
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from fastapi.testclient import TestClient
from app import speech_turns as core, live_word_turns, community_queue, gpu_exchange, store, step_one
from app.demo_features import import_transcript


class SharedCoreTests(unittest.TestCase):
    def test_live_adapter_is_same_splitter_not_a_separate_copy(self):
        self.assertIs(live_word_turns.sentence_turns,core.sentence_turns)
        words=[dict(word='5',start=0,end=.3),dict(word='a.m.',start=.3,end=.8),
            dict(word='Really?',start=.8,end=1.4),dict(word='Yes.',start=1.5,end=1.8)]
        result=core.record_sentences(words)
        self.assertEqual([s['text'] for s in result],['5 a.m. Really?','Yes.'])
        flattened=[w for s in result for w in s['words']]
        self.assertEqual([w['word'] for w in flattened],[w['word'] for w in words])
        self.assertEqual([(w['start'],w['end']) for w in flattened],[(w['start'],w['end']) for w in words])
    def test_real_change_can_split_inside_one_sentence_but_one_word_cannot(self):
        words=[dict(word=w,start=i*.5,end=i*.5+.45) for i,w in enumerate(['hello','there','yes','indeed.'])]
        turns=core.record_sentences(words,[dict(start=0,end=1,speaker='A'),dict(start=1,end=2,speaker='B')])
        self.assertEqual([t['speaker'] for t in turns],['A','B'])
        self.assertEqual([t['text'] for t in turns],['hello there','yes indeed.'])
        result=core.record_sentences(words,[dict(start=0,end=.45,speaker='A'),dict(start=.5,end=2,speaker='B')])
        self.assertEqual(len(result),1)
        self.assertIsNone(result[0]['speaker'])
    def test_embeddings_group_two_acoustic_voices_and_short_turns_cannot_enroll(self):
        candidate=lambda v,a,b:dict(vector=np.array(v),start=a,end=b)
        rows=[candidate([1,0],0,2),candidate([0,1],2,4),candidate([.99,.01],4,6),candidate([.01,.99],6,8),
              candidate([1,0],8,8.8),candidate([1,1],9,10)]
        labels=core.cluster_record_embeddings(rows)
        self.assertEqual(labels[0],labels[2]);self.assertEqual(labels[1],labels[3])
        self.assertNotEqual(labels[0],labels[1]);self.assertEqual(labels[0],labels[4])
        self.assertIsNone(labels[5])
        self.assertEqual(core.cluster_record_embeddings([candidate([1,0],0,1)]),[None])
        self.assertEqual(core.cluster_record_embeddings([candidate([1,0],0,2),candidate([1,0],0,2)]),[None,None])
    def test_incremental_short_sample_can_match_confirmed_identity_without_new_person(self):
        memory=core.SpeakerMemory()
        self.assertIsNone(memory.observe(np.array([1,0]),0,.8))
        confirmed=memory.observe(np.array([1,0]),1,5)
        self.assertIsNotNone(confirmed['speaker'])
        self.assertIs(memory.observe(np.array([1,0]),6,6.8),confirmed)
        self.assertEqual(len(memory.prototypes),1)
    def test_record_import_retains_verified_words_and_rejects_bad_timings(self):
        row=core.record_sentences([dict(word='Xin',start=0,end=.4),dict(word='chào.',start=.4,end=1)])[0]
        segments,_=import_transcript({'utterances':[row]})
        self.assertEqual(len(segments[0]['words']),2)
        self.assertEqual(segments[0]['words'][1]['end'],1)
        bad=copy.deepcopy(row);bad['words'][1]['end']=99
        with self.assertRaises(ValueError):import_transcript({'utterances':[bad]})


class SharedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(store,'DATA',Path(self.tmp.name));self.p.start()
        self.client=TestClient(step_one.app).__enter__()
    def tearDown(self):self.client.__exit__(None,None,None);self.p.stop();self.tmp.cleanup()
    def test_session_embeds_exact_server_core_and_refresh_preserves_it(self):
        community_queue.create('https://test.trycloudflare.com');worker=community_queue.current()
        for refresh in (False,True):
            if refresh:community_queue.refresh_notebook(worker)
            notebook=json.loads((worker[1]/'worker.ipynb').read_text(encoding='utf-8'))
            source=''.join(notebook['cells'][-1]['source'])
            embedded=next(ast.literal_eval(n.value) for n in ast.parse(source).body if isinstance(n,ast.Assign)
                and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='COMMON_TURN_SOURCE')
            self.assertEqual(embedded,(store.ROOT/'app/speech_turns.py').read_text(encoding='utf-8'))
            self.assertIn("TURN_ENGINE['record_sentences']",source)
            ast.parse(embedded)
    def test_old_worker_cannot_silently_process_new_record_mode(self):
        item=store.create('Audio','upload','community');folder=store.DATA/item['id'];folder.mkdir()
        audio=folder/'audio.wav';audio.write_bytes(b'fixture')
        store.patch(item['id'],status='ready',audio=str(audio),duration=1)
        with patch('app.remote_tasks.require_worker',side_effect=ValueError('Old worker')):
            response=self.client.post('/api/recordings/'+item['id']+'/transcribe',json=dict(provider='community',language='en',turn_policy='shared-sentence-v1'))
        self.assertEqual(response.status_code,400)
        self.assertEqual(store.get(item['id'])['status'],'ready')
    def test_new_policy_does_not_claim_legacy_cpu_uses_gpu_clustering(self):
        item=store.create('Audio','upload','local');folder=store.DATA/item['id'];folder.mkdir()
        audio=folder/'audio.wav';audio.write_bytes(b'fixture')
        store.patch(item['id'],status='ready',audio=str(audio),duration=1)
        response=self.client.post('/api/recordings/'+item['id']+'/transcribe',json=dict(provider='local',language='en',turn_policy='shared-sentence-v1'))
        self.assertEqual(response.status_code,400)
        self.assertEqual(store.get(item['id'])['status'],'ready')
    def test_policy_and_core_fingerprint_are_retained_in_validated_result(self):
        item=store.create('Audio','upload','community');folder=store.DATA/item['id'];folder.mkdir()
        audio=folder/'audio.wav';audio.write_bytes(b'fixture')
        item.update(audio=str(audio),duration=2,revision=1,turn_policy='shared-sentence-v1')
        row=core.record_sentences([dict(word='Hello.',start=0,end=1)])[0]
        payload=dict(schema='voxmeet-gpu-v1',recording_id=item['id'],revision=1,audio_sha256=gpu_exchange.audio_hash(audio),
            utterances=[row],provenance=dict(diarization_revision=gpu_exchange.REVISION,assignment_policy='shared-sentence-v1',caption_core_sha256='verified-core'))
        segments,_,provenance=gpu_exchange.validate_result(payload,item)
        self.assertTrue(segments[0]['words'])
        self.assertEqual(provenance['assignment_policy'],'shared-sentence-v1')
        payload['provenance']['assignment_policy']='legacy'
        with self.assertRaises(ValueError):gpu_exchange.validate_result(payload,item)


if __name__=='__main__':unittest.main()
