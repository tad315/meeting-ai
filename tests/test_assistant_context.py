import asyncio
import copy
import json
import unittest
from unittest.mock import patch
from app.meeting_qa import answer,select_evidence
from app.demo_features import draft_summary
from app.transcript import retrieve


def transcript(texts,language='vi'):
    return dict(revision=1,language=language,summary_language=language,speakers={'0':'Speaker A','1':'Speaker B'},
        segments=[dict(id=f'U{i:04d}',start=i*2,end=i*2+1,speaker=str(i%2),text=text) for i,text in enumerate(texts)])

class ContextTests(unittest.TestCase):
    def test_unaccented_vietnamese_query_finds_accented_source(self):
        item=transcript(['Nội dung khác.']*30)
        item['segments'][21]['text']='Ngân sách mua máy chủ là bao nhiêu?'
        item['segments'][22]['text']='Hai mươi triệu đồng.'
        selected=select_evidence('ngan sach may chu bao nhieu?',item['segments'])
        self.assertIn(item['segments'][21],selected);self.assertIn(item['segments'][22],selected)
    def test_bm25_does_not_let_long_repetition_dominate_concise_answer(self):
        rows=transcript(['Discussion.']*30,'en')['segments']
        rows[2]['text']='budget '*1000
        rows[22]['text']='Budget is twenty million.'
        self.assertEqual(retrieve('budget',rows,limit=1)[0]['id'],rows[22]['id'])
    def test_fragmented_short_answer_keeps_two_following_fragments(self):
        rows=transcript(['Unrelated discussion.']*30,'en')['segments']
        for index,text in [(15,'How long is the nap?'),(16,'Just'),(17,'15 minutes.')]:rows[index]['text']=text
        result=select_evidence('How long is the nap?',rows)
        self.assertTrue({rows[i]['id'] for i in (15,16,17)}<={s['id'] for s in result})
    def test_independent_vietnamese_question_does_not_match_ho_inside_hop(self):
        item=transcript(['Cuộc họp bàn ngân sách.']);item['qa']=[dict(revision=1,question='Câu hỏi cũ không liên quan',points=[])]
        async def fake(messages,*args,**kwargs):
            self.assertIn('Previous questions (context only): []',messages[-1]['content'])
            self.assertIn('(0:00)',messages[-1]['content'])
            return '{"answerable":false,"points":[]}'
        with patch('app.meeting_qa.chat',fake):asyncio.run(answer(item,'Cuộc họp bàn về gì?','kaggle'))
    def test_cross_language_retrieval_miss_rewrites_question_not_answer(self):
        item=transcript(['Unrelated conversation about another matter. '*20]*40,'en')
        item['segments'][22]['text']='The nap lasts fifteen minutes.'
        calls=[]
        async def fake(messages,*args,**kwargs):
            calls.append(messages)
            if len(calls)==1:return '{"query":"How long is the nap?"}'
            self.assertIn('The nap lasts fifteen minutes.',messages[-1]['content'])
            self.assertIn('LATEST QUESTION: Giấc ngủ trưa kéo dài bao lâu?',messages[-1]['content'])
            return json.dumps(dict(answerable=True,points=[dict(text='Giấc ngủ trưa kéo dài 15 phút.',evidence=[dict(id='U0022')])]))
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(item,'Giấc ngủ trưa kéo dài bao lâu?','kaggle'))
        self.assertEqual(len(calls),2);self.assertTrue(result['answerable'])
        self.assertEqual(result['points'][0]['evidence'][0]['id'],'U0022')
    def test_repair_reuses_translated_query_without_another_rewrite(self):
        item=transcript(['Unrelated conversation about another matter. '*20]*40,'en')
        item['segments'][22]['text']='The nap lasts fifteen minutes.'
        calls=[]
        async def fake(messages,*args,**kwargs):
            calls.append(messages)
            if len(calls)==1:return '{"query":"How long is the nap?"}'
            if len(calls)==2:return '{"answerable":true,"points":[]}'
            self.assertIn('The nap lasts fifteen minutes.',messages[-1]['content'])
            return json.dumps(dict(answerable=True,points=[dict(text='Giấc ngủ trưa kéo dài 15 phút.',evidence=[dict(id='U0022')])]))
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(item,'Giấc ngủ trưa kéo dài bao lâu?','kaggle'))
        self.assertTrue(result['answerable']);self.assertEqual(len(calls),3)

    def test_failed_rewrite_does_not_invent_context(self):
        item=transcript(['Unrelated conversation. '*30]*30,'en')
        async def fake(*args,**kwargs):return '{"query":null}'
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(item,'Giấc ngủ trưa bao lâu?','kaggle'))
        self.assertFalse(result['answerable']);self.assertEqual(result['points'],[])

class SummaryContextTests(unittest.TestCase):
    def test_short_record_uses_one_call_for_overview_and_analysis(self):
        item=transcript(['Chúng ta thảo luận kế hoạch ra mắt sản phẩm vào tháng tới.'])
        calls=[]
        async def fake(messages,*args,**kwargs):
            calls.append(messages)
            return json.dumps(dict(bullets=[dict(category='topic',text='Cuộc họp thảo luận kế hoạch ra mắt sản phẩm vào tháng tới.',ids=['1']),dict(category='proposal',text='Đề xuất ra mắt sản phẩm vào tháng tới.',ids=['1'])]))
        with patch('app.demo_features.chat',fake):result=asyncio.run(draft_summary(item,'kaggle'))
        self.assertEqual(len(calls),1)
        self.assertTrue(result['overview']);self.assertEqual(result['sections'][0]['bullets'][0]['category'],'discussion')
    def test_chunk_context_is_provided_but_cannot_be_cited_as_current_alias(self):
        item=transcript(['This is the beginning of a long discussion. '*80,'This preceding point sets context. '*80,'This is a later discussion of a related topic. '*80],'en')
        calls=[]
        async def fake(messages,*args,**kwargs):
            calls.append(messages[-1]['content'])
            return json.dumps(dict(bullets=[dict(category='discussion',text='The same recurring point.',ids=['1'])]))
        with patch('app.demo_features.chat',fake):result=asyncio.run(draft_summary(item,'kaggle'))
        self.assertTrue(any('PRECEDING CONTEXT (context only, never cite)' in value for value in calls))
        points=[p for section in result['sections'] for p in section['bullets']]
        self.assertEqual(len(points),1)
        self.assertGreater(len(points[0]['ids']),1)

class SummaryBudgetTests(unittest.TestCase):
    def test_single_long_turn_is_split_without_losing_source_ids_or_text(self):
        text='A detailed discussion of the production schedule and its risks. '*250
        item=transcript([text],'en');calls=[]
        async def fake(messages,*args,**kwargs):
            calls.append(messages)
            user=messages[-1]['content']
            self.assertLess(len(user),12500)
            return json.dumps(dict(bullets=[dict(category='discussion',text='The production schedule has risks.',ids=['1'])]))
        before=copy.deepcopy(item)
        with patch('app.demo_features.chat',fake):result=asyncio.run(draft_summary(item,'kaggle'))
        self.assertGreater(len(calls),2)
        self.assertEqual(item,before)
        self.assertTrue(all(p['ids']==['U0000'] for section in result['sections'] for p in section['bullets']))

class OverviewSamplingTests(unittest.TestCase):
    def test_long_source_overview_keeps_both_ends_not_only_opening(self):
        text='START_POLICY opposing team '+('extended discussion '*800)+' END_OPPOSITION'
        item=transcript([text],'en');calls=[]
        async def fake(messages,*args,**kwargs):
            calls.append(messages)
            return json.dumps(dict(bullets=[dict(category='discussion',text='The policy is disputed.',ids=['1'])]))
        with patch('app.demo_features.chat',fake):asyncio.run(draft_summary(item,'kaggle'))
        overview=calls[-1][-1]['content']
        self.assertIn('START_POLICY',overview);self.assertIn('END_OPPOSITION',overview)
        self.assertLess(len(overview),11500)
