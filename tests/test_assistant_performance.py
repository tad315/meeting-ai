import asyncio,copy,json,unittest
from unittest.mock import patch
from app import assistant_cache as cache
from app.demo_features import draft_summary
from app.meeting_qa import answer
from app.transcript import retrieve

class CacheTests(unittest.TestCase):
 def setUp(self):cache._entries.clear()
 def test_isolated_exact_content_and_expiry(self):
  a=cache.key('meeting-a','kaggle',{'text':'old'})
  cache.put(a,{'points':[1]});value=cache.get(a);value['points'].append(2)
  self.assertEqual(cache.get(a),{'points':[1]})
  self.assertIsNone(cache.get(cache.key('meeting-b','kaggle',{'text':'old'})))
  self.assertIsNone(cache.get(cache.key('meeting-a','kaggle',{'text':'new'})))
  with patch('app.assistant_cache.time.monotonic',return_value=10**15):self.assertIsNone(cache.get(a))
 def test_memory_bound(self):
  for n in range(120):cache.put(str(n),n)
  self.assertEqual(len(cache._entries),cache.MAX_ENTRIES)
 def item(self):
  return dict(id='fixture',revision=1,language='en',speakers={},segments=[dict(id='s1',text='The delivery is on Friday.',speaker=None,start=0,end=3)],qa=[])
 def test_summary_reuses_only_identical_context(self):
  item=self.item();calls=[]
  async def fake(*a,**k):
   calls.append(1);return json.dumps({'bullets':[{'category':'topic','text':'Delivery is scheduled.','ids':['1']}]})
  with patch('app.demo_features.chat',fake):
   first=asyncio.run(draft_summary(item,'kaggle'));second=asyncio.run(draft_summary(item,'kaggle'))
   self.assertEqual(second['timing']['cached_calls'],1);self.assertEqual(first['timing']['cached_calls'],0)
   first.pop('timing');second.pop('timing')
   self.assertEqual(first,second);self.assertEqual(len(calls),1)
   item['segments'][0]['text']='The delivery was cancelled.'
   asyncio.run(draft_summary(item,'kaggle'));self.assertEqual(len(calls),2)
 def test_live_append_invalidates_chat(self):
  item=self.item();calls=[]
  async def fake(*a,**k):calls.append(1);return dict(answerable=True,points=[dict(text='Friday',evidence=[dict(id='s1')])])
  with patch('app.meeting_qa._answer_uncached',fake):
   asyncio.run(answer(item,'When?','kaggle'));result=asyncio.run(answer(item,'When?','kaggle'))
   self.assertTrue(result['cache_hit']);self.assertEqual(len(calls),1)
   item['segments'].append(dict(id='s2',text='Correction: Monday.',speaker=None,start=5,end=6))
   asyncio.run(answer(item,'When?','kaggle'));self.assertEqual(len(calls),2)
 def test_failed_answers_are_not_cached(self):
  item=self.item();calls=[]
  async def fake(*a,**k):calls.append(1);return dict(answerable=False,points=[])
  with patch('app.meeting_qa._answer_uncached',fake):
   for _ in range(2):asyncio.run(answer(item,'When?','kaggle'))
  self.assertEqual(len(calls),2)
 def test_retrieval_keeps_distant_relevant_anchors(self):
  rows=[dict(id=str(i),text='unrelated words') for i in range(40)]
  rows[3]['text']='Budget budget: the proposed amount.'
  rows[30]['text']='Budget was rejected because funding is unavailable.'
  selected=retrieve('budget',rows,limit=6)
  self.assertTrue({'3','30'}<={r['id'] for r in selected})
