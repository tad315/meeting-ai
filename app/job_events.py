"""In-process wake-ups between the app and the Kaggle worker API.

The worker API is mounted in the same process (port 8009), so a new job or a
returned result can wake the waiting side immediately instead of it sleeping
and re-checking the job folder every 0.1 s. Waits still time out and re-check
the files, so correctness never depends on a notification arriving.
"""
import asyncio
import threading

_lock = threading.Lock()
_waiters = set()


def notify():
    """Wake every waiter. Safe from any thread (enqueue runs in a worker thread)."""
    with _lock:
        waiters = list(_waiters)
    for loop, event in waiters:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:  # loop already closed
            pass


async def wait(timeout):
    """Sleep until notify() or the timeout, whichever comes first."""
    event = asyncio.Event()
    entry = (asyncio.get_running_loop(), event)
    with _lock:
        _waiters.add(entry)
    try:
        await asyncio.wait_for(event.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        with _lock:
            _waiters.discard(entry)
