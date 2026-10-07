import asyncio
import json
import time
import unittest
from unittest.mock import patch
import test_demo_features as demo
from app.meeting_qa import answer, select_evidence
from app import store


class QATests(unittest.TestCase):
    setUp=demo.DemoTests.setUp
    tearDown=demo.DemoTests.tearDown
    imported=demo.DemoTests.imported

    def test_retrieval_keeps_short_reply_after_question(self):
        segments=[dict(id=str(i),text='How long does the meeting take?') for i in range(60)]
        segments[35]['text']='You take a nap in the morning?'
        segments[36]['text']='Just 15 minutes.'
        selected=select_evidence('How long is the nap?',segments)
        self.assertIn(segments[35],selected)
        self.assertIn(segments[36],selected)
        self.assertLessEqual(len(selected),16)

    def wait(self, mid):
        for _ in range(100):
            item=self.client.get('/api/recordings/'+mid).json()
            if item.get('qa_status')!='processing':return item
            time.sleep(.02)
        self.fail('QA did not finish')

    def test_answer_saved_with_exact_evidence_and_edit_clears(self):
        item=self.imported()
        async def fake(messages,provider,**kwargs):
            return json.dumps(dict(answerable=True,points=[dict(text='Design was discussed.',evidence=[dict(id='U0001',quote='discussed the design.')])]))
        with patch('app.meeting_qa.chat',fake):
            r=self.client.post(f"/api/recordings/{item['id']}/questions",json=dict(question='What was discussed?'))
            self.assertEqual(r.status_code,202,r.text)
            result=self.wait(item['id'])
        self.assertEqual(result['qa_status'],'ready',result)
        self.assertEqual(result['qa'][0]['points'][0]['evidence'][0]['id'],'U0001')
        self.client.patch(f"/api/recordings/{item['id']}/segments/U0001",json=dict(revision=1,text='Changed',speaker='0'))
        self.assertEqual(store.get(item['id'])['qa'],[])

    def test_bad_quote_rejected(self):
        item=self.imported()
        async def fake(messages,provider,**kwargs):
            return '{"answerable":true,"points":[{"text":"Budget is 10","evidence":[{"id":"U0001","quote":"Budget is 10"}]}]}'
        with patch('app.meeting_qa.chat',fake),self.assertRaises(ValueError):
            asyncio.run(answer(item,'Budget?','ollama'))

    def test_source_text_is_attached_by_application(self):
        item=self.imported()
        async def fake(messages,provider,**kwargs):
            return '{"answerable":true,"points":[{"text":"Design","evidence":[{"id":"U0001"}]}]}'
        with patch('app.meeting_qa.chat',fake):
            result=asyncio.run(answer(item,'What topic?','ollama'))
        self.assertEqual(result['points'][0]['evidence'][0]['quote'],'We discussed the design.')

    def test_insufficient_evidence(self):
        item=self.imported()
        async def fake(messages,provider,**kwargs):return '{"answerable":false,"points":[]}'
        with patch('app.meeting_qa.chat',fake):
            result=asyncio.run(answer(item,'Budget?','ollama'))
        self.assertFalse(result['answerable'])

    def test_literal_answer_resolves_wrong_id_only_with_unique_exact_match(self):
        item=self.imported()
        async def fake(messages,provider,**kwargs):
            return '{"answerable":true,"points":[{"text":"Nothing is agreed.","evidence":[{"id":"1"}]}]}'
        with patch('app.meeting_qa.chat',fake):
            result=asyncio.run(answer(item,'What was agreed?','ollama'))
        self.assertEqual(result['points'][0]['evidence'][0]['id'],'U0002')
        self.assertEqual(result['points'][0]['evidence'][0]['quote'],'Nothing is agreed.')

    def test_yes_no_does_not_repeat_unrelated_duration(self):
        item=self.imported()
        item['segments'][0]['text']='You take a nap in the morning.'
        item['segments'][1]['text']='Just 15 minutes.'
        async def fake(messages,provider,**kwargs):
            return '{"answerable":true,"points":[{"text":"15 minutes","evidence":[{"id":"2"}]}]}'
        with patch('app.meeting_qa.chat',fake):
            result=asyncio.run(answer(item,'Is that nap in the morning?','ollama'))
        self.assertTrue(result['points'][0]['text'].startswith('Transcript excerpt'))
        self.assertEqual(result['points'][0]['evidence'][0]['id'],'U0001')

    def test_inflight_edit_discards_old_answer(self):
        item=self.imported()
        async def fake(messages,provider,**kwargs):
            await asyncio.sleep(.15)
            return '{"answerable":false,"points":[]}'
        with patch('app.meeting_qa.chat',fake):
            self.client.post(f"/api/recordings/{item['id']}/questions",json=dict(question='What?'))
            self.client.patch(f"/api/recordings/{item['id']}/segments/U0001",json=dict(revision=1,text='New',speaker='0'))
            result=self.wait(item['id'])
        self.assertEqual(result['qa_status'],'error')
        self.assertFalse(result.get('qa'))

    def test_followup_receives_recent_history_but_not_stale_revision(self):
        item=self.imported()
        item['qa']=[dict(question='Old transcript question',revision=0,answerable=False,points=[])]
        item['qa'] += [dict(question=f'Question {i}',revision=1,answerable=True,
                           points=[dict(text=f'Answer {i}',evidence=[])]) for i in range(8)]
        async def fake(messages,provider,**kwargs):
            self.assertEqual(len(messages),2)
            self.assertIn('["Question 6", "Question 7"]',messages[-1]['content'])
            self.assertNotIn('Answer 7',str(messages))
            self.assertIn('LATEST QUESTION: And then?',messages[-1]['content'])
            self.assertNotIn('Old transcript question',str(messages))
            self.assertEqual(kwargs['schema']['anyOf'][0]['properties']['points']['minItems'],1)
            return '{"answerable":false,"points":[]}'
        with patch('app.meeting_qa.chat',fake):
            asyncio.run(answer(item,'And then?','ollama'))

    def test_independent_question_ignores_bad_old_answers_and_resolves_short_ids(self):
        item=self.imported()
        item['qa']=[dict(question='What topic?',revision=1,answerable=True,
                        points=[dict(text='Invented old answer',evidence=[])])]
        async def fake(messages,provider,**kwargs):
            self.assertIn('Previous questions (context only): []',messages[-1]['content'])
            self.assertNotIn('Invented old answer',str(messages))
            return '{"answerable":true,"points":[{"text":"Design","evidence":[{"id":"1"}]}]}'
        with patch('app.meeting_qa.chat',fake):
            result=asyncio.run(answer(item,'What topic?','ollama'))
        self.assertEqual(result['points'][0]['evidence'][0]['id'],'U0001')
        self.assertEqual(result['points'][0]['evidence'][0]['quote'],'We discussed the design.')

    def test_chat_keeps_older_exchanges_and_clears_pending_on_success(self):
        item=self.imported();saved=store.get(item['id'])
        saved['qa']=[dict(question=f'Question {i}',revision=1,answerable=False,points=[]) for i in range(22)]
        store.save(saved)
        async def fake(messages,provider,**kwargs):
            await asyncio.sleep(.1)
            return '{"answerable":false,"points":[]}'
        with patch('app.meeting_qa.chat',fake):
            started=self.client.post(f"/api/recordings/{item['id']}/questions",json=dict(question='And now?')).json()
            self.assertEqual(started['qa_pending'],'And now?')
            result=self.wait(item['id'])
        self.assertEqual(len(result['qa']),23)
        self.assertEqual(result['qa'][0]['question'],'Question 0')
        self.assertIsNone(result['qa_pending'])
        self.assertEqual(store.get(item['id'])['qa'],result['qa'])

    def test_multiple_answer_points_rejected(self):
        item=self.imported()
        async def fake(messages,provider,**kwargs):
            point=dict(text='Answer',evidence=[dict(id='U0001')])
            return json.dumps(dict(answerable=True,points=[point,point]))
        with patch('app.meeting_qa.chat',fake),self.assertRaises(ValueError):
            asyncio.run(answer(item,'What?','ollama'))

    def test_answerable_but_empty_answer_rejected(self):
        item=self.imported()
        async def fake(messages,provider,**kwargs):
            return '{"answerable":true,"points":[]}'
        with patch('app.meeting_qa.chat',fake),self.assertRaises(ValueError):
            asyncio.run(answer(item,'What?','ollama'))

