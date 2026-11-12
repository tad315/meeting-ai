import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('speaker_eval', Path(__file__).resolve().parents[1] / 'tools/speaker_eval.py')
speaker_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(speaker_eval)


def unit(a, b, speaker, sentence=0, text='w'):
    return dict(start=a, end=b, speaker=speaker, sentence=sentence, text=text)


class SpeakerEvalTests(unittest.TestCase):
    def test_names_do_not_matter_only_grouping(self):
        ref = [unit(0, 1, 'An', 0), unit(1, 2, 'Binh', 1)]
        hyp = [dict(start=0, end=1, speaker='SPEAKER_07'), dict(start=1, end=2, speaker='SPEAKER_02')]
        result = speaker_eval.score(ref, hyp)
        self.assertEqual(result['word_accuracy'], 1)
        self.assertEqual(result['sentence_accuracy'], 1)

    def test_merged_speakers_cost_the_smaller_one(self):
        ref = [unit(0, 3, 'An', 0), unit(3, 4, 'Binh', 1)]
        hyp = [dict(start=0, end=4, speaker='X')]
        result = speaker_eval.score(ref, hyp)
        self.assertAlmostEqual(result['word_accuracy'], .75)
        self.assertEqual((result['ref_speakers'], result['hyp_speakers']), (2, 1))
        self.assertEqual(result['errors'][0]['ref'], 'Binh')

    def test_unassigned_speech_is_reported(self):
        ref = [unit(0, 1, 'An', 0), unit(1, 2, 'An', 0)]
        hyp = [dict(start=0, end=1, speaker='X'), dict(start=1, end=2, speaker=None)]
        result = speaker_eval.score(ref, hyp)
        self.assertAlmostEqual(result['unassigned'], .5)

    def test_mapping_is_one_to_one(self):
        confusion = {'A': {'x': 5, 'y': 4}, 'B': {'x': 6, 'y': 0}}
        self.assertEqual(speaker_eval.best_mapping(confusion, ['A', 'B'], ['x', 'y']), {'x': 'B', 'y': 'A'})


if __name__ == '__main__':
    unittest.main()
