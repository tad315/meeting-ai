"""Where the time of live captions goes, for recent Kaggle live sessions.

  python tools/live_report.py

Columns (median seconds per caption update):
  total     audio snapshot sent -> caption back in the app
  queue     waiting until Kaggle picked the job up
  roundtrip Kaggle picked it up -> result back in the app (GPU + network)
  gpu       speech recognition on the GPU
  network   roundtrip - gpu  (Cloudflare link, upload of the audio, worker loop)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import store  # noqa: E402


def main():
    for stream in (sys.stdout,):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    rows = [item for item in store.listing() if item.get('provider') == 'live-kaggle'][:10]
    if not rows:
        print('No Kaggle live sessions yet.')
        return
    print(f'{"id":9s} {"length":>6s} {"lang":4s} {"updates":>7s} {"total":>6s} {"queue":>6s} {"roundtrip":>9s} {"gpu":>5s} {"network":>7s} {"lag":>5s}')
    for item in rows:
        b = item.get('live_timing_breakdown') or {}
        def f(key):
            value = b.get(key)
            return f'{value:.2f}' if isinstance(value, (int, float)) else '-'
        print(f'{item["id"][:8]:9s} {round(item.get("duration") or 0):>5d}s {str(item.get("language") or ""):4s} '
              f'{str(b.get("updates", "-")):>7s} {f("total"):>6s} {f("queue"):>6s} {f("worker_round_trip"):>9s} '
              f'{f("inference"):>5s} {f("network_and_overhead"):>7s} {item.get("live_latency_seconds", "-")!s:>5s}')
    print('\nSessions before this update show "-" (no breakdown was recorded).')


if __name__ == '__main__':
    main()
