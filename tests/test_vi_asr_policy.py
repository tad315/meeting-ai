import ast
import io
import json
import sys
import types
import unittest
from unittest.mock import patch
import numpy as np
from app import store

class VietnameseAsrPolicyTests(unittest.TestCase):
    def service(self):
        notebook=json.loads((store.ROOT/'colab/community_worker.ipynb').read_text(encoding='utf-8'))
        tree=ast.parse(''.join(notebook['cells'][-1]['source']))
        return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ASR_SERVICE' for t in node.targets))
    def run_service(self,tasks):
        calls=[];loads=[]
        class Model:
            def __init__(self,name,**kwargs):loads.append(name);self.name=name
            def transcribe(self,audio,**kwargs):
                calls.append((self.name,kwargs))
                words=[types.SimpleNamespace(start=0.,end=.8,word=' Xin chào.',probability=.9)]
                part=types.SimpleNamespace(start=0.,end=.8,avg_logprob=-.1,no_speech_prob=.01,compression_ratio=1.1,words=words)
                return iter([part]),types.SimpleNamespace(language=kwargs['language'] or 'en')
        output=io.StringIO();errors=io.StringIO()
        with patch.dict(sys.modules,{'faster_whisper':types.SimpleNamespace(WhisperModel=Model)}),patch('numpy.load',return_value=np.ones(16000,dtype=np.float32)),patch.object(sys,'stdin',io.StringIO(''.join(json.dumps(t)+'\n' for t in tasks))),patch.object(sys,'stdout',output),patch.object(sys,'stderr',errors):
            exec(compile(self.service(),'<asr-service>','exec'),{})
        return loads,calls,[json.loads(line) for line in output.getvalue().splitlines()]
    def test_vietnamese_and_english_use_separate_model_and_beam_policy(self):
        loads,calls,result=self.run_service([dict(path='audio.npy',language='vi',live=True),dict(path='audio.npy',language='en',live=True)])
        self.assertEqual(loads,['large-v3','turbo'])
        self.assertEqual([c[1]['beam_size'] for c in calls],[3,1])
        self.assertTrue(all(c[1]['temperature']==0 for c in calls))
        self.assertEqual(result[0]['asr_model'],'large-v3');self.assertTrue(result[0]['quality'])
        self.assertIn('Xin chào.',result[0]['segments'][0]['text'])
    def test_comparison_override_uses_requested_model_and_actual_provenance(self):
        loads,calls,result=self.run_service([dict(path='audio.npy',language='vi',live=True,model_override='turbo')])
        self.assertEqual(loads,['turbo']);self.assertEqual(result[0]['asr_model'],'turbo')
    def test_unknown_override_is_rejected_without_model_download(self):
        loads,calls,result=self.run_service([dict(path='audio.npy',language='vi',live=True,model_override='untrusted/model')])
        self.assertEqual(loads,[]);self.assertEqual(calls,[]);self.assertIn('error',result[0])
