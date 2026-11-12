"""Restore a recording's transcript from an automatic backup.

The app saves the whole transcript before every re-run (Kaggle result, split,
reprocess) as data/<id>/transcript-before-*.json. This puts one back.

  python tools/restore_transcript.py 009419a7            # newest backup with manual corrections
  python tools/restore_transcript.py 009419a7 --list     # show available backups
  python tools/restore_transcript.py 009419a7 --file transcript-before-gpu-r9-....json

The transcript being replaced is itself backed up first, so this is reversible.
Summaries/Q&A of the recording are cleared (they belonged to the other text).
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import store  # noqa: E402


def resolve(value):
    if len(value) == 32:
        return value
    matches = [item['id'] for item in store.listing() if item['id'].startswith(value)]
    if len(matches) != 1:
        raise SystemExit(f'"{value}" matches {len(matches)} recordings; use more characters.')
    return matches[0]


def describe(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    segments = data.get('segments') or []
    corrected = sum(1 for s in segments if s.get('edited') or s.get('split_from'))
    speakers = len({s.get('speaker') for s in segments if s.get('speaker') is not None})
    return data, corrected, speakers, len(segments)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('recording_id')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--file')
    args = parser.parse_args()
    mid = resolve(args.recording_id)
    folder = store.DATA / mid
    backups = sorted(folder.glob('transcript-before-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not backups:
        raise SystemExit('No backups for this recording.')
    if args.list:
        for path in backups:
            _, corrected, speakers, count = describe(path)
            print(f'{time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))}  '
                  f'{count:4d} turns  {speakers:2d} speakers  {corrected:3d} corrected  {path.name}')
        return
    if args.file:
        chosen = folder / Path(args.file).name
        if not chosen.is_file():
            raise SystemExit(f'Not found: {chosen.name}')
    else:
        chosen = next((p for p in backups if describe(p)[1] > 0), None)
        if chosen is None:
            raise SystemExit('No backup contains manual corrections. Use --list and --file to pick one.')
    backup, corrected, speakers, count = describe(chosen)
    item = store.get(mid)
    if item is None:
        raise SystemExit('Recording not found.')
    if item.get('status') in ('recording', 'queued', 'decoding', 'transcribing', 'cancelling'):
        raise SystemExit('The recording is being processed. Wait until it finishes, then retry.')
    safety = folder / f'transcript-before-restore-r{item.get("revision", 0)}-{time.time_ns()}.json'
    safety.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
    item.update(segments=backup.get('segments') or [], speakers=backup.get('speakers') or {},
                provenance=backup.get('provenance'), warning=backup.get('warning'),
                revision=int(item.get('revision') or 0) + 1, status='ready', stage='complete', progress=100,
                summary=None, summary_status=None, summary_error=None, messages=[], qa=[], error=None)
    store.save(item)
    print(f'Restored {count} turns ({speakers} speakers, {corrected} corrected) from {chosen.name}.')
    print(f'The replaced transcript was saved as {safety.name}. Refresh the page (Ctrl+F5).')


if __name__ == '__main__':
    main()
