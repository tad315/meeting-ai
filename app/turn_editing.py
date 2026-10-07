"""Explicit user-selected turn boundaries; never infer speaker from wording."""
import copy
import math
import re
import uuid


def split_turn(segment, offset, at, left_speaker, right_speaker):
    text = segment['text']
    if not 0 < offset < len(text) or not text[offset - 1].isspace():
        raise ValueError('Choose a word boundary inside this turn.')
    left, right = text[:offset].strip(), text[offset:].strip()
    if not left or not right:
        raise ValueError('Both parts must contain speech.')
    if not math.isfinite(at) or not segment['start'] < at < segment['end']:
        raise ValueError('The split time must be inside this turn.')
    result = []
    for value, start, end, speaker in ((left,segment['start'],at,left_speaker),
                                      (right,at,segment['end'],right_speaker)):
        part = copy.deepcopy(segment)
        part.update(id='U'+uuid.uuid4().hex, text=value, start=start, end=end,
                    speaker=speaker, edited=True, split_from=segment['id'])
        # Original recognition is preserved in the full snapshot, not presented
        # as exact word timing for a newly edited text or manual boundary.
        part.pop('words',None)
        result.append(part)
    return result


def word_boundaries(segment):
    tokens = list(re.finditer(r'\S+',segment['text']))
    words = segment.get('words') or []
    exact = len(tokens)==len(words) and all(t.group()==w.get('text') for t,w in zip(tokens,words))
    result = []
    for i, token in enumerate(tokens[1:],1):
        at = words[i]['start'] if exact else None
        if at is not None and not segment['start'] < at < segment['end']: at=None
        result.append(dict(offset=token.start(),label=segment['text'][token.start():][:70],time=at))
    return result
