import asyncio
import copy
import json
import unittest
from unittest.mock import patch
from app.meeting_qa import answer, continuation_passages


def fixture():
    texts=['Lan đề nghị','Từ nay, mỗi khi người soạn thảo gửi báo cáo.',
           'Người phụ trách cần yêu cầu đọc lại nội dung trước khi gửi.',
           'Đọc lại nội dung một lần nữa đã.']
    return dict(revision=1,language='vi',live_sources='sources.wav',speakers={'1':'Âm thanh cuộc họp'},
        segments=[dict(id=f'F{i+1}',start=start,end=end,speaker='1',text=text)
                  for i,(text,start,end) in enumerate(zip(texts,[79,81,86,92],[80,84,91,94]))])


def response(text,ids):return json.dumps(dict(answerable=True,points=[dict(text=text,evidence=[dict(id=sid) for sid in ids])]),ensure_ascii=False)


class AdjacentPassageTests(unittest.TestCase):
    def test_intro_condition_and_actual_action_are_read_together(self):
        rows=fixture()['segments'];before=copy.deepcopy(rows)
        groups=continuation_passages(rows,'Lan đã đề nghị gì?')
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['prefix_ids'],['F1','F2'])
        self.assertEqual(groups[0]['content_ids'],['F3','F4'])
        self.assertIn('đọc lại nội dung',groups[0]['text'])
        self.assertEqual(rows,before)

    def test_retrieved_non_neighbors_are_not_joined(self):
        rows=fixture()['segments']
        self.assertEqual(continuation_passages([rows[0],rows[2]],'Lan đề nghị gì?',{'F1':0,'F3':2}),[])

    def test_large_time_gap_does_not_imply_continuation(self):
        rows=fixture()['segments'];rows[1]['start']=120;rows[1]['end']=124
        self.assertEqual(continuation_passages(rows,'Lan đề nghị gì?'),[])

    def test_new_explicit_intro_stops_previous_passage(self):
        rows=fixture()['segments'];rows[1]['text']='Nam đề nghị'
        self.assertFalse(any(g['ids'][0]=='F1' for g in continuation_passages(rows,'Lan đề nghị gì?')))

    def test_complete_answer_keeps_original_source_ids_and_no_new_labels(self):
        item=fixture();before=copy.deepcopy(item)
        async def unused(*a,**kw):raise AssertionError('A direct linked statement should not need a model guess')
        with patch('app.meeting_qa.chat',unused):result=asyncio.run(answer(item,'Lan đã kết luận gì?','kaggle'))
        self.assertEqual(item,before);self.assertEqual(result['method'],'extractive_passage')
        self.assertIn('Người phụ trách cần yêu cầu đọc lại nội dung',result['points'][0]['text'])
        self.assertEqual({r['id'] for r in result['points'][0]['evidence']},{'F1','F2','F3','F4'})

    def test_intro_only_citation_is_repaired_with_following_action(self):
        calls=[]
        async def fake(messages,*a,**kw):
            calls.append(messages)
            if len(calls)==1:return response('Lan đề nghị từ nay, mỗi khi người soạn thảo gửi báo cáo.',['1','2'])
            self.assertIn('previous answer cited only an introduction',messages[0]['content'])
            return response('Lan đề nghị yêu cầu đọc lại nội dung trước khi gửi.',['3'])
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(fixture(),'Lan đã đề nghị gì và biện pháp đó có ý nghĩa gì?','kaggle'))
        self.assertEqual(len(calls),2);self.assertEqual(result['points'][0]['evidence'][0]['id'],'F3')

    def test_person_identification_can_cite_intro_only(self):
        calls=[]
        async def fake(*a,**kw):calls.append(1);return response('Lan.',['1'])
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(fixture(),'Ai đề nghị đọc lại nội dung?','kaggle'))
        self.assertEqual(len(calls),1);self.assertTrue(result['answerable'])

    def test_repeated_incomplete_statement_is_not_accepted(self):
        async def fake(*a,**kw):return response('Lan đề nghị từ nay, mỗi khi người soạn thảo gửi báo cáo.',['1','2'])
        with patch('app.meeting_qa.chat',fake),self.assertRaisesRegex(ValueError,'đọc đủ nội dung phát biểu'):
            asyncio.run(answer(fixture(),'Lan đề nghị gì và biện pháp đó có ý nghĩa gì?','kaggle'))

class NamedStatementBoundaryTests(unittest.TestCase):
    def test_embedded_intro_keeps_action_and_excludes_later_agreement(self):
        item=fixture()
        item['segments']=[
            dict(id='N1',start=79,end=84,speaker='1',text='Lan đề nghị Từ nay, mỗi khi người soạn thảo gửi báo cáo.'),
            dict(id='N2',start=86,end=91,speaker='1',text='Người phụ trách cần yêu cầu đọc lại nội dung trước khi gửi.'),
            dict(id='N3',start=92,end=94,speaker='1',text='Đọc lại một lần nữa đã.'),
            dict(id='N4',start=96,end=98,speaker='1',text='Tôi đồng ý tôi sẽ nhắc nhở thật cẩn thận.')]
        before=copy.deepcopy(item)
        async def unused(*a,**kw):raise AssertionError('No model needed for explicit named statement')
        with patch('app.meeting_qa.chat',unused):result=asyncio.run(answer(item,'Lan đã kết luận gì?','kaggle'))
        self.assertEqual(item,before);self.assertEqual(result['scope'],'retrieved_excerpts')
        self.assertNotIn('Tôi đồng ý',result['points'][0]['text'])
        self.assertIn('Người phụ trách cần yêu cầu',result['points'][0]['text'])
        self.assertEqual({r['id'] for r in result['points'][0]['evidence']},{'N1','N2','N3'})

    def test_unattributed_first_person_is_not_bound_to_named_actor(self):
        item=fixture();item['segments'][0]['text']='Tôi đề nghị'
        async def fake(messages,*a,**kw):
            self.assertIn('Tôi đề nghị',messages[-1]['content'])
            return '{"answerable":false,"points":[]}'
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(item,'Lan đã đề nghị gì?','kaggle'))
        self.assertFalse(result['answerable'])

if __name__=='__main__':unittest.main()
