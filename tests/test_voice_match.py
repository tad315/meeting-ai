"""Correction propagation from voice prints."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from app import step_one, store, voice_match


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class VoiceMatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(store, 'DATA', Path(self.tmp.name))
        self.patch.start()
        voice_match._cache.clear()
        store.init()
        rng = np.random.default_rng(0)
        boss, staff, other = (unit(rng.normal(size=16)) for _ in range(3))
        # 6 boss sentences, 2 staff sentences merged into the boss label, 3 of someone else.
        plan = [('1', boss)] * 3 + [('1', staff)] + [('1', boss)] * 3 + [('1', staff)] + [('0', other)] * 3
        segments, spans, vectors = [], [], []
        for i, (label, voice) in enumerate(plan):
            start = i * 4.0
            segments.append(dict(id=f'U{i:04d}', start=start, end=start + 3, text=f'câu {i}', speaker=label))
            for k in range(3):
                spans.append([start + k, start + k + 1])
                vectors.append(unit(voice + rng.normal(scale=.15, size=16)))
        item = store.create('Meeting', 'upload', 'community')
        item.update(status='ready', revision=1, speakers={'0': 'SPEAKER_00', '1': 'SPEAKER_01'}, segments=segments)
        store.save(item)
        self.id = item['id']
        folder = store.DATA / self.id
        folder.mkdir(parents=True, exist_ok=True)
        prints = dict(dim=16, dtype='float16', spans=spans,
                      data=base64.b64encode(np.asarray(vectors).astype('<f2').tobytes()).decode())
        (folder / 'gpu-result-r1.json').write_text(json.dumps(dict(voice_prints=prints)))
        self.client = TestClient(step_one.app)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_marking_one_merged_sentence_suggests_the_other(self):
        r = self.client.patch(f'/api/recordings/{self.id}/segments/U0003',
                              json=dict(revision=1, text='câu 3', speaker='__new__'))
        self.assertEqual(r.status_code, 200, r.text)
        data = self.client.get(f'/api/recordings/{self.id}/segments/U0003/similar').json()
        self.assertTrue(data['available'])
        self.assertEqual([s['id'] for s in data['suggestions']], ['U0007'])
        applied = self.client.post(f'/api/recordings/{self.id}/speaker-batch',
                                   json=dict(revision=2, segment_ids=['U0007'], speaker=data['speaker']))
        self.assertEqual(applied.status_code, 200, applied.text)
        labels = {s['id']: s['speaker'] for s in applied.json()['segments']}
        self.assertEqual(labels['U0003'], labels['U0007'])
        self.assertEqual(labels['U0000'], '1')

    def test_correct_speaker_gets_no_suggestions(self):
        data = self.client.get(f'/api/recordings/{self.id}/segments/U0008/similar').json()
        self.assertEqual(data['suggestions'], [])

    def test_without_voice_prints_feature_is_unavailable(self):
        (store.DATA / self.id / 'gpu-result-r1.json').write_text('{}')
        voice_match._cache.clear()
        self.assertFalse(self.client.get(f'/api/recordings/{self.id}/segments/U0003/similar').json()['available'])

    def test_batch_rejects_stale_revision_and_unknown_speaker(self):
        self.assertEqual(self.client.post(f'/api/recordings/{self.id}/speaker-batch',
                         json=dict(revision=9, segment_ids=['U0007'], speaker='0')).status_code, 409)
        self.assertEqual(self.client.post(f'/api/recordings/{self.id}/speaker-batch',
                         json=dict(revision=1, segment_ids=['U0007'], speaker='42')).status_code, 400)


if __name__ == '__main__':
    unittest.main()
