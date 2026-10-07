import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import store, step_one
from app.demo_features import draft_summary, import_transcript

PAYLOAD = {'meeting': 'Colab example', 'utterances': [
    {'id': 'U0001', 'start': 0, 'end': 3, 'speaker': 'SPEAKER_02', 'text': 'We discussed the design.'},
    {'id': 'U0002', 'start': 3, 'end': 4, 'speaker': 'Unknown', 'text': 'Nothing is agreed.'}]}

class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.object(store, 'DATA', Path(self.tmp.name))
        self.p.start()
        self.client = TestClient(step_one.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.p.stop()
        self.tmp.cleanup()

    def imported(self):
        response = self.client.post('/api/import-transcript', files={'file': ('t.json', json.dumps(PAYLOAD), 'application/json')})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_import_edit_preserves_original_and_invalidates_summary(self):
        item = self.imported()
        self.assertFalse(item['has_audio'])
        self.assertEqual(item['speakers'], {'0': 'SPEAKER_02'})
        self.assertIsNone(item['segments'][1]['speaker'])
        saved = store.get(item['id'])
        saved['summary'] = {'revision': 1}
        store.save(saved)
        route = f"/api/recordings/{item['id']}/segments/U0002"
        body = dict(revision=1, speaker='0', text='Nothing has been agreed yet.')
        response = self.client.patch(route, json=body)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertIsNone(result['summary'])
        self.assertEqual(result['segments'][1]['original_text'], 'Nothing is agreed.')
        self.assertEqual(result['revision'], 2)
        self.assertEqual(self.client.patch(route, json=body).status_code, 409)
        self.assertIn('Nothing has been agreed yet.', self.client.get(f"/api/recordings/{item['id']}/export").text)

    def test_invalid_import_does_not_create_recording(self):
        for raw in ['[]', '{', json.dumps({'utterances': [dict(PAYLOAD['utterances'][0], end=float('nan'))]})]:
            r = self.client.post('/api/import-transcript', files={'file': ('t.json', raw)})
            self.assertEqual(r.status_code, 400)
        self.assertEqual(store.listing(), [])

    def test_summary_saved_and_exported(self):
        item = self.imported()
        async def fake(messages, provider):
            return json.dumps({'bullets': [{'text': 'Design was discussed.', 'ids': ['U0001']}]})
        with patch('app.demo_features.chat', fake):
            r = self.client.post(f"/api/recordings/{item['id']}/summary", json={'provider': 'ollama'})
            self.assertEqual(r.status_code, 202)
            for _ in range(100):
                result = self.client.get(f"/api/recordings/{item['id']}").json()
                if result.get('summary_status') != 'processing':
                    break
                time.sleep(.02)
        self.assertEqual(result['summary_status'], 'ready', result)
        self.assertFalse(result['summary']['verified'])
        self.assertIn('[U0001]', self.client.get(f"/api/recordings/{item['id']}/summary/export").text)

    def test_edit_during_summary_rejects_stale_result(self):
        item = self.imported()
        async def fake(messages, provider):
            await asyncio.sleep(.2)
            return '{"bullets":[{"text":"Design","ids":["U0001"]}]}'
        with patch('app.demo_features.chat', fake):
            self.client.post(f"/api/recordings/{item['id']}/summary", json={})
            self.client.patch(f"/api/recordings/{item['id']}/segments/U0001", json=dict(revision=1, text='Corrected design.', speaker='0'))
            time.sleep(.4)
            result = self.client.get(f"/api/recordings/{item['id']}").json()
        self.assertIsNone(result['summary'])
        self.assertEqual(result['summary_status'], 'error')

    def test_bad_citations_rejected(self):
        async def fake(messages, provider):
            return '{"bullets":[{"text":"Invented","ids":["U9999"]}]}'
        item = self.imported()
        with patch('app.demo_features.chat', fake), self.assertRaises(ValueError):
            asyncio.run(draft_summary(item, 'ollama'))

    def test_interrupted_summary_recoverable(self):
        item = self.imported()
        saved = store.get(item['id'])
        saved['summary_status'] = 'processing'
        store.save(saved)
        store.init()
        self.assertEqual(store.get(item['id'])['summary_status'], 'error')

if __name__ == '__main__':
    unittest.main()
