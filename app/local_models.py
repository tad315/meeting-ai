"""Public CPU model assets for the upload transcription pipeline."""
import hashlib
import json
import os
import tarfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / 'data/models'
SEGMENTATION = MODEL_ROOT / 'sherpa-onnx-pyannote-segmentation-3-0/model.onnx'
EMBEDDING = MODEL_ROOT / 'nemo_en_titanet_small.onnx'
ASR = MODEL_ROOT / 'whisper-base.en'
SEG_URL = ('https://github.com/k2-fsa/sherpa-onnx/releases/download/'
           'speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2')
EMB_URL = ('https://github.com/k2-fsa/sherpa-onnx/releases/download/'
           'speaker-recongition-models/nemo_en_titanet_small.onnx')


def model_status():
    return {'asr': all((ASR / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')),
            'segmentation': SEGMENTATION.is_file(), 'embedding': EMBEDDING.is_file()}


def download(url, destination, expected_size=None):
    """Download atomically; failed downloads never look like complete models."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and (expected_size is None or destination.stat().st_size == expected_size):
        return
    partial = destination.with_suffix(destination.suffix + '.part')
    size = 0
    with httpx.stream('GET', url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        declared = int(response.headers.get('content-length', 0))
        with partial.open('wb') as output:
            for chunk in response.iter_bytes(1024 * 1024):
                size += len(chunk)
                output.write(chunk)
    if (expected_size and size != expected_size) or (declared and size != declared):
        raise RuntimeError(f'Incomplete download: {destination.name}. Run setup again.')
    partial.replace(destination)


def prepare():
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    print('Downloading speaker segmentation model...', flush=True)
    archive = MODEL_ROOT / 'segmentation.tar.bz2'
    download(SEG_URL, archive, 6958444)
    # Extract only known regular files; never trust archive paths or symlinks.
    folder = SEGMENTATION.parent
    folder.mkdir(exist_ok=True)
    with tarfile.open(archive, 'r:bz2') as source:
        for name in ('model.onnx', 'LICENSE', 'README.md'):
            member = source.getmember(f'sherpa-onnx-pyannote-segmentation-3-0/{name}')
            if not member.isfile():
                raise RuntimeError('Unexpected model archive entry.')
            with source.extractfile(member) as input_file, (folder / (name + '.part')).open('wb') as output:
                while chunk := input_file.read(1024 * 1024):
                    output.write(chunk)
            (folder / (name + '.part')).replace(folder / name)
    print('Downloading English speaker embedding model...', flush=True)
    download(EMB_URL, EMBEDDING, 40257283)
    print('Downloading English Whisper base model...', flush=True)
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
    from huggingface_hub import snapshot_download
    snapshot_download('Systran/faster-whisper-base.en', local_dir=ASR, max_workers=2,
                      allow_patterns=['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt', 'README.md'])
    files = [SEGMENTATION, EMBEDDING, ASR / 'model.bin']
    hashes = {}
    for path in files:
        with path.open('rb') as model_file:
            hashes[str(path.relative_to(MODEL_ROOT))] = hashlib.file_digest(model_file, 'sha256').hexdigest()
    inventory = {'sources': [SEG_URL, EMB_URL, 'https://huggingface.co/Systran/faster-whisper-base.en'],
                 'sha256': hashes}
    (MODEL_ROOT / 'inventory.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    print('Local models are ready. No account token is required.', flush=True)


if __name__ == '__main__':
    prepare()
