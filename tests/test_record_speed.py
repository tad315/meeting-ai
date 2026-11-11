"""Record speed path: lossless FLAC transfer and streamed ASR progress."""
import ast
import hashlib
import io
import json
import math
import struct
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app import gpu_exchange, providers

ROOT = Path(__file__).resolve().parents[1]


def write_wav(path, seconds=3.0):
    rate = 16000
    frames = bytearray()
    for i in range(int(seconds * rate)):
        value = 0.4 * math.sin(2 * math.pi * 220 * i / rate) + 0.1 * math.sin(2 * math.pi * 1375 * i / rate)
        frames += struct.pack('<h', int(value * 32767))
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(rate)
        audio.writeframes(bytes(frames))


class FlacPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.wav = self.folder / 'audio.wav'
        write_wav(self.wav)
        self.item = dict(id='a' * 32, revision=3, title='t', audio=str(self.wav), duration=3.0, language='en')

    def tearDown(self):
        self.tmp.cleanup()

    def test_flac_package_is_lossless_and_smaller(self):
        if not providers.ffmpeg_path():
            self.skipTest('FFmpeg not available')
        import soundfile as sf
        target = gpu_exchange.make_package(self.item, self.folder / 'job.zip', audio_format='flac')
        with zipfile.ZipFile(target) as z:
            self.assertEqual(set(z.namelist()), {'audio.flac', 'job.json'})
            job = json.loads(z.read('job.json'))
            flac = z.read('audio.flac')
        self.assertLess(len(flac), self.wav.stat().st_size)
        # Same check as the worker: decoded PCM matches the server WAV exactly.
        pcm, rate = sf.read(io.BytesIO(flac), dtype='int16', always_2d=True)
        self.assertEqual(rate, 16000)
        self.assertEqual(hashlib.sha256(pcm[:, 0].astype('<i2').tobytes()).hexdigest(), job['pcm_sha256'])
        self.assertEqual(job['audio_sha256'], gpu_exchange.audio_hash(self.wav))
        reference, _ = sf.read(str(self.wav), dtype='float32')
        np.testing.assert_array_equal(pcm[:, 0].astype(np.float32) / 32768.0, reference)

    def test_falls_back_to_wav_without_ffmpeg(self):
        with patch.object(providers, 'ffmpeg_path', return_value=''):
            target = gpu_exchange.make_package(self.item, self.folder / 'job.zip', audio_format='flac')
        with zipfile.ZipFile(target) as z:
            self.assertEqual(set(z.namelist()), {'audio.wav', 'job.json'})
            self.assertNotIn('pcm_sha256', json.loads(z.read('job.json')))

    def test_default_package_is_unchanged_wav(self):
        target = gpu_exchange.make_package(self.item, self.folder / 'job.zip')
        with zipfile.ZipFile(target) as z:
            self.assertEqual(z.read('audio.wav'), self.wav.read_bytes())

    def test_queue_uses_flac_only_for_capable_worker(self):
        from app import community_queue
        calls = []
        worker = ('b' * 32, self.folder, dict(expires=0, token_hash='x'))
        for features, expected in ((['multilingual'], 'wav'), (['multilingual', 'flac_audio'], 'flac')):
            calls.clear()
            with patch.object(community_queue, 'current', return_value=worker), \
                    patch.object(community_queue, 'worker_status', return_value=dict(available_for_jobs=True, features=features)), \
                    patch.object(community_queue, 'make_package', side_effect=lambda item, target, audio_format: calls.append(audio_format)), \
                    patch.object(community_queue, 'publish_pending'), \
                    patch.object(community_queue, 'root', return_value=self.folder):
                community_queue.enqueue(dict(self.item, id=self.item['id']))
            self.assertEqual(calls, [expected])

    def test_worker_advertises_and_bridge_accepts_flac(self):
        source = (ROOT / 'app/gpu_bridge.py').read_text(encoding='utf-8')
        self.assertIn("'flac_audio'", source)
        notebook = json.loads((ROOT / 'colab/community_worker.ipynb').read_text(encoding='utf-8'))
        self.assertIn("'flac_audio'", ''.join(notebook['cells'][-1]['source']))


class StreamedProgressTests(unittest.TestCase):
    def load_recognize(self, lines, folder):
        notebook = json.loads((ROOT / 'colab/community_worker.ipynb').read_text(encoding='utf-8'))
        tree = ast.parse(''.join(notebook['cells'][-1]['source']))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'recognize')
        released = []

        class Stdout:
            def __init__(self): self.lines = list(lines)
            def readline(self): return self.lines.pop(0) if self.lines else ''

        class Process:
            stdout = Stdout()
            stdin = io.StringIO()
            def poll(self): return None

        class Torch:
            class cuda:
                @staticmethod
                def device_count(): return 2

        scope = dict(asr_process=Process(), torch=Torch, np=np, json=json,
                     Path=lambda *_: folder, release_llm=lambda: None,
                     release_asr=lambda: released.append(True))
        exec(compile(ast.Module([node], []), '<recognize>', 'exec'), scope)
        return scope['recognize'], released

    def test_progress_events_are_forwarded_then_result_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines = [json.dumps(dict(event='progress', fraction=.25)) + '\n',
                     json.dumps(dict(event='progress', fraction=.75)) + '\n',
                     json.dumps(dict(words=[1], asr_model='turbo')) + '\n']
            recognize, _ = self.load_recognize(lines, Path(tmp))
            seen = []
            result = recognize(np.zeros(16000, dtype=np.float32), 'en', live=False, on_progress=seen.append)
        self.assertEqual(seen, [.25, .75])
        self.assertEqual(result['asr_model'], 'turbo')

    def test_crashed_service_fails_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            recognize, released = self.load_recognize([json.dumps(dict(event='progress', fraction=.1)) + '\n'], Path(tmp))
            with self.assertRaisesRegex(RuntimeError, 'ASR failed'):
                recognize(np.zeros(16000, dtype=np.float32), 'en', live=False)
        self.assertEqual(released, [True])


if __name__ == '__main__':
    unittest.main()


class RelayHeartbeatTests(unittest.TestCase):
    def test_unknown_features_are_ignored_not_rejected(self):
        import hashlib as h, os, tempfile as t
        from fastapi.testclient import TestClient
        from app import gpu_bridge
        with t.TemporaryDirectory() as tmp, patch.object(gpu_bridge, 'ROOT', Path(tmp)):
            sid, token = 'c' * 32, 'secret'
            folder = Path(tmp) / sid; folder.mkdir()
            (folder / 'session.json').write_text(json.dumps(dict(kind='worker', expires=4e9, token_hash=h.sha256(token.encode()).hexdigest())))
            client = TestClient(gpu_bridge.bridge)
            body = dict(features=['multilingual', 'flac_audio', 'from_the_future'], runtime_id='d' * 32, startup_state='ready',
                        assistant_state='ready', gpu_count=2, worker_build='x')
            response = client.post(f'/worker/{sid}/heartbeat', headers={'Authorization': 'Bearer ' + token}, json=body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads((folder / 'features.json').read_text()), ['multilingual', 'flac_audio'])
            self.assertTrue((folder / 'heartbeat').exists())
            self.assertEqual(client.get('/bridge-health').json()['code'], gpu_bridge.CODE_VERSION)


class AtomicWriteTests(unittest.TestCase):
    def test_retries_when_windows_briefly_locks_target(self):
        from app import gpu_bridge
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'runtime.json'
            real = Path.replace
            failures = [PermissionError(5, 'Access is denied')] * 2
            def flaky(self, other):
                if failures: raise failures.pop()
                return real(self, other)
            with patch.object(Path, 'replace', flaky):
                gpu_bridge.atomic_write(target, '{"ok": 1}')
            self.assertEqual(json.loads(target.read_text()), {'ok': 1})
            self.assertEqual(list(Path(tmp).glob('*.part')), [])
