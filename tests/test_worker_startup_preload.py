import ast
import asyncio
import contextlib
import hashlib
import io
import json
import os
import sys
import numpy as np
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
from starlette.requests import Request
from fastapi import HTTPException
from app import community_queue, gpu_bridge, remote_tasks, store


def worker_functions():
    notebook=json.loads((store.ROOT/'colab/community_worker.ipynb').read_text(encoding='utf-8'))
    tree=ast.parse(''.join(notebook['cells'][-1]['source']))
    names={'prepare_worker','heartbeat_payload'}
    return ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[])


class WorkerStartupPreloadTests(unittest.TestCase):
    def scope(self,gpus=2,generator=None):
        calls=[]
        scope=dict(startup_state='warming',assistant_state='warming',runtime_id='a'*32,WORKER_BUILD='assistant-preload-v2.5',
            torch=types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=lambda:gpus)),
            np=types.SimpleNamespace(zeros=lambda *a,**kw:'silence',float32='float32'),
            stop=threading.Event(),refinement_ready=threading.Event(),
            translate_nllb=lambda task:calls.append(('translation',task)),
            traceback=types.SimpleNamespace(print_exc=lambda:None),
            recognize=lambda *a,**kw:calls.append(('asr',a[1])),
            generate_text=generator or (lambda task:calls.append(('llm',task))),
            publish_heartbeat=lambda:calls.append(('published',scope['startup_state'])))
        exec(compile(worker_functions(),'<worker>','exec'),scope)
        return scope,calls

    def test_dual_gpu_warms_both_before_advertising_readiness(self):
        scope,calls=self.scope()
        self.assertEqual(scope['heartbeat_payload']()['features'],[])
        with contextlib.redirect_stdout(io.StringIO()):scope['prepare_worker']()
        self.assertEqual([c[0] for c in calls],['asr','llm','translation','published'])
        self.assertEqual(calls[0][1],'vi')
        self.assertEqual(calls[1][1]['max_tokens'],4)
        state=scope['heartbeat_payload']()
        self.assertEqual(state['startup_state'],'ready')
        self.assertEqual(state['assistant_state'],'ready')
        self.assertIn('llm',state['features']);self.assertIn('live_asr',state['features'])

    def test_translation_failure_keeps_asr_chat_but_disables_translation(self):
        scope,calls=self.scope()
        def fail(task):raise RuntimeError('NLLB unavailable')
        scope['translate_nllb']=fail
        with contextlib.redirect_stdout(io.StringIO()):scope['prepare_worker']()
        self.assertEqual(scope['startup_state'],'ready')
        self.assertEqual(scope['assistant_state'],'ready')
        self.assertEqual(scope['WORKER_BUILD'],'live-translate-v3.0-draft-no-nllb')

    def test_slow_model_load_never_advertises_ready_early(self):
        started=threading.Event();finish=threading.Event()
        def slow(task):
            started.set()
            if not finish.wait(2):raise RuntimeError('test timeout')
        scope,calls=self.scope(generator=slow)
        def run():
            with contextlib.redirect_stdout(io.StringIO()):scope['prepare_worker']()
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertEqual(scope['heartbeat_payload']()['features'],[])
            self.assertEqual(scope['startup_state'],'warming')
        finally:finish.set();thread.join(2)
        self.assertEqual(scope['assistant_state'],'ready')

    def test_failed_model_load_does_not_claim_ready(self):
        def failure(task):raise RuntimeError('download failed')
        scope,calls=self.scope(generator=failure)
        with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(RuntimeError):scope['prepare_worker']()
        self.assertNotEqual(scope['startup_state'],'ready')
        self.assertNotIn('llm',scope['heartbeat_payload']()['features'])

    def test_single_gpu_reports_on_demand_instead_of_false_preload(self):
        scope,calls=self.scope(gpus=1)
        with contextlib.redirect_stdout(io.StringIO()):scope['prepare_worker']()
        self.assertEqual([c[0] for c in calls],['asr','published'])
        self.assertEqual(scope['heartbeat_payload']()['assistant_state'],'on_demand')


class RuntimeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patch=patch.object(gpu_bridge,'ROOT',self.root);self.patch.start()
        self.sid='a'*32;self.folder=self.root/self.sid;self.folder.mkdir()
        self.token='test-only-token'
        self.config=dict(kind='worker',expires=time.time()+100,token_hash=hashlib.sha256(self.token.encode()).hexdigest())
        (self.folder/'session.json').write_text(json.dumps(self.config))
        self.worker=(self.sid,self.folder,self.config)

    def tearDown(self):self.patch.stop();self.tmp.cleanup()

    def heartbeat(self,**changes):
        payload=dict(features=['llm','live_asr'],runtime_id='b'*32,worker_build='assistant-preload-v2.5',
                     startup_state='ready',assistant_state='ready',gpu_count=2)
        payload.update(changes);body=json.dumps(payload).encode()
        async def receive():return dict(type='http.request',body=body,more_body=False)
        request=Request(dict(type='http',headers=[(b'authorization',('Bearer '+self.token).encode())]),receive=receive)
        return asyncio.run(gpu_bridge.heartbeat(self.sid,request))

    def test_warming_worker_is_online_but_not_available(self):
        self.heartbeat(startup_state='warming',assistant_state='loading')
        state=community_queue.worker_status(self.worker)
        self.assertTrue(state['connected']);self.assertFalse(state['available_for_jobs'])
        self.assertFalse(state['assistant_ready']);self.assertEqual(state['features'],[])
        with patch('app.remote_tasks.queue.status',return_value=state),self.assertRaisesRegex(ValueError,'tải và nạp'):
            remote_tasks.require_worker('llm')

    def test_ready_worker_reports_loaded_assistant(self):
        self.heartbeat()
        state=community_queue.worker_status(self.worker)
        self.assertTrue(state['available_for_jobs']);self.assertTrue(state['assistant_ready'])
        self.assertEqual(state['gpu_count'],2)
        with patch('app.remote_tasks.queue.status',return_value=state):remote_tasks.require_worker('llm')

    def test_new_runtime_clears_prior_readiness(self):
        self.heartbeat();self.heartbeat(runtime_id='c'*32,startup_state='warming',assistant_state='warming')
        self.assertFalse(community_queue.worker_status(self.worker)['assistant_ready'])

    def test_failed_startup_not_available(self):
        self.heartbeat(startup_state='error',assistant_state='error')
        state=community_queue.worker_status(self.worker)
        self.assertFalse(state['available_for_jobs']);self.assertEqual(state['startup_state'],'error')

    def test_legacy_worker_does_not_inherit_previous_ready_flag(self):
        self.heartbeat()
        body=json.dumps(dict(features=['llm','live_asr'])).encode()
        async def receive():return dict(type='http.request',body=body,more_body=False)
        request=Request(dict(type='http',headers=[(b'authorization',('Bearer '+self.token).encode())]),receive=receive)
        asyncio.run(gpu_bridge.heartbeat(self.sid,request))
        state=community_queue.worker_status(self.worker)
        self.assertFalse(state['assistant_ready']);self.assertEqual(state['assistant_state'],'unknown')
        self.assertTrue(state['available_for_jobs'])

    def test_invalid_runtime_is_rejected_before_state_changes(self):
        self.heartbeat()
        before=(self.folder/'runtime.json').read_bytes()
        with self.assertRaises(HTTPException) as raised:self.heartbeat(gpu_count='2')
        self.assertEqual(raised.exception.status_code,400)
        self.assertEqual(before,(self.folder/'runtime.json').read_bytes())

    def test_expired_heartbeat_does_not_leave_assistant_ready(self):
        self.heartbeat();old=time.time()-100;os.utime(self.folder/'heartbeat',(old,old))
        self.assertFalse(community_queue.worker_status(self.worker)['assistant_ready'])

class LoadedAssistantReuseTests(unittest.TestCase):
    def test_startup_warmup_loads_once_and_real_question_reuses_model(self):
        notebook=json.loads((store.ROOT/'colab/community_worker.ipynb').read_text(encoding='utf-8'))
        tree=ast.parse(''.join(notebook['cells'][-1]['source']))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='generate_text')
        loads=[]
        class Inputs(dict):
            input_ids=np.array([[1,2]])
            def to(self,device):return self
        class Tokenizer:
            eos_token_id=0
            def apply_chat_template(self,*a,**kw):return 'prompt'
            def __call__(self,*a,**kw):return Inputs()
            def decode(self,*a,**kw):return 'OK'
        class Model:
            def eval(self):return self
            def generate(self,**kw):return np.array([[1,2,3,4,5]])
        def load_tokenizer(*a,**kw):loads.append(('tokenizer',kw));return Tokenizer()
        def load_model(*a,**kw):loads.append(('model',kw));return Model()
        transformers=types.SimpleNamespace(AutoTokenizer=types.SimpleNamespace(from_pretrained=load_tokenizer),
            AutoModelForCausalLM=types.SimpleNamespace(from_pretrained=load_model),StoppingCriteria=object,StoppingCriteriaList=list)
        scope=dict(llm_model=None,llm_tokenizer=None,assistant_state='warming',time=time,HF_TOKEN='test-only-token',
            publish_heartbeat=lambda:None,release_asr=lambda:None,json=json,stop=threading.Event(),
            torch=types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=lambda:2),float16='float16',inference_mode=contextlib.nullcontext))
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<assistant>','exec'),scope)
        with patch.dict(sys.modules,{'transformers':transformers}),contextlib.redirect_stdout(io.StringIO()):
            warm=scope['generate_text'](dict(job_id='startup',messages=[],max_tokens=4))
            answer=scope['generate_text'](dict(job_id='question',messages=[],max_tokens=100))
        self.assertEqual([name for name,_ in loads],['tokenizer','model'])
        self.assertEqual(loads[1][1]['device_map'],'cuda:1')
        self.assertTrue(warm['timing']['cold_start']);self.assertFalse(answer['timing']['cold_start'])
        self.assertEqual(answer['timing']['output_tokens'],3)
        self.assertEqual(scope['assistant_state'],'ready')

if __name__=='__main__':unittest.main()
