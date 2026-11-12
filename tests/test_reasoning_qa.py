import asyncio
import copy
import json
import unittest
from unittest.mock import patch
from app.meeting_qa import answer, question_mode, repeats_outcome_as_cause


def fixture():
    texts=['Hoàng hoàn toàn không biết chấm câu.',
           'Tất cả là do cậu này chẳng bao giờ để ý đến dấu câu.',
           'Mỏi tay chỗ nào, cậu ta chấm chỗ ấy.',
           'Mỗi khi Hoàng định chấm câu, hãy yêu cầu em đọc lại nội dung câu văn.']
    return dict(revision=1,language='vi',live_sources='sources.wav',speakers={'1':'Âm thanh cuộc họp'},
                segments=[dict(id='U'+str(i+1),speaker='1',text=text,start=i*10,end=i*10+5) for i,text in enumerate(texts)])


def response(text,ids):
    return json.dumps(dict(answerable=True,points=[dict(text=text,evidence=[dict(id=i) for i in ids])]),ensure_ascii=False)


class GroundedReasoningTests(unittest.TestCase):
    def test_correct_cause_uses_one_call_and_cites_reason(self):
        item=fixture();calls=[]
        async def fake(messages,*a,**kw):
            calls.append(messages)
            self.assertIn('WHY/CAUSE',messages[0]['content'])
            return response('Hoàng không biết chấm câu vì không chú ý đến dấu câu.',['2'])
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(item,'Tại sao Hoàng không biết chấm câu?','kaggle'))
        self.assertEqual(len(calls),1);self.assertEqual(result['answer_mode'],'cause')
        self.assertEqual(result['points'][0]['evidence'][0]['id'],'U2')

    def test_circular_cause_triggers_targeted_repair(self):
        calls=[];item=fixture();before=copy.deepcopy(item)
        async def fake(messages,*a,**kw):
            calls.append(messages)
            if len(calls)==1:return response('Hoàng không biết chấm câu vì em hoàn toàn không biết chấm câu.',['1','2'])
            self.assertIn('previous answer repeated the problem as its cause',messages[0]['content'])
            return response('Hoàng không chú ý dấu câu mà chấm theo lúc mỏi tay.',['2','3'])
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(item,'Tại sao Hoàng không biết chấm câu?','kaggle'))
        self.assertEqual(len(calls),2);self.assertEqual(item,before)
        self.assertEqual({r['id'] for r in result['points'][0]['evidence']},{'U2','U3'})

    def test_twice_circular_answer_is_not_accepted_as_explanation(self):
        async def fake(*a,**kw):return response('Vì Hoàng không biết chấm câu.',['1'])
        with patch('app.meeting_qa.chat',fake),self.assertRaisesRegex(ValueError,'giải thích được nguyên nhân'):
            asyncio.run(answer(fixture(),'Tại sao Hoàng không biết chấm câu?','kaggle'))

    def test_inference_uses_supported_premises_not_past_ai_answers(self):
        item=fixture();item['qa']=[dict(revision=1,question='Hoàng bị làm sao?',points=[dict(text='INVENTED_OLD_ANSWER')])]
        async def fake(messages,*a,**kw):
            self.assertIn('INTERPRETATION',messages[0]['content'])
            self.assertIn('Label the conclusion',messages[0]['content'])
            self.assertNotIn('INVENTED_OLD_ANSWER',str(messages))
            return response('Có thể suy ra việc đọc lại giúp em chú ý nội dung trước khi đặt dấu câu.',['2','4'])
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(item,'Việc đọc lại giúp Hoàng tiến bộ như thế nào?','kaggle'))
        self.assertEqual(result['answer_mode'],'inference')
        self.assertEqual({r['id'] for r in result['points'][0]['evidence']},{'U2','U4'})

    def test_missing_fact_may_still_be_unanswerable(self):
        async def fake(messages,*a,**kw):return '{"answerable":false,"points":[]}'
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(fixture(),'Hoàng học lớp mấy?','kaggle'))
        self.assertFalse(result['answerable']);self.assertEqual(result['points'],[])

    def test_inference_does_not_bypass_citation_validation(self):
        calls=[]
        async def fake(messages,*a,**kw):
            calls.append(messages)
            return response('Có thể suy ra em cần đọc lại để chú ý nội dung hơn.',['NO_SUCH_SOURCE'] if len(calls)==1 else ['4'])
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(fixture(),'Có thể suy ra điều gì từ giải pháp đọc lại?','kaggle'))
        self.assertEqual(len(calls),2);self.assertEqual(result['points'][0]['evidence'][0]['id'],'U4')


class QuestionIntentTests(unittest.TestCase):
    def test_action_question_remains_factual(self):
        self.assertEqual(question_mode('Phải làm sao để giúp em Hoàng?'),'fact')
    def test_reason_and_mechanism_are_causal(self):
        for q in ['Tại sao Hoàng không biết chấm câu?','Why was the delivery late?','Vì sao đọc lại có thể giúp Hoàng?']:
            with self.subTest(q=q):self.assertEqual(question_mode(q),'cause')
    def test_inference_and_hypothetical_are_recognized(self):
        for q in ['Có thể rút ra bài học gì?','Nếu em tiếp tục chấm bừa thì điều gì sẽ xảy ra?',
                  'Việc đọc lại giúp Hoàng tiến bộ như thế nào?']:
            with self.subTest(q=q):self.assertEqual(question_mode(q),'inference')
    def test_proposed_strong_conclusions_use_evidence_sufficiency(self):
        self.assertEqual(question_mode('Có thể kết luận em lười học không?'),'claim_check')
        self.assertEqual(question_mode('Can we conclude that Hoàng is lazy?'),'claim_check')

    def test_conservative_circular_check_preserves_substantive_reasons(self):
        question='Tại sao Hoàng không biết chấm câu?'
        self.assertTrue(repeats_outcome_as_cause(question,'Vì em hoàn toàn không biết chấm câu.'))
        self.assertFalse(repeats_outcome_as_cause(question,'Vì Hoàng không để ý dấu câu, mỏi tay ở đâu thì chấm ở đó.'))
        self.assertFalse(repeats_outcome_as_cause(question,'Transcript không nêu đủ nguyên nhân.'))
        self.assertTrue(repeats_outcome_as_cause('Why was the delivery late?','The delivery was late because it was late.'))
        self.assertFalse(repeats_outcome_as_cause('What happened?','The delivery was late.'))

class ConclusionSufficiencyTests(unittest.TestCase):
    def test_personality_claim_not_licensed_by_punctuation_mistake(self):
        calls=[]
        async def fake(messages,*a,**kw):
            calls.append(messages)
            if len(calls)==1:
                return response('Có thể suy ra Hoàng lười học vì không biết chấm câu.',['1'])
            self.assertIn('specific mistake or skill gap alone cannot establish',messages[0]['content'])
            payload=json.loads(messages[-1]['content'])
            self.assertEqual(len(payload['transcript']),1)
            self.assertEqual(payload['transcript'][0]['text'],'Hoàng hoàn toàn không biết chấm câu.')
            return '{"established":false,"ids":["1"]}'
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(fixture(),'Có thể kết luận Hoàng lười học không?','kaggle'))
        self.assertEqual(len(calls),2);self.assertEqual(result['assessment'],'insufficient')
        self.assertIn('chưa đủ cơ sở',result['points'][0]['text'])
        self.assertNotIn('Hoàng lười học',result['points'][0]['text'])
        self.assertEqual(result['points'][0]['evidence'][0]['id'],'U1')

    def test_explicit_supported_conclusion_remains_answerable(self):
        calls=[]
        async def fake(messages,*a,**kw):
            calls.append(messages)
            if len(calls)==1:return response('Có. Transcript cho biết Hoàng không để ý dấu câu.',['2'])
            return '{"established":true,"ids":["1"]}'
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(fixture(),'Có thể kết luận Hoàng không chú ý dấu câu không?','kaggle'))
        self.assertTrue(result['answerable']);self.assertEqual(result['answer_mode'],'claim_check')
        self.assertTrue(result['points'][0]['text'].startswith('Có.'))

    def test_english_question_with_vietnamese_name_keeps_english_limit(self):
        async def fake(messages,*a,**kw):
            if 'Check ONLY whether' in messages[0]['content']:return '{"established":false,"ids":["1"]}'
            return response('Hoàng is lazy.',['1'])
        with patch('app.meeting_qa.chat',fake):result=asyncio.run(answer(fixture(),'Can we conclude that Hoàng is lazy?','kaggle'))
        self.assertTrue(result['points'][0]['text'].startswith('The cited statements do not establish'))

    def test_reasoning_schema_selects_premises_before_answer_without_extra_call(self):
        calls=[]
        async def fake(messages,*a,**kw):
            calls.append(messages)
            properties=kw['schema']['anyOf'][0]['properties']['points']['items']['properties']
            self.assertEqual(next(iter(properties)),'evidence')
            self.assertEqual(kw['max_tokens'],480)
            return response('Có thể suy ra việc đọc lại giúp em chú ý nội dung hơn.',['2','4'])
        with patch('app.meeting_qa.chat',fake):asyncio.run(answer(fixture(),'Việc đọc lại giúp em tiến bộ như thế nào?','kaggle'))
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()


class AnswerLanguageTests(unittest.TestCase):
    def test_vietnamese_question_with_english_terms_stays_vietnamese(self):
        from app.meeting_qa import answer_language
        for q in ['Cuộc họp quyết định gì về budget?', 'Ai phụ trách KPI marketing?', 'deadline project K2 khi nao',
                  'Hoàng nói gì về deadline?', 'cuoc hop quyet dinh gi']:
            self.assertEqual(answer_language(q, {'language': 'vi'}), 'vi', q)
        for q in ['What was decided?', 'Can we conclude that Hoàng is lazy?', 'What does Hoàng say about the deadline?']:
            self.assertEqual(answer_language(q, {'language': 'vi'}), 'en', q)

    def test_english_reply_to_vietnamese_question_is_retried_in_vietnamese(self):
        calls = []
        async def fake(messages, *a, **kw):
            calls.append(messages[0]['content'])
            if len(calls) == 1:
                return response('The meeting decided to change the bonus policy so that rewards follow productivity and results.', ['1'])
            return response('Cuộc họp quyết định đổi chính sách thưởng theo năng suất và kết quả công việc.', ['1'])
        with patch('app.meeting_qa.chat', fake):
            result = asyncio.run(answer(fixture(), 'Cuộc họp quyết định gì về bonus?', 'kaggle'))
        self.assertTrue(result['points'][0]['text'].startswith('Cuộc họp'))
        self.assertIn('not written in Vietnamese', calls[-1])


class SpeakerRoleTests(unittest.TestCase):
    def test_roles_come_from_how_people_are_addressed(self):
        from app.meeting_qa import speaker_roles
        rows = [('A', 'Tiếp đây, giám đốc sẽ phổ biến cụ thể với mọi người.'), ('B', 'Cảm ơn mọi người.'),
                ('B', 'Trưởng phòng hãy cho tôi biết cái lý do.'), ('C', 'Dạ thưa giám đốc, đối thủ rất mạnh.'),
                ('B', 'Thư ký em hãy đọc cho các bạn nghe.'), ('A', 'Vâng thưa mọi người.'),
                ('B', 'Trưởng phòng chị thấy chính sách này có thỏa đáng không?'), ('C', 'Chính sách này tạo động lực.'),
                ('D', 'Thưa giám đốc, em có ý kiến.')]
        segments = [dict(id=str(i), speaker=v, text=t) for i, (v, t) in enumerate(rows)]
        self.assertEqual(speaker_roles(segments), {'B': 'giám đốc', 'C': 'trưởng phòng'} | {'A': 'thư ký'})
