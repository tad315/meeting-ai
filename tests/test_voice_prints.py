import ast
import base64
import json
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class VoicePrintTests(unittest.TestCase):
    def test_pack_is_compact_normalised_and_strips_diagnostic(self):
        source = ''.join(json.loads((ROOT / 'colab/community_worker.ipynb').read_text(encoding='utf-8'))['cells'][-1]['source'])
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'pack_voice_prints')
        scope = dict(np=np)
        exec(compile(ast.Module([node], []), '<pack>', 'exec'), scope)
        diagnostic = [dict(kind='summary')] + [dict(start=i, end=i + 2.8, embedding=list(np.random.rand(256) * 5)) for i in range(4)]
        packed = scope['pack_voice_prints'](diagnostic)
        self.assertTrue(all('embedding' not in w for w in diagnostic))
        self.assertEqual((packed['dim'], len(packed['spans'])), (256, 4))
        vectors = np.frombuffer(base64.b64decode(packed['data']), dtype='<f2').reshape(4, 256).astype(np.float32)
        np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1, atol=2e-3)
        self.assertIsNone(scope['pack_voice_prints']([dict(kind='summary')]))


if __name__ == '__main__':
    unittest.main()
