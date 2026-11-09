import ast
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np


class TurboWorkerTests(unittest.TestCase):
    def test_live_and_recorded_jobs_share_warmed_turbo_and_report_model(self):
        notebook = json.loads((Path(__file__).resolve().parents[1]/'colab/community_worker.ipynb').read_text(encoding='utf-8'))
        source = ''.join(notebook['cells'][-1]['source'])
        service = next(n.value.value for n in ast.parse(source).body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'ASR_SERVICE' for t in n.targets))
        loads, beams = [], []
        class Model:
            def __init__(self, name, **kwargs):
                loads.append(name)
            def transcribe(self, audio, **kwargs):
                beams.append(kwargs['beam_size'])
                word = types.SimpleNamespace(start=0.1, end=0.4, word=' hello', probability=.9)
                part = types.SimpleNamespace(no_speech_prob=0., avg_logprob=0., words=[word])
                return iter([part]), types.SimpleNamespace(language='en')
        fake = types.ModuleType('faster_whisper')
        fake.WhisperModel = Model
        tasks = '\n'.join(json.dumps(dict(path='unused.npy', language='en', live=live)) for live in (True, False))+'\n'
        output = io.StringIO()
        with patch.dict(sys.modules, {'faster_whisper': fake}), patch.object(sys, 'stdin', io.StringIO(tasks)), \
                patch.object(sys, 'stdout', output), patch.object(np, 'load', return_value=np.zeros(16000, dtype='float32')):
            exec(compile(service, '<ASR_SERVICE>', 'exec'), {})
        results = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(loads, ['turbo'])
        self.assertEqual(beams, [1, 5])
        self.assertEqual(len(results[0]['segments']), 1)
        self.assertEqual(results[1]['segments'], [])
        for result in results:
            self.assertEqual(result['asr_model'], 'turbo')
            self.assertGreaterEqual(result['inference_seconds'], 0)


if __name__ == '__main__':
    unittest.main()
