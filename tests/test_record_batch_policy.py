import ast,json,pathlib,unittest,time,sys
from types import SimpleNamespace

class RecordBatchTests(unittest.TestCase):
 def run_policy(self,live=False,seconds=120,fail=(),other=False):
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
     yield 'partial'
     if other:raise RuntimeError('decoder failure')
     if size in fail:raise RuntimeError('CUDA out of memory')
     yield 'complete'
    return parts(),SimpleNamespace(language='vi')
  scope=dict(task=dict(live=live,language='vi'),audio=range(seconds*16000),model=Model(),BatchedInferencePipeline=lambda m:m,sys=sys)
  fragment=fragment.replace('            from faster_whisper import BatchedInferencePipeline\n','')
  exec(textwrap.dedent(fragment),scope)
  return calls,scope
 def test_record_batch_preserves_beam_and_timestamps(self):
  calls,s=self.run_policy();self.assertEqual(calls[0][0],4)
  self.assertEqual(calls[0][1]['beam_size'],5);self.assertTrue(calls[0][1]['word_timestamps'])
  self.assertEqual(s['parts'],['partial','complete'])
 def test_lazy_oom_discards_partial_output_and_retries(self):
  calls,s=self.run_policy(fail=(4,2));self.assertEqual([c[0] for c in calls],[4,2,1]);self.assertEqual(s['parts'],['partial','complete'])
 def test_live_stays_serial(self):
  calls,s=self.run_policy(live=True);self.assertEqual(calls[0][0],1);self.assertEqual(calls[0][1]['beam_size'],3)
 def test_short_record_stays_serial(self):
  calls,s=self.run_policy(seconds=30);self.assertEqual(calls[0][0],1)
 def test_unrelated_failure_is_not_retried(self):
  with self.assertRaisesRegex(RuntimeError,'decoder failure'):self.run_policy(other=True)
