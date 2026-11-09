import ast
import copy
import json
import unittest
from pathlib import Path
import numpy as np
from app.live_word_turns import label_turn,validate_tracks


class SpeakerGuardTests(unittest.TestCase):
    def turn(self):
        return dict(id='keep-id',text='What do you mean?',start=0,end=2,speaker='1',source_channel=1,
            words=[dict(word='What',start=0,end=.1),dict(word='do',start=.1,end=.6),
                dict(word='you',start=.6,end=1.2),dict(word='mean?',start=1.2,end=2)])
    def test_unverified_cluster_labels_are_not_applied(self):
        tracks=validate_tracks({'tracks':[dict(start=0,end=2,speaker='speaker_1')]},3)
        self.assertIsNone(tracks[0]['speaker'])
        self.assertEqual(label_turn(self.turn(),tracks,0,3,1),self.turn())
    def test_unassigned_word_never_becomes_isolated_display_fragment(self):
        original=self.turn()
        result=label_turn(original,[dict(start=.1,end=2,speaker='speaker_1')],0,3,1)
        self.assertEqual(result['speaker'],'1_speaker_1')
        self.assertNotIn('speaker_spans',result)
        self.assertEqual(result['text'],original['text'])
    def test_uncertain_update_revokes_old_label_without_changing_sentence(self):
        original=self.turn()
        wrong=dict(original,speaker='1_speaker_1',speaker_status='provisional_acoustic',
            speaker_spans=[dict(start=0,end=2,text=original['text'],speaker='1_speaker_1')])
        result=label_turn(wrong,[dict(start=0,end=2,speaker=None)],0,3,1)
        self.assertEqual(result['speaker'],'1')
        self.assertNotIn('speaker_spans',result)
        self.assertEqual(result['id'],original['id'])
        self.assertEqual(result['text'],original['text'])
    def test_one_word_speaker_fluctuation_does_not_split_sentence(self):
        result=label_turn(self.turn(),[dict(start=0,end=.1,speaker='speaker_2'),dict(start=.1,end=2,speaker='speaker_1')],0,3,1)
        self.assertNotIn('speaker_spans',result)
        self.assertEqual(result['speaker'],'1')
    def test_identity_memory_does_not_double_count_overlapping_audio_or_merge_ambiguous_voices(self):
        from app.speech_turns import SpeakerMemory
        memory=SpeakerMemory()
        first=memory.observe(np.array([1.,0.]),0,2)
        self.assertIsNone(first['speaker'])
        again=memory.observe(np.array([1.,0.]),.02,2.02)
        self.assertIs(first,again);self.assertEqual(first['seconds'],2)
        memory.observe(np.array([1.,0.]),3,5)
        second=memory.observe(np.array([0.,1.]),6,10)
        self.assertNotEqual(first['speaker'],second['speaker'])
        self.assertIsNone(memory.observe(np.array([1.,1.]),11,14))

if __name__=='__main__':unittest.main()
