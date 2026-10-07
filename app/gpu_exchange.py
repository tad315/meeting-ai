"""Validated file exchange between the local demo and a user-run GPU notebook."""
import hashlib
import json
import zipfile
from pathlib import Path

from .demo_features import import_transcript

REVISION = '3533c8cf8e369892e6b79ff1bf80f7b0286a54ee'


def audio_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def make_package(item, target):
    metadata = dict(schema='voxmeet-gpu-v1', recording_id=item['id'],
                    revision=item['revision'], title=item['title'],
                    audio_sha256=audio_hash(item['audio']), duration=item['duration'],
                    model_revision=REVISION)
    partial = target.with_suffix('.part')
    with zipfile.ZipFile(partial, 'w', zipfile.ZIP_STORED) as archive:
        archive.write(item['audio'], 'audio.wav')
        archive.writestr('job.json', json.dumps(metadata))
    partial.replace(target)
    return target


def validate_result(payload, item):
    if not isinstance(payload, dict) or payload.get('schema') != 'voxmeet-gpu-v1':
        raise ValueError('Choose the result JSON exported by the VoxMeet GPU notebook.')
    if payload.get('recording_id') != item['id']:
        raise ValueError('This result belongs to a different recording.')
    if payload.get('revision') != item['revision']:
        raise ValueError('The transcript has changed since export. Download a fresh GPU package.')
    if payload.get('audio_sha256') != audio_hash(item['audio']):
        raise ValueError('Audio checksum does not match this recording.')
    provenance = payload.get('provenance', {})
    if not isinstance(provenance, dict) or provenance.get('diarization_revision') != REVISION:
        raise ValueError('The result must use the evaluated Community-1 baseline revision.')
    segments, speakers = import_transcript(payload)
    if any(s['end'] > item['duration'] + .1 for s in segments):
        raise ValueError('Transcript timestamps exceed the recording duration.')
    return segments, speakers, {k: str(provenance.get(k, ''))[:200] for k in
                               ('asr_model', 'diarization_model', 'diarization_revision')}
