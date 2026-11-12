"""Known Whisper hallucinations (text the model invents from its YouTube
training data when audio is silent, music or hard to hear).

Shared by the server (removes them from every transcript) and the Kaggle
worker (re-recognizes the affected span to recover the real speech).
Patterns are deliberately specific: ordinary meeting sentences must never match.
"""
import re
import unicodedata

PATTERNS = [re.compile(p) for p in (
    # Vietnamese YouTube outros
    r'(?:hãy )?(?:subscribe|đăng ký|đăng kí)(?: cho)? kênh(?: \S+){0,5}? để không bỏ lỡ(?: những| các)? video'
    r'(?: hấp dẫn| mới nhất| mới| tiếp theo| thú vị| bổ ích)?(?: nhé| nha)?',
    r'(?:hãy )?(?:subscribe|đăng ký|đăng kí)(?: cho)? kênh ghiền mì gõ',
    r'ghiền mì gõ',
    r'la la school',
    r'cảm ơn các bạn đã (?:xem|theo dõi) video(?: này)?(?: và hẹn gặp lại(?: các bạn)?(?: trong (?:những|các) video tiếp theo)?)?',
    r'hẹn gặp lại các bạn trong (?:những|các) video tiếp theo',
    r'(?:hãy )?(?:like|subscribe)(?: và| share| chia sẻ| đăng ký| kênh)+ để ủng hộ(?: \S+){0,4}',
    # English
    r'thanks? (?:you )?for watching',
    r'subtitles by the amara org community',
    r'please subscribe(?: to (?:my|our|the) channel)?',
)]


def normalize(text):
    text = unicodedata.normalize('NFC', str(text)).lower()
    text = re.sub(r"[^\w\s]", ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def word_text(word):
    return word.get('word', word.get('text', ''))


def find_spans(words):
    """[(i, j)] word index ranges (j exclusive) covered by a known hallucination."""
    joined, owner = [], []
    for index, word in enumerate(words):
        token = normalize(word_text(word))
        if not token:
            continue
        if joined:
            joined.append(' ')
            owner.append(index)
        joined.append(token)
        owner.extend([index] * len(token))
    text = ''.join(joined)
    spans = []
    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            if match.end() <= match.start():
                continue
            # Whole words only: do not cut inside a real word.
            if match.start() > 0 and text[match.start() - 1] != ' ':
                continue
            if match.end() < len(text) and text[match.end()] != ' ':
                continue
            spans.append((owner[match.start()], owner[match.end() - 1] + 1))
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def clean_words(words):
    """Return (kept_words, removed) where removed lists the dropped phrases."""
    spans = find_spans(words)
    if not spans:
        return list(words), []
    drop = set()
    removed = []
    for a, b in spans:
        drop.update(range(a, b))
        removed.append(dict(start=words[a]['start'], end=words[b - 1]['end'],
                            text=' '.join(word_text(w).strip() for w in words[a:b])))
    return [w for i, w in enumerate(words) if i not in drop], removed


def clean_utterances(utterances):
    """Same cleaning for already-formed utterances (word lists or plain text)."""
    result, removed = [], []
    for utterance in utterances:
        words = utterance.get('words') or []
        if words:
            kept, dropped = clean_words(words)
            removed += dropped
            if not kept:
                continue
            if dropped:
                utterance = dict(utterance, words=kept, start=kept[0]['start'], end=kept[-1]['end'],
                                 text=' '.join(word_text(w).strip() for w in kept))
        else:
            text = normalize(utterance.get('text', ''))
            if text and any(p.fullmatch(text) for p in PATTERNS):
                removed.append(dict(start=utterance.get('start'), end=utterance.get('end'), text=utterance.get('text')))
                continue
        result.append(utterance)
    return result, removed


def clean_text(text):
    """Remove known hallucinated phrases from one caption string (Live path,
    which has no per-word timestamps). Returns the remaining text, maybe ''."""
    if not isinstance(text, str) or not text.strip():
        return text
    tokens = text.split()
    kept, removed = clean_words([dict(word=t, start=0, end=0) for t in tokens])
    if not removed:
        return text
    return ' '.join(w['word'] for w in kept).strip()
