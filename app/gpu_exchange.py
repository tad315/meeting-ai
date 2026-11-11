"""Validated file exchange between the local demo and a user-run GPU notebook."""
import hashlib
import json
import subprocess
import tempfile
import wave
import zipfile
from pathlib import Path

from .demo_features import import_transcript

REVISION = '3533c8cf8e369892e6b79ff1bf80f7b0286a54ee'


def audio_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pcm_hash(path):
    """SHA-256 of the raw 16-bit mono PCM frames, independent of container."""
    digest = hashlib.sha256()
    with wave.open(str(path)) as audio:
        if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
            raise ValueError('Expected 16-bit mono WAV.')
        while frames := audio.readframes(1 << 16):
            digest.update(frames)
    return digest.hexdigest()


def encode_flac(source, target):
    """Lossless FLAC copy of the normalized WAV; roughly half the transfer size."""
    from .providers import ffmpeg_path
    binary = ffmpeg_path()
    if not binary:
        raise ValueError('FFmpeg is missing.')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    result = subprocess.run([binary, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                             '-i', str(source), '-map_metadata', '-1', '-c:a', 'flac',
                             '-sample_fmt', 's16', '-compression_level', '5', '-y', str(target)],
                            capture_output=True, timeout=300, creationflags=flags)
    if result.returncode or not Path(target).is_file():
        raise ValueError('FLAC encoding failed.')
    return target


def make_package(item, target, audio_format='wav'):
    metadata = dict(schema='voxmeet-gpu-v1', recording_id=item['id'],
                    revision=item['revision'], title=item['title'],
                    audio_sha256=audio_hash(item['audio']), duration=item['duration'],
                    model_revision=REVISION, num_speakers=item.get('num_speakers'),
                    language=item.get('language', 'en'), clustering='VBxClustering',
                    turn_policy=item.get('turn_policy','legacy'))
    partial = target.with_suffix('.part')
    with tempfile.TemporaryDirectory(dir=Path(target).parent) as scratch:
        audio_path, audio_name = Path(item['audio']), 'audio.wav'
        if audio_format == 'flac':
            try:
                audio_path = encode_flac(item['audio'], Path(scratch) / 'audio.flac')
                metadata.update(audio_format='flac', pcm_sha256=pcm_hash(item['audio']))
                audio_name = 'audio.flac'
            except (OSError, ValueError, subprocess.SubprocessError):
                audio_path, audio_name = Path(item['audio']), 'audio.wav'  # always fall back to WAV
        with zipfile.ZipFile(partial, 'w', zipfile.ZIP_STORED) as archive:
            archive.write(audio_path, audio_name)
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
    if item.get('language') in ('vi','auto') and payload.get('language') != item['language']:
        raise ValueError('Kết quả dùng cấu hình ngôn ngữ cũ; cần chạy worker mới.')
    provenance = payload.get('provenance', {})
    if not isinstance(provenance, dict) or provenance.get('diarization_revision') != REVISION:
        raise ValueError('The result must use the evaluated Community-1 baseline revision.')
    if item.get('turn_policy')=='shared-sentence-v1' and provenance.get('assignment_policy')!='shared-sentence-v1':
        raise ValueError('Worker chưa hỗ trợ bộ xử lý câu chung. Cập nhật cell worker hoặc chọn baseline cũ.')
    # Sentence formatting is a server policy. Reuse raw full-recording VBx
    # tracks even when an already-running worker uses the retired reclustering.
    if item.get('turn_policy') == 'shared-sentence-v1' and payload.get('diarization_turns') is not None:
        import math
        from .speech_turns import baseline_sentence_labels
        tracks = payload['diarization_turns']
        if not isinstance(tracks, list) or len(tracks) > 50000:
            raise ValueError('Invalid diarization tracks.')
        for track in tracks:
            if (not isinstance(track, dict) or not isinstance(track.get('speaker'), str)
                or not track['speaker'] or len(track['speaker']) > 100
                or any(type(track.get(k)) not in (int,float) or not math.isfinite(track[k]) for k in ('start','end'))
                or not 0 <= track['start'] <= track['end'] <= item['duration'] + .1):
                raise ValueError('Invalid diarization track timestamps or speaker.')
        diagnostic=payload.get('speaker_diagnostic')
        if isinstance(diagnostic,list) and diagnostic and len(diagnostic)<=10000:
            info=diagnostic[0]
            if isinstance(info,dict) and info.get('policy')=='audio-reference-v2.1' and info.get('ready') is True:
                from .speech_turns import verify_window_sequence, tracks_from_windows
                labels=info.get('reference_labels')
                baseline=payload.get('baseline_diarization_turns')
                windows=diagnostic[1:]
                valid=(isinstance(labels,list) and 2<=len(labels)<=8 and all(isinstance(x,str) for x in labels)
                       and len(set(labels))==len(labels) and isinstance(baseline,list) and len(baseline)<=50000)
                valid=valid and all(isinstance(t,dict) and t.get('speaker') in labels
                    and all(type(t.get(k)) in (int,float) and math.isfinite(t[k]) for k in ('start','end'))
                    and 0<=t['start']<=t['end']<=item['duration']+.1 for t in baseline)
                valid=valid and all(isinstance(w,dict) and w.get('baseline') in labels
                    and all(type(w.get(k)) in (int,float) and math.isfinite(w[k]) for k in ('start','end'))
                    and 0<=w['start']<w['end']<=item['duration']+.1
                    and isinstance(w.get('scores'),list) and len(w['scores'])==len(labels)
                    and all(type(x) in (int,float) and math.isfinite(x) and -1.001<=x<=1.001 for x in w['scores']) for w in windows)
                if not valid:raise ValueError('Invalid audio verification evidence.')
                tracks=tracks_from_windows(baseline,verify_window_sequence(windows,labels,True))
                provenance=dict(provenance,alignment_revision='server-relative-sequence-1')
        words = payload.get('words')
        if not isinstance(words, list) or not words:
            raise ValueError('Missing word timestamps for sentence alignment.')
        payload = dict(payload, utterances=baseline_sentence_labels(words, tracks))
        provenance = dict(provenance, clustering='VBxClustering',
                          assignment_policy='shared-sentence-v1', alignment_revision=provenance.get('alignment_revision','server-vbx-sentences-1'))
    segments, speakers = import_transcript(payload)
    if any(s['end'] > item['duration'] + .1 for s in segments):
        raise ValueError('Transcript timestamps exceed the recording duration.')
    return segments, speakers, {k: str(provenance.get(k, ''))[:200] for k in
                               ('asr_model', 'diarization_model', 'diarization_revision', 'language', 'clustering', 'assignment_policy', 'caption_core_sha256', 'alignment_revision', 'speaker_verifier', 'worker_build')}
