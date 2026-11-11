"""Version of the GPU relay code, so a relay left running with old code is replaced."""
import hashlib
from pathlib import Path

FILES = ('gpu_bridge.py', 'gpu_payloads.py', 'relay_version.py')


def relay_code_version():
    digest = hashlib.sha256()
    folder = Path(__file__).resolve().parent
    for name in FILES:
        # Normalize line endings: the same code must give the same version.
        digest.update((folder / name).read_bytes().replace(b'\r\n', b'\n'))
    return digest.hexdigest()[:16]
