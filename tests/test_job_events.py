import asyncio
import threading
import time
import unittest

from app import job_events


class JobEventTests(unittest.TestCase):
    def test_notify_from_another_thread_wakes_waiter_immediately(self):
        async def scenario():
            threading.Timer(.05, job_events.notify).start()
            began = time.monotonic()
            await job_events.wait(2)
            return time.monotonic() - began
        self.assertLess(asyncio.run(scenario()), .5)

    def test_wait_times_out_without_notification(self):
        async def scenario():
            began = time.monotonic()
            await job_events.wait(.1)
            return time.monotonic() - began
        self.assertGreaterEqual(asyncio.run(scenario()), .09)
        self.assertEqual(job_events._waiters, set())


if __name__ == '__main__':
    unittest.main()
