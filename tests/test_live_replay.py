"""Live capture replay: early commits never leave a stale draft beside the
committed sentence, and every word is saved exactly once, in order."""
import asyncio
import io
import json
import random
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app import live_rolling, remote_tasks, store


def script():
    words, t = [], 7.7
    for line in ["Paul, you're late again.", "Sorry, I never get up before 8 .45.", "8 .45?", "That's late.",
                 "What time do you get up?", "5 a .m.?", "That's all right.", "What do you do in the morning?"]:
        for w in line.split():
            words.append((t, t + .25, ' ' + w)); t += .3
        t += .05
    return words


class LiveReplayTests(unittest.TestCase):
    def test_no_stale_drafts_and_no_lost_words(self):
        words = script()
        jobs = {}
        rng = random.Random(3)

        def enqueue(task, audio):
            reader = wave.open(io.BytesIO(audio))
            n = reader.getnframes()
            first = int(np.frombuffer(reader.readframes(1), dtype='<i2')[0])
            jobs[len(jobs)] = ((first - 2000) * 160 / 16000, n / 16000)
            return len(jobs) - 1

        async def wait(job, timeout=60, poll_interval=.1):
            await asyncio.sleep(.02)
            start, duration = jobs[job]
            jitter = rng.uniform(-.1, .1)
            return dict(segments=[dict(start=max(0, a - start + jitter), end=max(0, b - start + jitter), text=w)
                                  for a, b, w in words if a >= start - .01 and b <= start + duration],
                        asr_model='turbo', inference_seconds=.01, quality=[])

        class Socket:
            def __init__(self):
                self.queue, self.view, self.issues = asyncio.Queue(), {}, []

            async def send_json(self, message):
                if message.get('type') != 'update':
                    return
                for rid in message.get('remove_turns', []):
                    self.view.pop(rid, None)
                for turn in message.get('turns', []):
                    self.view[turn['id']] = turn
                drafts = [t for t in self.view.values() if not t.get('committed')]
                done = [t for t in self.view.values() if t.get('committed')]
                norm = lambda text: ' '.join(text.lower().replace(',', ' ').split())
                for d in drafts:
                    for c in done:
                        # The bug: a committed sentence still shown inside a draft.
                        if norm(c['text']) in norm(d['text']):
                            self.issues.append((d['text'], c['text']))

            async def receive(self):
                return await self.queue.get()

        async def run(socket):
            async def feed():
                for i in range(230):
                    signal = (2000 + (np.arange(i * 1600, (i + 1) * 1600) // 160)).astype('<i2')
                    await socket.queue.put(dict(type='websocket.receive',
                                                bytes=np.stack([signal * 0, signal], 1).astype('<i2').tobytes()))
                    await asyncio.sleep(.005)
                await socket.queue.put(dict(type='websocket.receive', text=json.dumps(dict(type='stop'))))
            asyncio.get_running_loop().create_task(feed())
            await live_rolling.capture(socket, dict(sources=[False, True], language='en'))

        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'DATA', Path(tmp)), \
                patch.object(remote_tasks, 'require_worker', lambda feature: None), \
                patch.object(remote_tasks, 'enqueue', enqueue), patch.object(remote_tasks, 'wait', wait), \
                patch.object(remote_tasks, 'timing', lambda job: dict(created=None, dispatched=None, received=None)):
            store.init()
            socket = Socket()
            asyncio.run(run(socket))
            item = store.get(store.listing()[0]['id'])
        self.assertEqual(socket.issues, [])
        saved = ' '.join(s['text'] for s in item['segments']).split()
        self.assertEqual(saved, [w.strip() for a, b, w in words])
        self.assertGreater(len(item['segments']), 5)  # committed sentence by sentence


if __name__ == '__main__':
    unittest.main()
