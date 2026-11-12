"""Reviewers can separate people the clustering merged: '+ New speaker'."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import step_one, store


class NewSpeakerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(store, 'DATA', Path(self.tmp.name))
        self.patch.start()
        store.init()
        item = store.create('Meeting', 'upload', 'community')
        words = [dict(text=w, word=w, start=i * .5, end=i * .5 + .4) for i, w in enumerate('Dạ em hiểu rồi sếp ạ'.split())]
        item.update(status='ready', revision=1, speakers={'0': 'SPEAKER_04', '1': 'SPEAKER_07'},
                    segments=[dict(id='U0001', speaker='1', start=0, end=3, text='Dạ em hiểu rồi sếp ạ', words=words)])
        store.save(item)
        self.id = item['id']
        self.client = TestClient(step_one.app)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_edit_can_assign_a_new_speaker(self):
        response = self.client.patch(f'/api/recordings/{self.id}/segments/U0001',
                                     json=dict(revision=1, text='Dạ em hiểu rồi sếp ạ', speaker='__new__'))
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['segments'][0]['speaker'], '2')
        self.assertEqual(data['speakers']['2'], 'SPEAKER_02')
        self.assertEqual(data['segments'][0]['original_speaker'], '1')

    def test_split_can_create_new_speaker_on_one_side(self):
        boundaries = self.client.get(f'/api/recordings/{self.id}/segments/U0001/boundaries').json()['boundaries']
        b = boundaries[1]
        response = self.client.post(f'/api/recordings/{self.id}/segments/U0001/split',
                                    json=dict(revision=1, offset=b['offset'], at=b['time'], left_speaker='1', right_speaker='__new__'))
        self.assertEqual(response.status_code, 200, response.text)
        speakers = [s['speaker'] for s in response.json()['segments']]
        self.assertEqual(speakers, ['1', '2'])

    def test_unknown_speaker_is_still_rejected(self):
        response = self.client.patch(f'/api/recordings/{self.id}/segments/U0001',
                                     json=dict(revision=1, text='x', speaker='99'))
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
