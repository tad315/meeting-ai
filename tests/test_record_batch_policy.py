import ast,io,json,pathlib,unittest,time,sys
from contextlib import redirect_stdout
from types import SimpleNamespace

class RecordBatchTests(unittest.TestCase):
 def run_policy(self,live=False,seconds=120,fail=(),other=False,selected='large-v3'):
  nb=json.loads(pathlib.Path('colab/community_worker.ipynb').read_text(encoding='utf-8'))
  tree=ast.parse(''.join(nb['cells'][-1]['source']))
  service=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ASR_SERVICE' for t in n.targets))
  import textwrap
  fragment=service[service.index('        options=dict'):service.index('        words=[]')]
  calls=[]
  class Model:
   def transcribe(self,a,**kw):
    size=kw.get('batch_size',1);calls.append((size,kw))
    def parts():
     yield SimpleNamespace(end=seconds/2)
     if other:raise RuntimeError('decoder failure')
     if size in fail:raise RuntimeError('CUDA out of memory')
     yield SimpleNamespace(end=seconds)
    return parts(),SimpleNamespace(language='vi')
  scope=dict(task=dict(live=live,language='vi'),audio=range(seconds*16000),model=Model(),BatchedInferencePipeline=lambda m:m,sys=sys,json=json,time=time,selected=selected)
  fragment=fragment.replace('            from faster_whisper import BatchedInferencePipeline\n','')
  out=io.StringIO()
  with redirect_stdout(out):exec(textwrap.dedent(fragment),scope)
  scope['events']=[json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
  return calls,scope
 def test_record_batch_preserves_beam_and_timestamps(self):
  calls,s=self.run_policy();self.assertEqual(calls[0][0],8)
  self.assertEqual(calls[0][1]['beam_size'],5);self.assertTrue(calls[0][1]['word_timestamps'])
  self.assertEqual([p.end for p in s['parts']],[60,120])
 def test_turbo_record_starts_with_large_batch(self):
  calls,s=self.run_policy(selected='turbo');self.assertEqual(calls[0][0],16)
 def test_record_reports_decoding_progress(self):
  calls,s=self.run_policy()
  self.assertTrue(s['events']);self.assertEqual(s['events'][0]['event'],'progress')
  self.assertTrue(0<s['events'][0]['fraction']<=1)
 def test_lazy_oom_discards_partial_output_and_retries(self):
  calls,s=self.run_policy(fail=(8,4,2));self.assertEqual([c[0] for c in calls],[8,4,2,1]);self.assertEqual([p.end for p in s['parts']],[60,120])
  calls,s=self.run_policy(fail=(16,8),selected='turbo');self.assertEqual([c[0] for c in calls],[16,8,4])
 def test_live_stays_serial(self):
  calls,s=self.run_policy(live=True);self.assertEqual(calls[0][0],1);self.assertEqual(calls[0][1]['beam_size'],3);self.assertEqual(s['events'],[])
 def test_short_record_stays_serial(self):
  calls,s=self.run_policy(seconds=30);self.assertEqual(calls[0][0],1)
 def test_unrelated_failure_is_not_retried(self):
  with self.assertRaisesRegex(RuntimeError,'decoder failure'):self.run_policy(other=True)
