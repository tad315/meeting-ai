import ast,json,threading,unittest,types
from pathlib import Path

class IndependentLaneTests(unittest.TestCase):
 def test_assistant_pull_dispatches_without_asr_loop(self):
  nb=json.loads(Path('colab/community_worker.ipynb').read_text(encoding='utf-8'))
  source=next(''.join(c['source']) for c in nb['cells'] if 'def assistant_pull_loop():' in ''.join(c.get('source',[])))
  fn=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='assistant_pull_loop')
  stop=threading.Event();calls=[]
  class HTTP:
   def get(self,url,**kwargs):
    calls.append(kwargs['params']['lane'])
    return types.SimpleNamespace(status_code=200,headers={'X-VoxMeet-Job':'b'*32},content=b'job',raise_for_status=lambda:None)
   def close(self):calls.append('closed')
  def dispatch(endpoint,raw):
   self.assertEqual(endpoint,'https://example.test/exchange/'+'b'*32)
   self.assertEqual(raw,b'job');calls.append('dispatched');stop.set()
  scope=dict(requests=types.SimpleNamespace(Session=HTTP,RequestException=OSError),stop=stop,WORKER_URL='https://example.test/worker/'+'a'*32,HEADERS={},pending_result_ids=lambda:[],run_background_assistant=dispatch)
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'worker','exec'),scope)
  scope['assistant_pull_loop']()
  self.assertEqual(calls,['assistant','dispatched','closed'])
