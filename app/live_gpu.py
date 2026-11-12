"""GPU chunk transcription. Audio capture never waits for remote inference.

Labels denote capture sources, not inferred people. Final Community-1 processing
of the saved recording is available separately.
"""
import asyncio
import io
import json
import math
import time
import uuid
import wave
import numpy as np
from . import store, remote_tasks


def wav_bytes(samples):
    output = io.BytesIO()
    with wave.open(output, 'wb') as w:
        w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        w.writeframes(samples.astype('<i2').tobytes())
    return output.getvalue()


def validated_turns(result, channel, offset, duration, cutoff):
    rows = result.get('segments')
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('Kết quả live GPU không hợp lệ.')
    turns = []
    for row in rows:
        start, end, text = row.get('start'), row.get('end'), row.get('text')
        if (any(type(v) not in (float, int) or not math.isfinite(v) for v in (start,end))
                or not 0 <= start <= end <= duration+.1 or not isinstance(text,str) or len(text)>4000):
            raise ValueError('Timestamp/nội dung live GPU không hợp lệ.')
        # A one-second context overlap is not emitted twice.
        from .hallucination import clean_text
        text = clean_text(text)
        if end <= cutoff or not text.strip(): continue
        turns.append(dict(id=uuid.uuid4().hex, start=offset+max(start,cutoff), end=offset+end,
            text=text.strip(), speaker=(f"{channel}_{row['speaker']}" if isinstance(row.get('speaker'),str) and row['speaker'].startswith('speaker_') else None) if result.get('speaker_labels') else str(channel), source_channel=channel, provisional=True))
    merged = []
    for turn in turns:
        if (merged and turn['speaker']==merged[-1]['speaker'] and turn['start']-merged[-1]['end'] < .8 and
                turn['end']-merged[-1]['start'] <= 8 and
                not merged[-1]['text'].rstrip().endswith(('.', '?', '!'))):
            merged[-1]['text'] += ' ' + turn['text']
            merged[-1]['end'] = turn['end']
        else: merged.append(turn)
    return merged


def chunk_ready(samples, enabled, rate=16000):
    """Prefer a quiet boundary; bound waiting during continuous speech.

    This inexpensive energy gate only schedules inference. The worker's speech
    detector still decides whether a region contains speech.
    """
    if len(samples) >= rate * 4:
        return True
    if len(samples) < rate * 2:
        return False
    channels = [index for index, selected in enumerate(enabled) if selected]
    if not channels:
        return False
    tail = samples[-int(rate * .65):, channels].astype(np.float32)
    blocks = tail[:len(tail) // 320 * 320].reshape(-1, 320, len(channels))
    rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    return bool(np.all(rms < 180))


async def capture(ws, config):
    # Preserve the public entry point while using revisable rolling captions.
    from .live_rolling import capture as rolling_capture
    return await rolling_capture(ws, config)
