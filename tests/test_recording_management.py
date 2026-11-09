import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import store, step_one


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(store,'DATA',Path(self.temp.name));self.patch.start()
        self.client=TestClient(step_one.app).__enter__()
        self.item=store.create('Old name','upload','local')
        self.item.update(status='ready',segments=[{'text':'keep'}],revision=7,qa=[{'question':'keep'}])
        store.save(self.item)
        self.folder=store.DATA/self.item['id'];self.folder.mkdir()
        (self.folder/'audio.wav').write_bytes(b'fixture')
        self.route='/api/recordings/'+self.item['id']

    def tearDown(self):
        self.client.__exit__(None,None,None);self.patch.stop();self.temp.cleanup()

    def test_rename_preserves_content_and_revision(self):
        r=self.client.patch(self.route,json={'title':'  New name  '})
        self.assertEqual(r.status_code,200)
        item=store.get(self.item['id'])
        self.assertEqual(item['title'],'New name')
        self.assertEqual(item['revision'],7)
        self.assertEqual(item['qa'],self.item['qa'])
        self.assertEqual(self.client.patch(self.route,json={'title':'   '}).status_code,400)

    def test_delete_only_selected_recording(self):
        other=store.create('Other','upload','local')
        other_folder=store.DATA/other['id'];other_folder.mkdir()
        (other_folder/'audio.wav').write_bytes(b'keep')
        self.assertEqual(self.client.delete(self.route).status_code,200)
        self.assertFalse(self.folder.exists())
        self.assertIsNone(store.get(self.item['id']))
        self.assertTrue((other_folder/'audio.wav').exists())
        self.assertEqual(self.client.get(self.route).status_code,404)

    def test_active_meeting_protected(self):
        store.patch(self.item['id'],status='recording')
        self.assertEqual(self.client.delete(self.route).status_code,409)
        self.assertEqual(self.client.patch(self.route,json={'title':'New'}).status_code,409)
        self.assertTrue(self.folder.exists())

    def test_assistant_processing_protected(self):
        store.patch(self.item['id'],qa_status='processing')
        self.assertEqual(self.client.delete(self.route).status_code,409)
