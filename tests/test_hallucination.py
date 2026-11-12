import ast
import json
import unittest
from pathlib import Path

import numpy as np

from app import hallucination as h

ROOT = Path(__file__).resolve().parents[1]


def words(text, start=0.0, step=.3):
    return [dict(word=w, start=start + i * step, end=start + i * step + .25) for i, w in enumerate(text.split())]


class HallucinationTests(unittest.TestCase):
    def test_youtube_outro_is_removed_but_neighbours_kept(self):
        ws = words('dời lại đến cuối năm Hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn Các phần thưởng')
        kept, removed = h.clean_words(ws)
        self.assertEqual(' '.join(w['word'] for w in kept), 'dời lại đến cuối năm Các phần thưởng')
        self.assertIn('Ghiền Mì Gõ', removed[0]['text'])

    def test_ordinary_meeting_sentences_are_never_touched(self):
        for text in ('Mọi người nhớ đăng ký tham gia khóa đào tạo nhé',
                     'Cảm ơn các bạn đã tham gia cuộc họp hôm nay',
                     'Em đã đăng ký kênh phân phối mới cho dự án',
                     'Thanks for coming everyone', 'We should subscribe to the new plan'):
            self.assertEqual(h.find_spans(words(text)), [], text)

    def test_english_outro(self):
        kept, removed = h.clean_words(words('see you next week Thanks for watching!'))
        self.assertEqual([w['word'] for w in kept], ['see', 'you', 'next', 'week'])

    def test_utterance_level_cleaning(self):
        utterances = [dict(text='Hãy subscribe cho kênh Ghiền Mì Gõ để không bỏ lỡ những video hấp dẫn', start=1, end=5),
                      dict(text='Xin chào', start=6, end=7)]
        kept, removed = h.clean_utterances(utterances)
        self.assertEqual([u['text'] for u in kept], ['Xin chào'])

    def test_worker_re_recognizes_the_invented_span(self):
        source = ''.join(json.loads((ROOT / 'colab/community_worker.ipynb').read_text(encoding='utf-8'))['cells'][-1]['source'])
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'recover_hallucinations')
        calls = []
        def recognize(clip, language, live):
            calls.append(len(clip) / 16000)
            return dict(words=[dict(word=' Theo', start=.5, end=.8), dict(word=' kế', start=.8, end=1.0), dict(word=' hoạch', start=1.0, end=1.3)])
        engine = {}
        exec((ROOT / 'app/hallucination.py').read_text(encoding='utf-8'), engine)
        scope = dict(recognize=recognize, HALLUCINATION_ENGINE=engine)
        exec(compile(ast.Module([node], []), '<recover>', 'exec'), scope)
        ws = words('cuối năm', 0) + words('Hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn', 10) + words('Các phần', 30)
        out, report = scope['recover_hallucinations'](np.zeros(40 * 16000, dtype=np.float32), 'vi', ws)
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(calls[0], 30 - .55, places=1)  # previous real word end -> next real word start
        self.assertEqual([w['word'].strip() for w in out], ['cuối', 'năm', 'Theo', 'kế', 'hoạch', 'Các', 'phần'])
        self.assertEqual(report[0]['recovered'], 'Theo kế hoạch')
        self.assertTrue(all(a['start'] <= b['start'] for a, b in zip(out, out[1:])))


if __name__ == '__main__':
    unittest.main()


class LiveCaptionTests(unittest.TestCase):
    def test_live_snapshot_drops_youtube_outro(self):
        from app.live_rolling import snapshot_turn
        result = dict(segments=[dict(start=0.0, end=1.5, text='Tắt được không?'),
                                dict(start=2.0, end=5.0, text='Hãy subscribe cho kênh La La School Để không bỏ lỡ những video hấp dẫn')])
        turn = snapshot_turn(result, 0, 10.0, 6.0, 'x')
        self.assertEqual(turn['text'], 'Tắt được không?')
        self.assertEqual([w['text'] for w in turn['words']], ['Tắt được không?'])

    def test_only_outro_gives_no_turn(self):
        from app.live_rolling import snapshot_turn
        result = dict(segments=[dict(start=0.0, end=3.0, text='Hãy subscribe cho kênh Ghiền Mì Gõ để không bỏ lỡ những video hấp dẫn nhé')])
        self.assertIsNone(snapshot_turn(result, 0, 0.0, 4.0, 'x'))
