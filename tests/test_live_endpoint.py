import unittest
import numpy as np
from app.live_gpu import chunk_ready


class EndpointTests(unittest.TestCase):
    def test_continuous_audio_waits_until_limit(self):
        self.assertFalse(chunk_ready(np.full((48000, 2), 1500, dtype=np.int16), [True, True]))
        self.assertTrue(chunk_ready(np.full((64000, 2), 1500, dtype=np.int16), [True, True]))

    def test_pause_requires_all_enabled_sources_to_be_quiet(self):
        samples = np.full((48000, 2), 1500, dtype=np.int16)
        samples[-11000:, 0] = 0
        self.assertFalse(chunk_ready(samples, [True, True]))
        self.assertTrue(chunk_ready(samples, [True, False]))

    def test_short_hesitation_does_not_trigger(self):
        samples = np.full((48000, 2), 1500, dtype=np.int16)
        samples[-3000:] = 0
        self.assertFalse(chunk_ready(samples, [True, True]))
        self.assertFalse(chunk_ready(np.zeros((16000, 2), dtype=np.int16), [True, True]))
