import unittest
from app.transcript import assign_speakers, group_words


class TurnAlignmentTests(unittest.TestCase):
    def test_pause_inside_same_voice_keeps_sentence(self):
        words = [dict(word='What are you doing', start=0., end=1., speaker='0'),
                 dict(word='in the morning?', start=2.6, end=3.5, speaker='0')]
        turns = group_words(words)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]['text'], 'What are you doing in the morning?')

    def test_short_interjection_is_not_overwritten(self):
        words = [dict(word='Well',start=0.,end=1.,speaker='0'),
                 dict(word='yes',start=1.,end=1.2,speaker='1'),
                 dict(word='continue',start=1.3,end=2.,speaker='0')]
        self.assertEqual([s['speaker'] for s in group_words(words)],['0','1','0'])

    def test_fragmented_voice_uses_union_overlap(self):
        turns = [dict(start=0.,end=.4,speaker='A'),dict(start=.4,end=.8,speaker='A'),
                 dict(start=.1,end=.7,speaker='B')]
        result = assign_speakers([dict(word='test', start=0.,end=1.)],turns)
        self.assertEqual(result[0]['speaker'],'A')
        self.assertTrue(result[0]['ambiguous_speaker'])

    def test_equal_overlap_unassigned_and_no_duplicate_weight(self):
        turns = [dict(start=0.,end=1.,speaker='A'),dict(start=0.,end=1.,speaker='A'),
                 dict(start=0.,end=1.,speaker='B')]
        self.assertIsNone(assign_speakers([dict(word='test',start=0.,end=1.)],turns)[0]['speaker'])
