"""Remove Whisper's invented YouTube outros ("Hãy subscribe cho kênh La La
School…", "Ghiền Mì Gõ"…) from transcripts that were saved before the filter
covered them (for example Live recordings).

  python tools/clean_hallucinations.py            # show what would be removed
  python tools/clean_hallucinations.py --apply    # remove it

Each changed recording is backed up first as data/<id>/transcript-before-clean-*.json
(restore with tools/restore_transcript.py <id> --list / --file). Its summary and
Q&A are cleared because they were made from the old text.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import store  # noqa: E402
from app.hallucination import clean_text  # noqa: E402

BUSY = ('recording', 'queued', 'decoding', 'transcribing', 'cancelling')


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    total = 0
    for row in store.listing():
        item = store.get(row['id'])
        if not item or not item.get('segments'):
            continue
        kept, removed = [], []
        for segment in item['segments']:
            text = clean_text(segment.get('text', ''))
            if text == segment.get('text'):
                kept.append(segment)
                continue
            removed.append(segment.get('text', ''))
            if text and text.strip():
                kept.append(dict(segment, text=text))
        if not removed:
            continue
        total += len(removed)
        print(f"{item.get('title')}  ({item['id'][:8]})")
        for text in removed:
            print(f'   - {text}')
        if not args.apply:
            continue
        if item.get('status') in BUSY:
            print('   skipped: still being processed, run again later')
            continue
        folder = store.DATA / item['id']
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / f"transcript-before-clean-r{item.get('revision', 0)}-{time.time_ns()}.json"
        backup.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
        store.patch(item['id'], segments=kept, revision=int(item.get('revision') or 0) + 1,
                    summary=None, summary_status=None, summary_error=None, messages=[], qa=[])
        print(f'   cleaned; backup {backup.name}')
    if not total:
        print('No invented phrases found.')
    elif not args.apply:
        print(f'\n{total} line(s) would change. Run again with --apply to remove them.')
    else:
        print('\nDone. Refresh the page (Ctrl+F5).')


if __name__ == '__main__':
    main()
