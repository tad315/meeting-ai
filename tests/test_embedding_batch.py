import ast
import contextlib
import json
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
import numpy as np
from app import store
from app.speech_turns import acoustic_resegment,label_turn

class BatchTests(unittest.TestCase):
    def batch_class(self):
        n=json.loads((store.ROOT/'colab/community_worker.ipynb').read_text(encoding='utf-8'))
        source=''.join(n['cells'][-1]['source'])
        tree=ast.parse(source)
        node=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='AudioEmbeddingBatch')
        scope=dict(np=np,time=time,torch=SimpleNamespace(inference_mode=contextlib.nullcontext,from_numpy=lambda x:x,cuda=SimpleNamespace(empty_cache=lambda:None)))
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<worker-batch>','exec'),scope)
        return scope['AudioEmbeddingBatch']
    def test_exact_length_grouping_reduces_calls_and_matches_single(self):
        calls=[]
        def embed(x):
            calls.append(x.shape)
            return np.stack([x.mean(axis=(1,2)),x.std(axis=(1,2))],axis=1)
        samples=np.arange(16000*8,dtype=np.float32)
        b=self.batch_class()(samples,SimpleNamespace(_embedding=embed),batch_size=16)
        ranges=[(i*.1,i*.1+1.6) for i in range(30)]
        b.prepare(ranges)
        self.assertTrue(b.batch_verified)
        self.assertLess(b.calls,len(ranges))
        count=b.calls
        for a,end in ranges:
            expected=embed(samples[round(a*16000):round(end*16000)][None,None,:])[0]
            np.testing.assert_allclose(b.at(a,end),expected)
        self.assertEqual(b.calls,count)
    def test_batch_inconsistency_falls_back_without_skipping_windows(self):
        def embed(x):
            values=np.stack([x.mean(axis=(1,2))+1,np.ones(len(x))],axis=1)
            if len(x)>1:values=values[:,::-1]
            return values
        samples=np.arange(16000*8,dtype=np.float32)
        b=self.batch_class()(samples,SimpleNamespace(_embedding=embed),batch_size=4)
        ranges=[(i*.1,i*.1+1.6) for i in range(13)]
        b.prepare(ranges)
        self.assertFalse(b.batch_verified)
        self.assertEqual(len(b.cache),len(ranges))
        for a,end in ranges:
            np.testing.assert_allclose(b.at(a,end),embed(samples[round(a*16000):round(end*16000)][None,None,:])[0])
    def test_live_preserves_supported_change_despite_weak_interjection(self):
        words=[dict(word=w,start=a,end=b) for w,a,b in [('Oh,',0,.3),('thank',.5,1),('you',1,1.5),('It',1.6,2.1),('looks',2.1,2.6),('late.',2.6,3)]]
        turn=dict(id='id',text='Oh, thank you It looks late.',start=0,end=3,speaker='1',source_channel=1,words=words)
        result=label_turn(turn,[dict(start=0,end=.3,speaker='speaker_1'),dict(start=.5,end=1.5,speaker='speaker_2'),dict(start=1.6,end=3,speaker='speaker_1')],0,3,1)
        self.assertEqual([s['speaker'] for s in result['speaker_spans']],['1_speaker_2','1_speaker_1'])
        self.assertEqual(result['id'],turn['id']);self.assertEqual(result['text'],turn['text'])

class LiveShortOverlayTests(unittest.TestCase):
    def test_confirmed_short_voice_splits_timeline_without_overlap(self):
        from app.speech_turns import overlay_verified_tracks
        base=[dict(start=0,end=3,speaker='speaker_1',evidence=dict(accepted=True,margin=.3))]
        short=[dict(start=1,end=1.5,speaker='speaker_2',evidence=dict(accepted=True,margin=.5))]
        result=overlay_verified_tracks(base,short)
        self.assertEqual([(t['start'],t['end'],t['speaker']) for t in result],[(0,1,'speaker_1'),(1,1.5,'speaker_2'),(1.5,3,'speaker_1')])
    def test_unverified_short_crop_cannot_replace_live_identity(self):
        from app.speech_turns import overlay_verified_tracks
        base=[dict(start=0,end=3,speaker='speaker_1')]
        self.assertEqual(overlay_verified_tracks(base,[dict(start=1,end=2,speaker='speaker_2',evidence=dict(accepted=False))]),base)
