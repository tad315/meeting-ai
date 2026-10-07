"""Pure transcript transformations, independent of model loading."""
import math
import re
from collections import Counter, defaultdict


def overlap(a, b):
    return max(0, min(a['end'], b['end']) - max(a['start'], b['start']))


def group_words(words):
    segments = []
    for word in words:
        text = word.get('punctuated_word', word.get('word', '')).strip()
        if not text:
            continue
        speaker = word.get('speaker')
        if speaker is not None:
            speaker = str(speaker)
        if (not segments or segments[-1]['speaker'] != speaker or
                word['start'] - segments[-1]['end'] > 2.5 or
                word['end'] - segments[-1]['start'] > 30):
            segments.append(dict(id=f's{len(segments)+1}', speaker=speaker,
                                 start=float(word['start']), end=float(word['end']), text=text, words=[]))
        else:
            segments[-1]['text'] += ' ' + text
            segments[-1]['end'] = max(segments[-1]['end'], float(word['end']))
        segments[-1]['words'].append(dict(text=text, start=float(word['start']), end=float(word['end']),
                                         confidence=word.get('confidence')))
    return segments


def assign_speakers(words, turns):
    for word in words:
        # Whisper sometimes returns point timestamps for short words. Assign
        # only when exactly one speaker is active at that point.
        if word['start'] == word['end']:
            speakers = {turn['speaker'] for turn in turns if turn['start'] <= word['start'] < turn['end']}
            word['speaker'] = next(iter(speakers)) if len(speakers) == 1 else None
            continue
        # A voice can have several adjacent intervals within one word. Compare
        # total covered time per voice, merging intervals to avoid double count.
        intervals = defaultdict(list)
        for turn in turns:
            if overlap(word, turn) > 0:
                intervals[turn['speaker']].append((max(word['start'], turn['start']),
                                                   min(word['end'], turn['end'])))
        scores = {}
        for speaker, spans in intervals.items():
            total, right = 0., float('-inf')
            for start, end in sorted(spans):
                total += max(0., end - max(start, right))
                right = max(right, end)
            scores[speaker] = total
        ranked = sorted(scores, key=scores.get, reverse=True)
        word['speaker'] = ranked[0] if ranked and (len(ranked) == 1 or
            scores[ranked[0]] - scores[ranked[1]] > 1e-6) else None
        word['ambiguous_speaker'] = len(ranked) > 1
    return words


def stabilize(segments, previous):
    """Preserve speaker IDs across cumulative passes using shared audio timestamps.

    This is temporal matching, not biometric identity recognition. New evidence
    can still revise earlier speaker assignments.
    """
    scores = defaultdict(float)
    for current in segments:
        if current['speaker'] is None:
            continue
        for old in previous:
            if old['speaker'] is not None:
                scores[(current['speaker'], old['speaker'])] += overlap(current, old)
    mapping, used = {}, set()
    for (new, old), score in sorted(scores.items(), key=lambda x: -x[1]):
        if score > 0 and new not in mapping and old not in used:
            mapping[new] = old
            used.add(old)
    next_id = max((int(s['speaker']) for s in previous if s['speaker'] is not None), default=-1) + 1
    for segment in segments:
        label = segment['speaker']
        if label is None:
            continue
        if label not in mapping:
            mapping[label] = str(next_id)
            next_id += 1
        segment['speaker'] = mapping[label]
    return segments


def evidence_text(segments, speakers):
    return '\n'.join(f"[{s['id']}] {s['start']:.1f}-{s['end']:.1f}s "
                     f"{speakers.get(s['speaker'], 'Unassigned')}: {s['text']}" for s in segments)


def retrieve(question, segments, limit=16):
    """Lexical retrieval with adjacent turns. Small meetings retain full context."""
    if len(segments) <= limit:
        return segments
    tokens = lambda text: re.findall(r'[a-z0-9]+', text.lower())
    query = set(tokens(question)) - {'the', 'a', 'is', 'what', 'who', 'did', 'was', 'in', 'to', 'of', 'and'}
    documents = [Counter(tokens(s['text'])) for s in segments]
    df = Counter(token for doc in documents for token in doc)
    ranked = sorted(range(len(segments)), key=lambda i: -sum(
        math.log(1 + len(segments) / (1 + df[t])) * min(documents[i][t], 3) for t in query))
    selected = set()
    for i in ranked:
        if not query.intersection(documents[i]):
            continue
        for index in (i, i - 1, i + 1):
            if 0 <= index < len(segments) and len(selected) < limit:
                selected.add(index)
        if len(selected) >= limit:
            break
    return [segments[i] for i in sorted(selected)]


def validate_citations(answer, allowed):
    citations = re.findall(r'\[(s\d+)\]', answer)
    if not citations or any(c not in allowed for c in citations):
        raise ValueError('The model did not provide valid transcript references. Please retry.')
    return list(dict.fromkeys(citations))
