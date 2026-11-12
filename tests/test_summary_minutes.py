import asyncio, json, unittest
from unittest.mock import patch
from app.demo_features import draft_summary, tidy_minutes


import itertools
_ids = itertools.count()


def recording(texts, speakers=('A', 'B')):
    segments = [dict(id=f'S{i}', start=i * 60.0, end=i * 60.0 + 50, speaker=speakers[i % len(speakers)], text=t)
                for i, t in enumerate(texts)]
    return dict(id=f'minutes-{next(_ids)}', revision=3, language='vi', segments=segments)


class MinutesTests(unittest.TestCase):
    def test_tidy_drops_farewells_merges_twins_and_caps(self):
        points = [dict(category='discussion', text='Lời tạm biệt của người tham dự họp', ids=['S1'])]
        points += [dict(category='decision', text='Áp dụng chính sách thưởng theo năng suất từ ngày mai', ids=['S1'])]
        points += [dict(category='decision', text='Áp dụng chính sách thưởng theo năng suất từ ngày mai.', ids=['S2'])]
        topics = ['ngân sách quảng cáo', 'tuyển dụng nhân sự', 'quy trình duyệt ý tưởng', 'hình ảnh tự chụp', 'thiết kế banner', 'du lịch cuối năm', 'hỗ trợ sức khỏe', 'doanh thu quý', 'làm việc từ xa']
        points += [dict(category='discussion', text=t, ids=['S3']) for t in topics]
        points += [dict(category='action', text='Thư ký soạn chính sách', ids=['S4'], owner=None, deadline=None)]
        result = tidy_minutes(points)
        self.assertEqual([p['category'] for p in result][:2], ['decision', 'action'])
        self.assertEqual(result[0]['ids'], ['S1', 'S2'])
        self.assertEqual(sum(p['category'] == 'discussion' for p in result), 5)
        self.assertFalse(any('tạm biệt' in p['text'] for p in result))
        self.assertNotIn('owner', result[1])

    def test_long_meeting_consolidates_notes_into_capped_minutes(self):
        item = recording(['Chúng ta bàn về chính sách thưởng của phòng marketing. ' * 60 for _ in range(3)])
        calls = []

        async def fake(messages, *args, **kwargs):
            calls.append(messages)
            if 'NOTES:' in messages[-1]['content']:
                return json.dumps(dict(overview='Cuộc họp bàn việc đổi chính sách thưởng của phòng marketing.',
                                       overview_notes=['1'], items=[
                                           dict(category='decision', text='Thưởng theo năng suất từ ngày mai.', notes=['1', '2', '3']),
                                           dict(category='action', text='Thư ký soạn văn bản chính sách mới.', notes=['2'], owner='không có'),
                                       ]))
            return json.dumps(dict(bullets=[dict(category='decision', text='Đổi sang thưởng theo năng suất.', ids=['1'])]))
        with patch('app.demo_features.chat', fake):
            result = asyncio.run(draft_summary(item, 'kaggle'))
        self.assertEqual(result['format'], 'minutes')
        self.assertEqual(len(calls), 4)
        self.assertNotIn('Chúng ta bàn', calls[-1][-1]['content'])
        self.assertEqual(result['overview'][0]['category'], 'topic')
        points = result['sections'][0]['bullets']
        self.assertEqual([p['category'] for p in points], ['decision', 'action'])
        self.assertEqual(points[0]['ids'], ['S0', 'S1', 'S2'])
        self.assertNotIn('owner', points[1])

    def test_failed_consolidation_falls_back_to_notes(self):
        item = recording(['Chúng ta bàn về chính sách thưởng của phòng marketing. ' * 60 for _ in range(3)])

        async def fake(messages, *args, **kwargs):
            if 'NOTES:' in messages[-1]['content'] or 'failed validation' in messages[-1]['content'] and 'NOTES:' in messages[1]['content']:
                return 'not json'
            return json.dumps(dict(bullets=[dict(category='discussion', text='Chính sách thưởng chưa công bằng với người làm nhiều.', ids=['1'])]))
        with patch('app.demo_features.chat', fake):
            result = asyncio.run(draft_summary(item, 'kaggle'))
        points = result['sections'][0]['bullets']
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]['ids'], ['S0', 'S1', 'S2'])

    def test_quiet_excerpt_may_return_no_points(self):
        item = recording(['Chúng ta bàn về chính sách thưởng của phòng marketing. ' * 60, 'Xin chào mọi người. ' * 200])

        async def fake(messages, *args, **kwargs):
            if 'NOTES:' in messages[-1]['content']:
                return json.dumps(dict(overview='Cuộc họp bàn về chính sách thưởng.', overview_notes=['1'],
                                       items=[dict(category='discussion', text='Chính sách thưởng hiện tại chưa công bằng.', notes=['1'])]))
            if 'Xin chào' in messages[-1]['content']:
                return json.dumps(dict(bullets=[]))
            return json.dumps(dict(bullets=[dict(category='discussion', text='Thưởng chưa công bằng.', ids=['1'])]))
        with patch('app.demo_features.chat', fake):
            result = asyncio.run(draft_summary(item, 'kaggle'))
        self.assertEqual(len(result['sections'][0]['bullets']), 1)

    def test_single_speaker_is_a_talk(self):
        item = recording(['Hôm nay tôi chia sẻ năm kỹ năng tổ chức cuộc họp hiệu quả cho các bạn.'], speakers=('A',))

        async def fake(messages, *args, **kwargs):
            return json.dumps(dict(bullets=[dict(category='topic', text='Bài nói chia sẻ kỹ năng tổ chức cuộc họp hiệu quả.', ids=['1']),
                                            dict(category='discussion', text='Cần chuẩn bị chương trình họp trước.', ids=['1'])]))
        with patch('app.demo_features.chat', fake):
            result = asyncio.run(draft_summary(item, 'kaggle'))
        self.assertEqual(result['kind'], 'talk')
        self.assertEqual(result['overview'][0]['text'], 'Bài nói chia sẻ kỹ năng tổ chức cuộc họp hiệu quả.')


if __name__ == '__main__':
    unittest.main()


class SalvageTests(unittest.TestCase):
    def test_one_bad_point_does_not_rerun_the_excerpt(self):
        item = recording(['Chúng ta bàn về chính sách thưởng của phòng marketing. ' * 60 for _ in range(3)])
        calls = []

        async def fake(messages, *args, **kwargs):
            calls.append(1)
            if 'NOTES:' in messages[-1]['content']:
                return json.dumps(dict(overview='Cuộc họp bàn về chính sách thưởng.', overview_notes=['1'],
                                       items=[dict(category='discussion', text='Chính sách thưởng chưa công bằng.', notes=['1'])]))
            return json.dumps(dict(bullets=[dict(category='discussion', text='Thưởng chưa công bằng.', ids=['1', '1', '99']),
                                            dict(category='decision', text='Speaker 2 đồng ý.', ids=['1']),
                                            dict(category='action', text='Soạn văn bản mới.', ids=['42'])]))
        with patch('app.demo_features.chat', fake):
            result = asyncio.run(draft_summary(item, 'kaggle'))
        self.assertEqual(len(calls), 4)
        self.assertEqual(result['timing']['retries'], [])
