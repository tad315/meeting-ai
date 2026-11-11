import asyncio,copy,json,unittest
from unittest.mock import patch
from app import live_translation as tr
from app.recording_translation import bilingual_text,is_current

class TranslationTests(unittest.IsolatedAsyncioTestCase):
 def item(self):
  return dict(id='test',title='Meeting',revision=1,status='ready',segments=[dict(id='a',text='Good morning.',start=1,end=2,speaker=None)],speakers={},live_translation=dict(generation='g',enabled=True,source_language='en',target_language='vi',segments=[]))
 def test_source_revision_discards_late_result(self):
  item=self.item();requested=copy.deepcopy(item['segments']);item['segments'][0]['text']='Good evening.'
  result=tr.merge_result(item,'g',requested,['Chào buổi sáng.'])
  self.assertEqual(result['segments'],[])
 def test_direction_switch_discards_late_result(self):
  item=self.item();self.assertIsNone(tr.merge_result(item,'old',item['segments'],['Xin chào.']))
 def test_disabled_discards_result(self):
  item=self.item();item['live_translation']['enabled']=False
  self.assertIsNone(tr.merge_result(item,'g',item['segments'],['Xin chào.']))
 def test_new_speech_stays_pending(self):
  item=self.item();requested=copy.deepcopy(item['segments'])
  item['segments'].append(dict(id='b',text='Thank you.',start=3,end=4,speaker=None))
  state=tr.merge_result(item,'g',requested,['Chào buổi sáng.']);item['live_translation']=state
  self.assertEqual([r['id'] for r in tr.pending(item)],['b'])
 async def test_pump_saves_record_and_export_without_changing_source(self):
  item=self.item();original=copy.deepcopy(item['segments'])
  def update(mid,**kw):item.update(kw);return copy.deepcopy(item)
  async def translate(rows,source,target,context=None):
   self.assertEqual((source,target),('en','vi'));return ['Chào buổi sáng.']
  with patch.object(tr.store,'get',lambda mid:copy.deepcopy(item)),patch.object(tr.store,'patch',update),patch.object(tr,'translate_sentence',translate):
   await tr.pump('test','g')
  self.assertEqual(item['segments'],original);self.assertEqual(item['live_translation']['status'],'ready')
  self.assertTrue(is_current(item));self.assertIn('Chào buổi sáng.',bilingual_text(item))
 async def test_failure_keeps_original_and_partial_translation(self):
  item=self.item()
  def update(mid,**kw):item.update(kw);return copy.deepcopy(item)
  async def fail(*a):raise ValueError('offline')
  with patch.object(tr.store,'get',lambda mid:copy.deepcopy(item)),patch.object(tr.store,'patch',update),patch.object(tr,'translate_sentence',fail):await tr.pump('test','g')
  self.assertEqual(item['live_translation']['status'],'error');self.assertTrue(item['translation']['partial'])
  self.assertEqual(item['segments'][0]['text'],'Good morning.')
 async def test_turn_split_reassembles_all_parts(self):
  rows=[dict(id='a',text='hello '*200)]
  tasks=[]
  async def chat(messages,schema,maximum):
   payload=json.loads(messages[-1]['content']);tasks.append(payload)
   return json.dumps(dict(translations=[dict(id=u['id'],text=u['text']) for u in payload['CURRENT']]))
  with patch.object(tr.remote_tasks,'chat',chat):result=await tr.translate_rows(rows,'en','vi')
  self.assertEqual(result[0],' '.join(rows[0]['text'].split()))
  self.assertTrue(all(len(u['text'])<=450 for t in tasks for u in t['CURRENT']))
 def test_available_handles_old_worker(self):
  with patch.object(tr.community_queue,'status',return_value=dict(gpu_count=None,available_for_jobs=True)):self.assertFalse(tr.available())

 def test_context_is_preceding_only_and_neutral_policy(self):
  item=self.item();item['segments']=[dict(id=str(i),text='sentence '+str(i)) for i in range(7)]
  context=tr.preceding_context(item,[item['segments'][4]])
  self.assertEqual(context,['sentence 1','sentence 2','sentence 3'])
  messages=tr.translation_messages([dict(id='4',text='Yes.')],'en','vi',context)
  self.assertIn('tôi/bạn',messages[0]['content'])
  payload=json.loads(messages[1]['content']);self.assertEqual(payload['CURRENT'],[dict(id='4',text='Yes.')])
  self.assertNotIn('sentence 5',messages[1]['content'])

 def test_extra_brace_is_recoverable_but_truncation_is_not(self):
  good=json.dumps(dict(translations=[dict(id='1',text='Xin chào.')]))
  self.assertEqual(tr.recording_translation.decode(good+'}',[dict(id='1')]),{'1':'Xin chào.'})
  for bad in [good[:-2],good+' prose',good+'}}']:
   with self.assertRaises(ValueError):tr.recording_translation.decode(bad,[dict(id='1')])
 def test_neutral_guard_preserves_explicit_roles(self):
  self.assertTrue(tr.unsupported_address("You're late.",'Em lại trễ rồi.'))
  self.assertFalse(tr.unsupported_address('My sister is here.','Chị tôi ở đây.'))
  self.assertFalse(tr.unsupported_address('Anh, are you ready?','Anh, bạn sẵn sàng chưa?'))
 async def test_neutral_address_does_not_stop_translation(self):
  from unittest.mock import AsyncMock
  response=json.dumps(dict(translations=[dict(id='1',text='Em lại trễ rồi.')]))
  with patch.object(tr.remote_tasks,'chat',AsyncMock(return_value=response)) as chat:
   self.assertEqual(await tr.translate_rows([dict(id='a',text="You're late again.")],'en','vi'),['Bạn lại trễ rồi.'])
   self.assertEqual(chat.await_count,1)
  self.assertEqual(tr.neutral_address('I never wake early.','Em không dậy sớm.'),'Tôi không dậy sớm.')
  self.assertEqual(tr.neutral_address('I asked you.','Em hỏi anh.'),'Em hỏi anh.')

 async def test_fast_sentence_has_no_json_and_bounded_context(self):
  from unittest.mock import AsyncMock
  with patch.object(tr.remote_tasks,'chat',AsyncMock(return_value='Bạn lại trễ rồi.')) as chat:
   result=await tr.translate_sentence([dict(id='a',text="You are late again.")],'en','vi',['old','previous','latest'])
   self.assertEqual(result,['Bạn lại trễ rồi.'])
   messages,schema,budget=chat.call_args.args
   self.assertIsNone(schema)
   self.assertEqual(json.loads(messages[1]['content'])['PREVIOUS'],['previous','latest'])
 async def test_pump_publishes_each_turn_before_next_request(self):
  item=self.item();item['segments'] += [dict(id='b',text='Thanks.',start=3,end=4)]
  seen=[]
  def update(mid,**kw):item.update(kw);return copy.deepcopy(item)
  async def translate(rows,*args):
   self.assertEqual(len(rows),1)
   seen.append(len(item['live_translation']['segments']))
   return ['Xin chào.']
  with patch.object(tr.store,'get',lambda mid:copy.deepcopy(item)),patch.object(tr.store,'patch',update),patch.object(tr,'translate_sentence',translate):await tr.pump('test','g')
  self.assertEqual(seen,[0,1])
  self.assertEqual(item['live_translation']['status'],'ready')

 async def test_draft_revision_drops_stale_result_and_is_not_saved(self):
  item=self.item();item.update(status='recording',segments=[])
  old=dict(id='draft',text='I can',start=0,end=1,source_channel=1,committed=False)
  def update(mid,**kw):item.update(kw);return copy.deepcopy(item)
  calls=[]
  async def translate(rows,*args):
   calls.append(rows[0]['text'])
   if len(calls)==1:
    tr.observe('test',[dict(old,text='I cannot')])
    return ['Tôi có thể']
   self.assertEqual(tr.previews('test'),[])
   item['status']='ready'
   return ['Tôi không thể']
  with patch.object(tr.store,'get',lambda mid:copy.deepcopy(item)),patch.object(tr.store,'patch',update),patch.object(tr,'translate_sentence',translate):
   tr.observe('test',[old]);await tr.pump('test','g')
  self.assertEqual(calls,['I can','I cannot'])
  self.assertEqual(item['segments'],[])
  self.assertEqual(item['translation']['segments'],[])
  tr.drafts.clear();tr.draft_results.clear()
 def test_remove_and_latest_draft_are_bounded(self):
  item=self.item()
  with patch.object(tr.store,'get',lambda mid:item):
   for i in range(100):tr.observe('test',[dict(id=str(i),text=str(i),source_channel=1,committed=False)])
   self.assertEqual(list(tr.drafts['test']),['99'])
   tr.observe('test',[],['99']);self.assertEqual(tr.drafts['test'],{})
  tr.drafts.clear();tr.draft_results.clear()
