"""Correction propagation: after a reviewer assigns a sentence to a speaker,
find other sentences whose voice matches that speaker better than the speaker
they are currently labelled with. Uses the voice prints (speaker embeddings of
analysis windows) returned by the Kaggle worker; runs on CPU in milliseconds.

Measured on a 14-minute Vietnamese meeting where clustering merged three
people into one: marking one sentence finds the person's other sentence and
suggests nothing for correctly labelled speakers. Suggestions are never
applied automatically.
"""
import base64
import json

import numpy as np

from . import store

MIN_SIMILARITY = .50     # voice must resemble the chosen speaker…
MIN_ADVANTAGE = .05      # …more than the speaker it is labelled with now
MIN_SECONDS = 1.0        # shorter sentences carry too little voice evidence
MAX_SUGGESTIONS = 25

_cache = {}


def _revision(path):
    tail = path.stem.rsplit('r', 1)[-1]
    return int(tail) if tail.isdigit() else -1


def load_prints(mid):
    """(vectors, spans) from the newest saved GPU result that has voice prints."""
    folder = store.DATA / mid
    for path in sorted(folder.glob('gpu-result-r*.json'), key=_revision, reverse=True):
        try:
            key = (str(path), path.stat().st_mtime)
        except OSError:
            continue
        if key not in _cache:
            if len(_cache) > 8:
                _cache.clear()
            try:
                data = json.loads(path.read_text(encoding='utf-8')).get('voice_prints')
                if data:
                    vectors = np.frombuffer(base64.b64decode(data['data']), dtype='<f2')
                    vectors = vectors.reshape(-1, int(data['dim'])).astype(np.float32)
                    spans = np.asarray(data['spans'], dtype=np.float32).reshape(-1, 2)
                    _cache[key] = (vectors, spans) if len(vectors) == len(spans) else None
                else:
                    _cache[key] = None
            except (OSError, ValueError, KeyError, TypeError):
                _cache[key] = None
        if _cache[key] is not None:
            return _cache[key]
    return None


def segment_vector(segment, vectors, spans):
    a, b = float(segment['start']), float(segment['end'])
    overlap = np.clip(np.minimum(spans[:, 1], b) - np.maximum(spans[:, 0], a), 0, None)
    width = np.maximum(spans[:, 1] - spans[:, 0], 1e-6)
    chosen = np.where(overlap / width >= .5)[0]
    if not len(chosen):
        chosen = np.where(overlap > 0)[0]
    if not len(chosen):
        return None
    vector = (vectors[chosen] * overlap[chosen, None]).sum(0)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else None


def _unit(vector):
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-9 else None


def similar_segments(item, sid):
    prints = load_prints(item['id'])
    if prints is None:
        return dict(available=False, speaker=None, suggestions=[])
    vectors, spans = prints
    segments = item.get('segments') or []
    chosen = next((s for s in segments if s['id'] == sid), None)
    if chosen is None or chosen.get('speaker') is None:
        return dict(available=True, speaker=None, suggestions=[])
    target = chosen['speaker']
    rows = []
    for segment in segments:
        duration = float(segment['end']) - float(segment['start'])
        vector = segment_vector(segment, vectors, spans) if duration >= MIN_SECONDS else None
        rows.append((segment, vector, duration))
    # Target voice: the chosen sentence plus sentences the reviewer already
    # confirmed for this speaker (never the unreviewed clustering output).
    examples = [(v, d) for s, v, d in rows if v is not None and s['speaker'] == target
                and (s is chosen or s.get('edited'))]
    if not examples:
        return dict(available=True, speaker=target, suggestions=[])
    target_voice = _unit(sum(v * d for v, d in examples))
    totals = {}
    for segment, vector, duration in rows:
        if vector is not None and segment.get('speaker') is not None:
            totals[segment['speaker']] = totals.get(segment['speaker'], 0) + vector * duration
    suggestions = []
    for segment, vector, duration in rows:
        label = segment.get('speaker')
        if vector is None or label == target or segment.get('edited'):
            continue  # never override what the reviewer decided
        similarity = float(vector @ target_voice)
        if similarity < MIN_SIMILARITY:
            continue
        current = _unit(totals[label] - vector * duration) if label is not None else None
        current_similarity = float(vector @ current) if current is not None else -1.0
        if similarity - current_similarity < MIN_ADVANTAGE:
            continue
        suggestions.append(dict(id=segment['id'], start=segment['start'], end=segment['end'],
                                text=segment.get('text', '')[:300], speaker=label,
                                similarity=round(similarity, 3), current_similarity=round(current_similarity, 3)))
    suggestions.sort(key=lambda s: s['similarity'], reverse=True)
    return dict(available=True, speaker=target, suggestions=suggestions[:MAX_SUGGESTIONS])
