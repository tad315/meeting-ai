import hashlib
import io
import json
import zipfile
from pathlib import Path
import test_demo_features as demo
from app import store
from app.gpu_exchange import REVISION


PAYLOAD = demo.PAYLOAD


class GPUExchangeTests(__import__('unittest').TestCase):
    setUp = demo.DemoTests.setUp
    tearDown = demo.DemoTests.tearDown
    imported = demo.DemoTests.imported
    def prepared(self):
        public = self.imported()
        item = store.get(public['id'])
        path = Path(self.tmp.name) / item['id'] / 'audio.wav'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'audio fixture')
        item.update(audio=str(path),duration=4,summary={'revision':1})
        store.save(item)
        result = dict(PAYLOAD, schema='voxmeet-gpu-v1',recording_id=item['id'],revision=1,
                      audio_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                      provenance={'diarization_revision':REVISION,'diarization_model':'Community-1 baseline'})
        return item, result

    def test_package_and_apply_preserves_audio_and_backup(self):
        item, payload = self.prepared()
        prefix = f"/api/recordings/{item['id']}"
        r = self.client.get(prefix+'/gpu-package')
        self.assertEqual(r.status_code,200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
            self.assertEqual(json.loads(archive.read('job.json'))['audio_sha256'],payload['audio_sha256'])
            self.assertEqual(archive.read('audio.wav'),b'audio fixture')
        r = self.client.post(prefix+'/gpu-result',files={'file':('result.json',json.dumps(payload))})
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(r.json()['has_audio'])
        self.assertIsNone(r.json()['summary'])
        self.assertEqual(r.json()['revision'],2)
        self.assertEqual(len(list((Path(self.tmp.name)/item['id']).glob('transcript-before-gpu-*.json'))),1)
        # Replaying an outdated result must not overwrite edits/current data.
        self.assertEqual(self.client.post(prefix+'/gpu-result',files={'file':('r.json',json.dumps(payload))}).status_code,400)

    def test_wrong_audio_recording_revision_and_bounds_rejected(self):
        item,payload=self.prepared()
        route=f"/api/recordings/{item['id']}/gpu-result"
        for changes in ({'audio_sha256':'bad'},{'recording_id':'other'},{'revision':0},
                        {'utterances':[dict(PAYLOAD['utterances'][0],end=5)]}):
            r=self.client.post(route,files={'file':('r.json',json.dumps(dict(payload,**changes)))})
            self.assertEqual(r.status_code,400,r.text)
        self.assertEqual(store.get(item['id'])['revision'],1)

    def test_summary_prompt_keeps_speaker_context(self):
        import asyncio
        from unittest.mock import patch
        from app.demo_features import draft_summary
        item=self.imported()
        async def fake(messages,provider):
            self.assertIn('Speaker label 0:', messages[1]['content'])
            return '{"bullets":[{"text":"Design discussed","ids":["U0001"]}]}'
        with patch('app.demo_features.chat',fake):
            asyncio.run(draft_summary(item,'ollama'))
