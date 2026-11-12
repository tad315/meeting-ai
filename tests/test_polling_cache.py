"""Polling: unchanged recordings answer 304 without re-sending the transcript."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import live_translation, step_one, store


class PollingCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(store, 'DATA', Path(self.tmp.name))
        self.patch.start()
        store.init()
        item = store.create('Weekly sync', 'upload', 'community')
        item['segments'] = [dict(id='U0001', speaker='SPEAKER_00', start=0, end=1, text='Xin chào mọi người',
                                 words=[dict(text='Xin', start=0, end=.3)])]
        item['status'] = 'ready'
        store.save(item)
        self.id = item['id']
        self.client = TestClient(step_one.app)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_unchanged_recording_returns_304_and_change_returns_new_body(self):
        first = self.client.get(f'/api/recordings/{self.id}')
        self.assertEqual(first.status_code, 200)
        etag = first.headers['etag']
        self.assertEqual(first.json()['segments'][0]['text'], 'Xin chào mọi người')
        again = self.client.get(f'/api/recordings/{self.id}', headers={'If-None-Match': etag})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b'')
        store.patch(self.id, title='Renamed')
        changed = self.client.get(f'/api/recordings/{self.id}', headers={'If-None-Match': etag})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()['title'], 'Renamed')
        self.assertNotEqual(changed.headers['etag'], etag)

    def test_live_translation_drafts_change_the_version(self):
        etag = self.client.get(f'/api/recordings/{self.id}').headers['etag']
        with patch.object(live_translation, 'previews', return_value=[dict(id='U0001', text='Hello everyone')]):
            response = self.client.get(f'/api/recordings/{self.id}', headers={'If-None-Match': etag})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['live_translation_previews'][0]['text'], 'Hello everyone')

    def test_response_matches_previous_public_shape(self):
        data = self.client.get(f'/api/recordings/{self.id}').json()
        self.assertEqual(data, step_one.public(store.get(self.id)))
        self.assertEqual(self.client.get('/api/recordings/' + 'f' * 32).status_code, 404)

    def test_listing_omits_transcript_and_supports_304(self):
        rows = store.listing()
        self.assertEqual(len(rows), 1)
        self.assertNotIn('segments', rows[0])
        self.assertEqual(rows[0]['title'], 'Weekly sync')
        first = self.client.get('/api/recordings')
        self.assertEqual(first.json()[0]['segments'], None)
        again = self.client.get('/api/recordings', headers={'If-None-Match': first.headers['etag']})
        self.assertEqual(again.status_code, 304)

    def test_listing_without_sqlite_json_functions(self):
        real = store.connect
        class NoJson:
            def __init__(self, db): self.db = db
            def execute(self, sql, *args):
                if 'json_remove' in sql:
                    import sqlite3
                    raise sqlite3.OperationalError('no such function: json_remove')
                return self.db.execute(sql, *args)
        from contextlib import contextmanager
        @contextmanager
        def fake():
            with real() as db:
                yield NoJson(db)
        with patch.object(store, 'connect', fake):
            rows = store.listing()
        self.assertNotIn('segments', rows[0])


if __name__ == '__main__':
    unittest.main()
