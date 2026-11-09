import asyncio
import io
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import store, step_one
from app.providers import ffmpeg_path, normalize
from app.transcript import assign_speakers, group_words


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 16000)
    return output.getvalue()


class TranscriptTests(unittest.TestCase):
    def test_speaker_change_and_unassigned_speech(self):
        words = [dict(word='Hello', start=0, end=.4), dict(word='there.', start=.4, end=.9),
                 dict(word='Thanks.', start=1.2, end=1.7), dict(word='Unknown.', start=4, end=5)]
        turns = [dict(start=0, end=1, speaker='0'), dict(start=1, end=2, speaker='1')]
        result = group_words(assign_speakers(words, turns))
        self.assertEqual([s['speaker'] for s in result], ['0', '1', None])
        self.assertEqual(result[0]['text'], 'Hello there.')
        self.assertEqual((result[0]['start'], result[0]['end']), (0, .9))

    def test_boundary_word_uses_longest_overlap(self):
        words = [dict(word='boundary', start=.8, end=1.5)]
        turns = [dict(start=0, end=1, speaker='0'), dict(start=1, end=2, speaker='1')]
        self.assertEqual(assign_speakers(words, turns)[0]['speaker'], '1')

    def test_silence_produces_no_fabricated_turns(self):
        self.assertEqual(group_words([]), [])

    def test_point_timestamp_uses_only_unambiguous_active_speaker(self):
        word = dict(word='and', start=1, end=1)
        turns = [dict(start=.5, end=2, speaker='0')]
        self.assertEqual(assign_speakers([word], turns)[0]['speaker'], '0')
        turns.append(dict(start=.9, end=1.5, speaker='1'))
        self.assertIsNone(assign_speakers([word], turns)[0]['speaker'])


class APITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patch_data = patch.object(store, 'DATA', Path(self.directory.name))
        self.patch_data.start()
        self.client = TestClient(step_one.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.patch_data.stop()
        self.directory.cleanup()

    def wait_for(self, mid):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            item = self.client.get('/api/recordings/' + mid).json()
            if item['status'] not in step_one.ACTIVE:
                return item
            time.sleep(.02)
        self.fail('Background processing did not finish.')

    def upload(self):
        response = self.client.post('/api/upload', files={'file': ('conversation.wav', wav_bytes(), 'audio/wav')})
        self.assertEqual(response.status_code, 202, response.text)
        return self.wait_for(response.json()['id'])

    def test_only_step_one_routes_are_registered(self):
        routes = {getattr(route,'path',None) for route in step_one.app.routes}
        self.assertNotIn('/api/live/{mid}', routes)
        self.assertNotIn('/api/meetings/{mid}/summary', routes)
        self.assertNotIn('/api/meetings/{mid}/chat', routes)
        self.assertEqual(self.client.get('/api/health').json()['milestone'], 1)
        self.assertIn('id="overview-count"', self.client.get('/').text)
        recordings = self.client.get('/recordings')
        self.assertEqual(recordings.status_code, 200)
        self.assertIn('id="translation-panel"', recordings.text)

    @unittest.skipUnless(ffmpeg_path(), 'FFmpeg is needed for real decoding tests')
    def test_upload_playback_and_api_result_persistence(self):
        item = self.upload()
        self.assertEqual(item['status'], 'uploaded', item)
        self.assertAlmostEqual(item['duration'], 1, places=2)
        self.assertTrue(item['has_audio'])
        self.assertNotIn('source_path', item)
        audio = self.client.get(f"/api/recordings/{item['id']}/audio", headers={'Range': 'bytes=0-31'})
        self.assertEqual(audio.status_code, 206)
        self.assertEqual(audio.content[:4], b'RIFF')

        async def fake_transcribe(path, provider, **kwargs):
            return [dict(id='s1', start=0, end=.4, speaker='0', text='Hello team.'),
                    dict(id='s2', start=.5, end=.9, speaker='1', text='Good morning.')]
        with patch.object(step_one, 'check_provider'), patch.object(step_one, 'transcribe', fake_transcribe):
            response = self.client.post(f"/api/recordings/{item['id']}/transcribe", json={'provider': 'local'})
            self.assertEqual(response.status_code, 202)
            result = self.wait_for(item['id'])
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['speakers'], {'0': 'Speaker 1', '1': 'Speaker 2'})
        self.assertEqual(result['segments'][0]['original_text'], 'Hello team.')
        exported = self.client.get(f"/api/recordings/{item['id']}/export")
        self.assertIn('Speaker 2: Good morning.', exported.text)
        self.assertIn('attachment', exported.headers['content-disposition'])

    @unittest.skipUnless(ffmpeg_path(), 'FFmpeg is needed')
    def test_missing_credentials_keeps_uploaded_audio(self):
        item = self.upload()
        with patch.object(step_one, 'check_provider', side_effect=ValueError('HF_TOKEN required')):
            response = self.client.post(f"/api/recordings/{item['id']}/transcribe", json={'provider': 'local'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/recordings/' + item['id']).json()['status'], 'uploaded')

    @unittest.skipUnless(ffmpeg_path(), 'FFmpeg is needed')
    def test_provider_failure_is_retryable_and_double_start_rejected(self):
        item = self.upload()
        async def fail(path, provider, **kwargs):
            await asyncio.sleep(.2)
            raise ValueError('Provider unavailable')
        with patch.object(step_one, 'check_provider'), patch.object(step_one, 'transcribe', fail):
            self.client.post(f"/api/recordings/{item['id']}/transcribe", json={'provider': 'local'})
            duplicate = self.client.post(f"/api/recordings/{item['id']}/transcribe", json={'provider': 'local'})
            self.assertEqual(duplicate.status_code, 409)
            result = self.wait_for(item['id'])
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['has_audio'])
        self.assertEqual(result['error'], 'Provider unavailable')

    def test_bad_extension_size_and_empty_upload(self):
        with patch.object(step_one, 'capabilities', return_value={'ffmpeg_available': True}):
            self.assertEqual(self.client.post('/api/upload', files={'file': ('bad.txt', b'text')}).status_code, 400)
            self.assertEqual(self.client.post('/api/upload', files={'file': ('empty.wav', b'')}).status_code, 400)
            with patch.object(step_one, 'MAX_BYTES', 10):
                self.assertEqual(self.client.post('/api/upload', files={'file': ('large.wav', b'a'*11)}).status_code, 400)

    @unittest.skipUnless(ffmpeg_path(), 'FFmpeg is needed')
    def test_corrupt_audio_reports_decode_error(self):
        response = self.client.post('/api/upload', files={'file': ('broken.wav', b'not a wave')})
        result = self.wait_for(response.json()['id'])
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['has_audio'])

    def test_foreign_origin_rejected(self):
        response = self.client.post('/api/upload', headers={'Origin': 'https://unrelated.example'},
                                    files={'file': ('test.wav', wav_bytes())})
        self.assertEqual(response.status_code, 403)

    @unittest.skipUnless(ffmpeg_path(), 'FFmpeg is needed')
    def test_cancel_preserves_audio_and_previous_transcript(self):
        item = self.upload()
        saved = store.get(item['id'])
        saved['segments'] = [dict(id='old', start=0, end=.5, speaker='0', text='Earlier words.')]
        store.save(saved)

        async def cancellable(path, provider, cancel=None, **kwargs):
            while not cancel.is_set():
                await asyncio.sleep(.01)
            raise step_one.ProcessingCancelled()

        with patch.object(step_one, 'check_provider'), patch.object(step_one, 'transcribe', cancellable):
            self.client.post(f"/api/recordings/{item['id']}/transcribe", json={'provider': 'local'})
            response = self.client.post(f"/api/recordings/{item['id']}/cancel")
            self.assertEqual(response.status_code, 202)
            result = self.wait_for(item['id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(result['has_audio'])
        self.assertEqual(result['segments'][0]['text'], 'Earlier words.')
        self.assertEqual(self.client.post(f"/api/recordings/{item['id']}/cancel").status_code, 409)

    def test_speaker_count_is_bounded(self):
        for count in (0, 13):
            response = self.client.post('/api/recordings/invalid/transcribe', json={'num_speakers': count})
            self.assertEqual(response.status_code, 422)

    def test_restart_marks_inflight_work_interrupted(self):
        item = store.create('In-flight', 'upload', 'local')
        item['status'] = 'transcribing'
        store.save(item)
        store.init()
        self.assertEqual(store.get(item['id'])['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
