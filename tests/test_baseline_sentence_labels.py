import unittest
from app.speech_turns import baseline_sentence_labels

class BaselineLabels(unittest.TestCase):
    def test_short_sentences_keep_two_baseline_identities(self):
        words=[dict(word='Hello.',start=0,end=.4),dict(word='Yes.',start=.5,end=.9)]
        tracks=[dict(start=0,end=.4,speaker='A'),dict(start=.5,end=.9,speaker='B')]
        result=baseline_sentence_labels(words,tracks)
        self.assertEqual([r['speaker'] for r in result],['A','B'])
        self.assertEqual([r['text'] for r in result],['Hello.','Yes.'])
    def test_sustained_change_splits_without_losing_words(self):
        words=[dict(word=w,start=i,end=i+.9) for i,w in enumerate(['Hello','there','Good','morning.'])]
        result=baseline_sentence_labels(words,[dict(start=0,end=2,speaker='A'),dict(start=2,end=4,speaker='B')])
        self.assertEqual([r['speaker'] for r in result],['A','B'])
        self.assertEqual([w['word'] for r in result for w in r['words']],[w['word'] for w in words])
    def test_no_evidence_stays_unknown(self):
        self.assertIsNone(baseline_sentence_labels([dict(word='Hi.',start=0,end=1)],[])[0]['speaker'])
    def test_boundary_jitter_does_not_fragment_sentence(self):
        words=[dict(word=w,start=i,end=i+.9) for i,w in enumerate(['Paul,',"you’re",'late','again.'])]
        result=baseline_sentence_labels(words,[dict(start=0,end=.2,speaker='B'),dict(start=.2,end=4,speaker='A')])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['speaker'],'A')

class MixedSentenceEdges(unittest.TestCase):
    def test_weak_interjection_does_not_veto_later_speaker_change(self):
        words=[dict(word=w,start=a,end=b) for w,a,b in [('Oh,',0,.3),('thank',.5,1),('you',1,1.5),('It',1.6,2.1),('looks',2.1,2.6),('late.',2.6,3)]]
        tracks=[dict(start=0,end=.3,speaker='A'),dict(start=.5,end=1.5,speaker='B'),dict(start=1.6,end=3,speaker='A')]
        result=baseline_sentence_labels(words,tracks)
        self.assertEqual([r['speaker'] for r in result],['B','A'])
        self.assertEqual(result[0]['text'],'Oh, thank you')
        self.assertEqual([w['word'] for r in result for w in r['words']],[w['word'] for w in words])
