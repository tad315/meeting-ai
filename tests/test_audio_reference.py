import unittest
import numpy as np
from app.speech_turns import acoustic_resegment
from app.live_word_turns import validate_tracks

class AudioReferenceTests(unittest.TestCase):
    def test_corrects_mislabeled_audio_inside_long_track_without_text(self):
        audio=np.ones(2400,dtype=np.float32)
        audio[600:1800]=-1;audio[1000:1200]=1
        tracks=[dict(start=0,end=6,speaker='A'),dict(start=6,end=18,speaker='B'),dict(start=18,end=24,speaker='A')]
        def embedding(clip):
            return np.array([np.mean(clip>0),np.mean(clip<0)],dtype=np.float32)
        corrected,diagnostic=acoustic_resegment(audio,tracks,embedding,sample_rate=100)
        self.assertTrue(any(t['start']<=11<t['end'] and t['speaker']=='A' for t in corrected))
        self.assertTrue(any(w.get('baseline')=='B' and w['speaker']=='A' and w['verified'] for w in diagnostic))
        self.assertEqual(corrected[0]['start'],0)
        self.assertEqual(corrected[-1]['end'],24)
        self.assertTrue(all(a['end']==b['start'] for a,b in zip(corrected,corrected[1:])))
    def test_single_voice_is_not_forced_into_second_person(self):
        tracks=[dict(start=0,end=6,speaker='A')]
        result,_=acoustic_resegment(np.ones(600),tracks,lambda clip:np.array([1.,0]),sample_rate=100)
        self.assertEqual({t['speaker'] for t in result},{'A'})
    def test_invalid_embeddings_keep_baseline(self):
        tracks=[dict(start=0,end=6,speaker='A')]
        result,_=acoustic_resegment(np.ones(600),tracks,lambda clip:np.array([float('nan'),0]),sample_rate=100)
        self.assertEqual(result,tracks)
    def test_live_reference_requires_similarity_margin_and_support(self):
        evidence=dict(policy='audio-reference-v1',accepted=True,similarity=.8,margin=.2,support_seconds=4)
        track=dict(start=0,end=1,speaker='speaker_1',evidence=evidence)
        self.assertEqual(validate_tracks(dict(tracks=[track]),1)[0]['speaker'],'speaker_1')
        for key,value in [('margin',.01),('similarity',.3),('support_seconds',1)]:
            bad=dict(track,evidence=dict(evidence,**{key:value}))
            self.assertIsNone(validate_tracks(dict(tracks=[bad]),1)[0]['speaker'])

class ReferenceDiagnostics(unittest.TestCase):
    def test_missing_reference_is_reported_not_silently_discarded(self):
        result,diag=acoustic_resegment(np.ones(600),[dict(start=0,end=6,speaker='A')],lambda clip:np.array([1.,0]),sample_rate=100)
        self.assertFalse(diag[0]['ready'])
        self.assertIn('A',diag[0]['references'])
        self.assertEqual(diag[0]['reason'],'insufficient_or_indistinguishable_references')
    def test_very_similar_references_do_not_trigger_reassignment(self):
        tracks=[dict(start=0,end=6,speaker='A'),dict(start=6,end=12,speaker='B')]
        result,diag=acoustic_resegment(np.ones(1200),tracks,lambda clip:np.array([1.,0]),sample_rate=100)
        self.assertFalse(diag[0]['ready'])
        self.assertEqual(result,tracks)
    def test_vectors_are_only_returned_when_explicitly_requested(self):
        args=(np.ones(600),[dict(start=0,end=6,speaker='A')],lambda clip:np.array([1.,0]))
        _,private=acoustic_resegment(*args,sample_rate=100)
        _,debug=acoustic_resegment(*args,sample_rate=100,include_vectors=True)
        self.assertNotIn('embedding',private[1])
        self.assertEqual(debug[1]['embedding'],[1.,0.])

class RapidDialogueReferences(unittest.TestCase):
    def test_short_turns_rescue_two_inconsistent_long_candidates(self):
        audio=np.ones(1800);audio[300:900]=-1
        tracks=[dict(start=0,end=3,speaker='B'),dict(start=3,end=6,speaker='B'),
                dict(start=6,end=7,speaker='B'),dict(start=8,end=9,speaker='B'),
                dict(start=10,end=17,speaker='A')]
        def embedding(clip):return np.array([np.mean(clip>0),np.mean(clip<0)])
        _,diagnostic=acoustic_resegment(audio,tracks,embedding,sample_rate=100)
        info=diagnostic[0]
        self.assertTrue(info['ready'])
        self.assertEqual(info['references']['B']['candidates'],4)
        self.assertEqual(info['references']['B']['retained'],3)
        self.assertTrue(info['references']['B']['reliable'])
    def test_overlapping_candidates_do_not_inflate_support(self):
        tracks=[dict(start=0,end=1.6,speaker='A'),dict(start=.2,end=1.8,speaker='A')]
        _,diagnostic=acoustic_resegment(np.ones(200),tracks,lambda clip:np.array([1.,0]),sample_rate=100)
        reference=diagnostic[0]['references']['A']
        self.assertAlmostEqual(reference['support_seconds'],1.8)
        self.assertFalse(reference['reliable'])

class TemporalRelativeEvidence(unittest.TestCase):
    def test_consistent_short_reply_with_lower_cosine_is_accepted(self):
        from app.speech_turns import verify_window_sequence,tracks_from_windows
        rows=[dict(start=i*.4,end=i*.4+1.6,interval=0,baseline='B',scores=score) for i,score in enumerate([[.60,.02],[.26,.34],[.09,.33],[.10,.33],[.14,.42],[.06,.39],[.22,.35],[.51,.11]])]
        result=verify_window_sequence(rows,['B','A'],True)
        self.assertTrue(result[2]['verified'])
        self.assertEqual(result[2]['speaker'],'A')
        self.assertEqual(result[1]['speaker'],'A')
        tracks=tracks_from_windows([dict(start=0,end=4.4,speaker='B')],result)
        self.assertTrue(any(t['speaker']=='A' for t in tracks))
        self.assertTrue(all(a['end']==b['start'] for a,b in zip(tracks,tracks[1:])))
    def test_single_weak_window_does_not_override_baseline(self):
        from app.speech_turns import verify_window_sequence
        row=dict(start=0,end=1.6,interval=0,baseline='B',scores=[.1,.34])
        self.assertFalse(verify_window_sequence([row],['B','A'],True)[0]['verified'])
    def test_missing_references_do_not_override_baseline(self):
        from app.speech_turns import verify_window_sequence
        row=dict(start=0,end=1.6,interval=0,baseline='B',scores=[.1,.9])
        self.assertEqual(verify_window_sequence([row],['B','A'],False)[0]['speaker'],'B')
