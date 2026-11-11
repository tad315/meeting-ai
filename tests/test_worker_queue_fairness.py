import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from starlette.requests import Request
from app import gpu_bridge


class FairLiveChatQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.patch=patch.object(gpu_bridge,'ROOT',self.root);self.patch.start()
        self.worker='a'*32
        self.token='test-only-token'
        worker=self.root/self.worker;worker.mkdir()
        (worker/'pending').mkdir()
        (worker/'session.json').write_text(json.dumps(dict(kind='worker',expires=time.time()+60,
            token_hash=hashlib.sha256(self.token.encode()).hexdigest())))

    def tearDown(self):
        self.patch.stop();self.tmp.cleanup()

    def job(self,letter,kind,age,worker_id=None):
        sid=letter*32;p=self.root/sid;p.mkdir()
        session=p/'session.json'
        session.write_text(json.dumps(dict(kind=kind,worker_id=worker_id or self.worker,expires=time.time()+60)))
        stamp=time.time()-age;os.utime(session,(stamp,stamp))
        (self.root/self.worker/'pending'/sid).touch()
        (p/'job.zip').write_bytes(b'fixture')
        return sid

    def test_fresh_question_does_not_preempt_caption(self):
        asr=self.job('b','live_asr',.2);self.job('c','llm',.5)
        self.assertEqual(gpu_bridge.pending_job(self.worker,self.root/self.worker),asr)

    def test_waiting_question_is_not_starved_by_continuous_asr(self):
        question=self.job('c','llm',3)
        for i in range(4):
            asr=self.job('bdef'[i],'live_asr',.1)
            self.assertEqual(gpu_bridge.pending_job(self.worker,self.root/self.worker),question)
            (self.root/asr/'closed').touch()

    def test_running_assistant_does_not_block_captions_with_second_question(self):
        active=self.job('c','llm',20);self.job('d','llm',10)
        asr=self.job('b','live_asr',.1)
        self.assertEqual(gpu_bridge.pending_job(self.worker,self.root/self.worker,exclude=active),asr)

    def test_other_worker_exclusion_cannot_hide_our_questions(self):
        other=self.job('d','llm',30,worker_id='e'*32)
        question=self.job('c','llm',3);self.job('b','live_asr',.1)
        self.assertEqual(gpu_bridge.pending_job(self.worker,self.root/self.worker,exclude=other),question)

    def test_record_batch_stays_behind_live_asr(self):
        self.job('c','record',30);asr=self.job('b','live_asr',.1)
        self.assertEqual(gpu_bridge.pending_job(self.worker,self.root/self.worker),asr)

    def test_first_delivery_time_is_stable_across_retries(self):
        question=self.job('c','llm',3)
        request=Request(dict(type='http',headers=[(b'authorization',('Bearer '+self.token).encode())]))
        first=asyncio.run(gpu_bridge.next_job(self.worker,request))
        stamp=json.loads((self.root/question/'dispatch.json').read_text())
        second=asyncio.run(gpu_bridge.next_job(self.worker,request))
        self.assertEqual(first,second)
        self.assertEqual(json.loads((self.root/question/'dispatch.json').read_text()),stamp)
        self.assertGreaterEqual(stamp['first_dispatched_at'],time.time()-2)



    def test_completed_excluded_job_returns_without_long_poll(self):
        active=self.job('c','llm',2)
        (self.root/active/'result.json').write_text('{}')
        self.job('d','llm',1)
        request=Request(dict(type='http',headers=[(b'authorization',('Bearer '+self.token).encode())]))
        start=time.monotonic()
        response=asyncio.run(gpu_bridge.next_job(self.worker,request,inline=True,wait=15,exclude=active))
        self.assertEqual(response.status_code,204)
        self.assertLess(time.monotonic()-start,.5)

    def test_live_translation_does_not_wait_two_seconds(self):
        translation=self.job('c','llm',.1)
        path=self.root/translation/'session.json'
        data=json.loads(path.read_text());data['realtime_translation']=True;path.write_text(json.dumps(data))
        self.job('b','live_asr',.2)
        self.assertEqual(gpu_bridge.pending_job(self.worker,self.root/self.worker),translation)
    def test_aged_asr_is_not_starved_by_translation(self):
        translation=self.job('c','llm',.1)
        path=self.root/translation/'session.json'
        data=json.loads(path.read_text());data['realtime_translation']=True;path.write_text(json.dumps(data))
        audio=self.job('b','live_asr',3)
        self.assertEqual(gpu_bridge.pending_job(self.worker,self.root/self.worker),audio)

    def test_independent_lanes_never_deliver_other_gpu_work(self):
        audio=self.job('b','live_asr',1)
        assistant=self.job('c','llm',1)
        self.assertEqual(gpu_bridge.pending_job(self.worker,self.root/self.worker,lane='audio'),audio)
        self.assertEqual(gpu_bridge.pending_job(self.worker,self.root/self.worker,lane='assistant'),assistant)
