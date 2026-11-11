import ast
import asyncio
import io
import json
import threading
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from app import store
import test_demo_features as demo

class ParallelAssistantTests(unittest.TestCase):
    def test_dual_gpu_worker_keeps_asr_running_during_llm_generation(self):
        notebook=json.loads((store.ROOT/'colab/community_worker.ipynb').read_text(encoding='utf-8'))
        source=''.join(notebook['cells'][-1]['source']);tree=ast.parse(source)
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_background_assistant')
        loop=next(n for n in tree.body if isinstance(n,ast.Try) and n.body and isinstance(n.body[0],ast.While) and 'worker_http.get' in ast.get_source_segment(source,n))
        started=threading.Event();asr=threading.Event();returned=threading.Event();stop=threading.Event();posted=[]
        class Response:
            def __init__(self,job=None,kind=None):
                self.status_code=200 if job else 204;self.headers={'X-VoxMeet-Job':job} if job else {}
                data=io.BytesIO()
                if job:
                    with zipfile.ZipFile(data,'w') as z:z.writestr('task.json',json.dumps(dict(kind=kind)))
                self.content=data.getvalue()
            def raise_for_status(self):pass
        def post(url,**kwargs):
            posted.append(url)
            if '/llm/result' in url:returned.set()
            return Response('ok')
        class Http:
            calls=0
            def get(self,*args,**kwargs):
                self.calls+=1
                if self.calls==1:return Response('llm','llm')
                if self.calls==2:
                    if not started.wait(1):raise AssertionError('Assistant did not start')
                    return Response('asr','live_asr')
                if not returned.wait(1):raise AssertionError('Assistant result did not return')
                stop.set();return Response()
            def post(self,*args,**kwargs):return post(*args,**kwargs)
            def close(self):pass
        def dispatch(raw):
            with zipfile.ZipFile(io.BytesIO(raw)) as z:kind=json.loads(z.read('task.json'))['kind']
            if kind=='llm':
                started.set()
                if not asr.wait(1):raise AssertionError('ASR was blocked by assistant')
            else:asr.set()
            return b'{}'
        scope=dict(threading=threading,time=time,requests=types.SimpleNamespace(post=post,RequestException=RuntimeError),
            traceback=types.SimpleNamespace(print_exc=lambda:None),stop=stop,HEADERS={},BASE='https://test',WORKER_URL='https://test/worker',
            worker_http=Http(),background_assistant=None,background_assistant_id='',torch=types.SimpleNamespace(cuda=types.SimpleNamespace(device_count=lambda:2)),
            last_outbox_retry=0,flush_outbox_once=lambda:None,pending_result_ids=lambda:[],
            send_result=lambda job,raw:post('https://test/exchange/'+job+'/result',data=raw),
            zipfile=zipfile,io=io,json=json,dispatch=dispatch,thread=types.SimpleNamespace(join=lambda **kw:None),release_asr=lambda:None,release_llm=lambda:None)
        exec(compile(ast.Module(body=[function,loop],type_ignores=[]),'<worker>','exec'),scope)
        self.assertTrue(asr.is_set());self.assertTrue(returned.is_set())
        self.assertTrue(any('/asr/result' in url for url in posted))
        self.assertTrue(any('/llm/result' in url for url in posted))

class LiveSnapshotCoverageTests(unittest.TestCase):
    setUp=demo.DemoTests.setUp
    tearDown=demo.DemoTests.tearDown
    imported=demo.DemoTests.imported
    def wait_for(self,mid,key):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            item=self.client.get('/api/recordings/'+mid).json()
            if item.get(key)!='processing':return item
            time.sleep(.01)
        self.fail('Assistant did not complete')
    def test_summary_covers_snapshot_while_new_live_text_is_preserved(self):
        item=self.imported();end=max(t['end'] for t in item['segments'])
        store.patch(item['id'],status='recording',live_sources='sources.wav')
        async def fake(snapshot,provider):
            await asyncio.sleep(.08)
            return dict(revision=snapshot['revision'],overview=[],sections=[])
        with patch('app.step_one.draft_summary',fake):
            started=self.client.post('/api/recordings/'+item['id']+'/summary',json=dict(provider='ollama')).json()
            self.assertEqual(started['summary_pending_through'],end)
            self.assertIsNotNone(started['summary_started_at'])
            extra=dict(id='new',start=end+1,end=end+2,speaker='0',text='New live words.')
            store.patch(item['id'],segments=item['segments']+[extra])
            result=self.wait_for(item['id'],'summary_status')
        self.assertEqual(result['summary']['through_seconds'],end)
        self.assertEqual(result['segments'][-1]['id'],'new')
        self.assertIsNone(result['summary_pending_through'])
    def test_chat_reports_snapshot_coverage_and_keeps_new_live_text(self):
        item=self.imported();end=max(t['end'] for t in item['segments'])
        store.patch(item['id'],status='recording',live_sources='sources.wav')
        async def fake(snapshot,question,provider):
            await asyncio.sleep(.08);return dict(answerable=False,points=[],scope='full_transcript')
        with patch('app.step_one.answer_meeting_question',fake):
            started=self.client.post('/api/recordings/'+item['id']+'/questions',json=dict(provider='ollama',question='What happened?')).json()
            self.assertEqual(started['qa_pending_through'],end)
            extra=dict(id='new',start=end+1,end=end+2,speaker='0',text='New live words.')
            store.patch(item['id'],segments=item['segments']+[extra])
            result=self.wait_for(item['id'],'qa_status')
        self.assertEqual(result['qa'][0]['through_seconds'],end)
        self.assertEqual(result['segments'][-1]['id'],'new')
        self.assertIsNone(result['qa_pending_through'])
