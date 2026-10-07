"""Real model smoke test through the running upload API; no mocked AI output."""
import json
import time
from pathlib import Path

import httpx

from app.local_models import download

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'data/evaluation'
BASE = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/'


def wait(client, mid):
    deadline = time.monotonic() + 900
    previous = None
    while time.monotonic() < deadline:
        response = client.get('/api/recordings/' + mid)
        response.raise_for_status()
        item = response.json()
        state = (item['status'], item.get('stage'))
        if state != previous:
            print(state, flush=True)
            previous = state
        if item['status'] not in {'queued', 'decoding', 'transcribing', 'cancelling'}:
            return item
        time.sleep(1)
    raise TimeoutError('Transcription did not finish within 15 minutes.')


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    reports = []
    with httpx.Client(base_url='http://127.0.0.1:8008', timeout=120) as client:
        for name, size in [('1-two-speakers-en.wav', 512044), ('2-two-speakers-en.wav', 1088078)]:
            path = OUTPUT / name
            download(BASE + name, path, size)
            with path.open('rb') as audio:
                response = client.post('/api/upload', files={'file': (name, audio, 'audio/wav')})
            response.raise_for_status()
            mid = response.json()['id']
            assert wait(client, mid)['status'] == 'uploaded'
            response = client.post(f'/api/recordings/{mid}/transcribe', json={'provider': 'local'})
            response.raise_for_status()
            item = wait(client, mid)
            assert item['status'] == 'ready', item.get('error')
            assert item['segments'], 'No speech detected in speech fixture'
            assert all(0 <= s['start'] <= s['end'] <= item['duration'] + .5 for s in item['segments'])
            exported = client.get(f'/api/recordings/{mid}/export')
            exported.raise_for_status()
            (OUTPUT / (path.stem + '.txt')).write_text(exported.text, encoding='utf-8')
            reports.append({'source': BASE + name, 'result': item})
            (OUTPUT / 'report.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
            print(json.dumps({'file': name, 'id': mid, 'duration': item['duration'],
                              'processing_seconds': item['processing_seconds'], 'speakers': item['speakers'],
                              'text': [s['text'] for s in item['segments']]}), flush=True)


if __name__ == '__main__':
    main()
