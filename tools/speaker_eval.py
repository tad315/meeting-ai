"""Speaker-label evaluation for VoxMeet recordings (runs on CPU, no GPU needed).

Workflow
  1. Transcribe a recording with the Kaggle worker (Community-1).
  2. Correct the speaker labels in the app (edit / split sentences).
  3. python tools/speaker_eval.py reference <recording-id>
       -> saves the corrected labels as ground truth in data/speaker-eval/
  4. python tools/speaker_eval.py score [<recording-id> ...]
       -> re-computes several labelling strategies from the saved raw GPU
          result (data/<id>/gpu-result-r*.json) and scores each against the
          ground truth. No re-run on Kaggle is needed for post-processing ideas.

Metrics (speaker names are matched optimally, like DER):
  word accuracy      time-weighted share of reference words given the right speaker
  sentence accuracy  share of reference sentences whose majority label is right
  unassigned         share of speech left without any speaker
  speakers           number of speakers in reference vs. hypothesis
"""
import argparse
import itertools
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import store  # noqa: E402
from app import speech_turns  # noqa: E402

EVAL = store.DATA / 'speaker-eval'


# ----------------------------------------------------------------- reference
def reference_units(item):
    """Corrected transcript -> list of (start, end, speaker, sentence_index, text)."""
    units = []
    for index, segment in enumerate(item.get('segments') or []):
        speaker = segment.get('speaker')
        if speaker in (None, ''):
            continue
        words = segment.get('words') or []
        if words:
            for word in words:
                units.append(dict(start=float(word['start']), end=float(word['end']), speaker=str(speaker),
                                  sentence=index, text=str(word.get('word', word.get('text', ''))).strip()))
        else:
            units.append(dict(start=float(segment['start']), end=float(segment['end']), speaker=str(speaker),
                              sentence=index, text=segment.get('text', '')))
    return units


def save_reference(mid):
    item = store.get(mid)
    if not item:
        raise SystemExit(f'Recording not found: {mid}')
    units = reference_units(item)
    if not units:
        raise SystemExit('This recording has no labelled sentences.')
    EVAL.mkdir(parents=True, exist_ok=True)
    target = EVAL / f'{mid}.reference.json'
    target.write_text(json.dumps(dict(recording_id=mid, title=item.get('title'), revision=item.get('revision'),
                                      language=item.get('language'), created_at=time.time(), units=units),
                                 ensure_ascii=False), encoding='utf-8')
    speakers = Counter(u['speaker'] for u in units)
    print(f'Saved reference for "{item.get("title")}" ({len(units)} words, speakers: {dict(speakers)}) -> {target}')


# ---------------------------------------------------------------- hypotheses
def gpu_results(mid):
    """Every saved Kaggle run of this recording, oldest first."""
    files = sorted((store.DATA / mid).glob('gpu-result-r*.json'),
                   key=lambda p: int(p.stem.rsplit('r', 1)[-1]) if p.stem.rsplit('r', 1)[-1].isdigit() else -1)
    return [(p.stem.replace('gpu-result-', ''), json.loads(p.read_text(encoding='utf-8'))) for p in files]


def latest_gpu_result(mid):
    runs = gpu_results(mid)
    return runs[-1][1] if runs else None


def server_tracks(payload):
    """Exactly the tracks the server uses today (see gpu_exchange.validate_result)."""
    tracks = payload.get('diarization_turns')
    diagnostic = payload.get('speaker_diagnostic')
    if isinstance(diagnostic, list) and diagnostic:
        info = diagnostic[0]
        if isinstance(info, dict) and info.get('policy') == 'audio-reference-v2.1' and info.get('ready') is True:
            windows = diagnostic[1:]
            labels = info.get('reference_labels')
            tracks = speech_turns.tracks_from_windows(payload['baseline_diarization_turns'],
                                                      speech_turns.verify_window_sequence(windows, labels, True))
    return tracks


def word_level_labels(words, tracks):
    """Upper-bound style variant: every word takes its own best-overlapping speaker."""
    units = []
    for word in speech_turns.normalized_words(words):
        best, label = 0, None
        for track in tracks:
            overlap = min(word['end'], track['end']) - max(word['start'], track['start'])
            if overlap > best:
                best, label = overlap, track['speaker']
        units.append(dict(start=word['start'], end=word['end'], speaker=label))
    return units


def sentences_to_units(sentences):
    units = []
    for sentence in sentences:
        for word in sentence.get('words') or []:
            units.append(dict(start=word['start'], end=word['end'], speaker=sentence.get('speaker')))
    return units


def strategies(payload):
    """Name -> function(payload) returning hypothesis units. Add new ideas here."""
    words = payload.get('words')
    found = {}
    if words and payload.get('diarization_turns') is not None:
        found['current (server today)'] = lambda: sentences_to_units(
            speech_turns.baseline_sentence_labels(words, server_tracks(payload)))
        found['worker-verified tracks'] = lambda: sentences_to_units(
            speech_turns.baseline_sentence_labels(words, payload['diarization_turns']))
    if words and payload.get('baseline_diarization_turns') is not None:
        found['Community-1 raw tracks'] = lambda: sentences_to_units(
            speech_turns.baseline_sentence_labels(words, payload['baseline_diarization_turns']))
        found['Community-1 raw, word-level'] = lambda: word_level_labels(words, payload['baseline_diarization_turns'])
    if not found and payload.get('utterances'):
        found['stored result (old worker)'] = lambda: [
            dict(start=w['start'], end=w['end'], speaker=u.get('speaker'))
            for u in payload['utterances'] for w in (u.get('words') or [dict(start=u['start'], end=u['end'])])]
    return found


# ------------------------------------------------------------------- scoring
def best_mapping(confusion, ref_speakers, hyp_speakers):
    """One-to-one hyp->ref mapping that maximises matched time (exact for <= 8 speakers)."""
    hyp = [h for h in hyp_speakers if h is not None]
    if not hyp:
        return {}
    small, large = (hyp, ref_speakers) if len(hyp) <= len(ref_speakers) else (ref_speakers, hyp)
    if len(large) <= 8:
        best, best_map = -1, {}
        for perm in itertools.permutations(large, len(small)):
            pairs = list(zip(small, perm))
            mapping = {h: r for h, r in pairs} if small is hyp else {h: r for r, h in pairs}
            value = sum(confusion[r][h] for h, r in mapping.items())
            if value > best:
                best, best_map = value, mapping
        return best_map
    mapping, used = {}, set()
    for value, r, h in sorted(((confusion[r][h], r, h) for r in ref_speakers for h in hyp), reverse=True):
        if h not in mapping and r not in used and value > 0:
            mapping[h] = r
            used.add(r)
    return mapping


def score(reference, hypothesis):
    hyp = sorted(hypothesis, key=lambda u: u['start'])
    starts = [u['start'] for u in hyp]
    import bisect
    labelled = []
    for unit in reference:
        # Hypothesis speaker with the largest overlap (or nearest midpoint).
        mid = (unit['start'] + unit['end']) / 2
        i = bisect.bisect_left(starts, unit['start'] - 5)
        best, label = 0.0, None
        nearest, nearest_label = 1e9, None
        for h in hyp[i:]:
            if h['start'] > unit['end'] + 5:
                break
            overlap = min(unit['end'], h['end']) - max(unit['start'], h['start'])
            if overlap > best:
                best, label = overlap, h['speaker']
            distance = abs((h['start'] + h['end']) / 2 - mid)
            if distance < nearest:
                nearest, nearest_label = distance, h['speaker']
        if label is None and nearest < .3:
            label = nearest_label
        labelled.append((unit, label, max(.05, unit['end'] - unit['start'])))
    ref_speakers = sorted({u['speaker'] for u in reference})
    hyp_speakers = sorted({l for _, l, _ in labelled if l is not None})
    confusion = defaultdict(lambda: defaultdict(float))
    for unit, label, weight in labelled:
        confusion[unit['speaker']][label] += weight
    mapping = best_mapping(confusion, ref_speakers, hyp_speakers)
    total = sum(w for _, _, w in labelled)
    correct = sum(w for u, l, w in labelled if l is not None and mapping.get(l) == u['speaker'])
    unassigned = sum(w for _, l, w in labelled if l is None)
    sentences = defaultdict(lambda: [None, Counter()])
    for unit, label, weight in labelled:
        sentences[unit['sentence']][0] = unit['speaker']
        sentences[unit['sentence']][1][mapping.get(label) if label is not None else None] += weight
    sentence_ok = sum(1 for ref, votes in sentences.values() if votes.most_common(1)[0][0] == ref)
    errors = []
    for unit, label, weight in labelled:
        mapped = mapping.get(label) if label is not None else None
        if mapped != unit['speaker']:
            if errors and errors[-1]['sentence'] == unit['sentence'] and errors[-1]['hyp'] == mapped:
                errors[-1]['end'] = unit['end']
                errors[-1]['text'] += ' ' + unit['text']
            else:
                errors.append(dict(sentence=unit['sentence'], start=unit['start'], end=unit['end'],
                                   ref=unit['speaker'], hyp=mapped, text=unit['text']))
    return dict(word_accuracy=correct / total if total else 0, sentence_accuracy=sentence_ok / max(1, len(sentences)),
                unassigned=unassigned / total if total else 0, ref_speakers=len(ref_speakers),
                hyp_speakers=len(hyp_speakers), mapping=mapping, errors=errors)


def clock(seconds):
    seconds = int(seconds)
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def score_recording(mid, show_errors):
    path = EVAL / f'{mid}.reference.json'
    if not path.is_file():
        print(f'- {mid}: no reference yet (run: reference {mid})')
        return []
    reference = json.loads(path.read_text(encoding='utf-8'))
    runs = gpu_results(mid)
    if not runs:
        print(f'- {mid}: no saved GPU result in data/{mid}/')
        return []
    print(f'\n## {reference.get("title")} ({mid[:8]}, {len(reference["units"])} words)')
    rows = []
    for run_index, (run, payload) in enumerate(runs):
        last = run_index == len(runs) - 1
        hint = payload.get('num_speakers') or 'auto'
        print(f'\n### Kaggle run {run} (speaker count: {hint}, worker {payload.get("provenance", {}).get("worker_build", "?")})')
        print(f'{"strategy":32s} {"word acc":>9s} {"sentence":>9s} {"unassigned":>11s} {"speakers":>9s}')
        for name, build in strategies(payload).items():
            result = score(reference['units'], build())
            rows.append(dict(recording=mid, run=run, speaker_hint=str(hint), strategy=name,
                             **{k: v for k, v in result.items() if k not in ('errors', 'mapping')}))
            print(f'{name:32s} {result["word_accuracy"]:9.1%} {result["sentence_accuracy"]:9.1%} '
                  f'{result["unassigned"]:11.1%} {result["ref_speakers"]:>4d} / {result["hyp_speakers"]:<3d}')
            if not (show_errors and last and name.startswith('current')):
                continue
            print('   biggest errors (current):')
            for e in sorted(result['errors'], key=lambda e: e['end'] - e['start'], reverse=True)[:show_errors]:
                print(f'   {clock(e["start"])}-{clock(e["end"])}  should be {e["ref"]}, got {e["hyp"]}:  {e["text"][:80]}')
    return rows


def main():
    # Windows consoles/redirects default to a legacy code page (e.g. cp1258)
    # that cannot print Vietnamese titles. Always write UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='show recent recordings and their short ids')
    ref = sub.add_parser('reference', help='save the corrected labels of a recording as ground truth')
    ref.add_argument('recording_id')
    sc = sub.add_parser('score', help='score labelling strategies against saved references')
    sc.add_argument('recording_ids', nargs='*', help='default: every recording with a reference')
    sc.add_argument('--errors', type=int, default=8, help='show the N longest errors of the current strategy')
    args = parser.parse_args()
    if args.command == 'list':
        print(f'{"id":10s} {"length":>7s} {"speakers":>8s} {"GPU result":>10s} {"reference":>9s}  title')
        for item in store.listing()[:25]:
            if item.get('source') != 'upload':
                continue
            mid = item['id']
            full = store.get(mid) or {}
            speakers = len({s.get('speaker') for s in full.get('segments') or [] if s.get('speaker') is not None})
            has_gpu = 'yes' if list((store.DATA / mid).glob('gpu-result-r*.json')) else '-'
            has_ref = 'yes' if (EVAL / f'{mid}.reference.json').is_file() else '-'
            print(f'{mid[:8]:10s} {clock(item.get("duration") or 0):>7s} {speakers:>8d} {has_gpu:>10s} {has_ref:>9s}  {item.get("title", "")[:50]}')
        return
    if args.command == 'reference':
        save_reference(resolve(args.recording_id))
        return
    ids = [resolve(x) for x in args.recording_ids] or sorted(p.name.split('.')[0] for p in EVAL.glob('*.reference.json'))
    if not ids:
        raise SystemExit('No references yet. Correct a recording in the app, then run: reference <recording-id>')
    rows = []
    for mid in ids:
        rows += score_recording(mid, args.errors)
    if len(ids) > 1 and rows:
        print('\n## Average over recordings')
        by = defaultdict(list)
        for row in rows:
            by[row['strategy']].append(row)
        for name, items in by.items():  # all runs pooled
            print(f'{name:32s} {sum(r["word_accuracy"] for r in items) / len(items):9.1%} '
                  f'{sum(r["sentence_accuracy"] for r in items) / len(items):9.1%}')
    EVAL.mkdir(parents=True, exist_ok=True)
    with (EVAL / 'history.jsonl').open('a', encoding='utf-8') as log:
        for row in rows:
            log.write(json.dumps(dict(row, scored_at=time.time()), ensure_ascii=False) + '\n')


def resolve(value):
    """Accept a full id or a unique prefix (e.g. the first 8 characters)."""
    if len(value) == 32:
        return value
    matches = [item['id'] for item in store.listing() if item['id'].startswith(value)]
    if len(matches) != 1:
        raise SystemExit(f'"{value}" matches {len(matches)} recordings; use more characters.')
    return matches[0]


if __name__ == '__main__':
    main()
