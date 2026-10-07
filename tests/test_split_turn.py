import unittest
import test_demo_features as demo
from app import store
from app.turn_editing import word_boundaries


class SplitTests(unittest.TestCase):
    setUp=demo.DemoTests.setUp
    tearDown=demo.DemoTests.tearDown
    imported=demo.DemoTests.imported

    def test_split_preserves_content_backup_and_clears_summary(self):
        item=self.imported()
        stored=store.get(item['id']);stored['summary']={'revision':1};store.save(stored)
        path=f"/api/recordings/{item['id']}/segments/U0001/split"
        body=dict(revision=1,offset=3,at=1,left_speaker='0',right_speaker=None)
        r=self.client.post(path,json=body)
        self.assertEqual(r.status_code,200,r.text)
        result=r.json()
        self.assertEqual([s['text'] for s in result['segments'][:2]],['We','discussed the design.'])
        self.assertIsNone(result['summary'])
        self.assertEqual(result['segments'][0]['end'],result['segments'][1]['start'])
        self.assertNotEqual(result['segments'][0]['id'],result['segments'][1]['id'])
        self.assertTrue(list((store.DATA/item['id']).glob('transcript-before-split-*.json')))
        self.assertEqual(self.client.post(path,json=body).status_code,409)

    def test_invalid_boundary_time_and_speaker_rejected(self):
        item=self.imported();path=f"/api/recordings/{item['id']}/segments/U0001/split"
        base=dict(revision=1,offset=3,at=1,left_speaker='0',right_speaker=None)
        for changes in ({'offset':2},{'at':0},{'at':3},{'right_speaker':'missing'}):
            self.assertEqual(self.client.post(path,json=dict(base,**changes)).status_code,400)
        self.assertEqual(store.get(item['id'])['revision'],1)

    def test_timing_only_used_when_words_match(self):
        s=dict(text='Hello there',start=0,end=2,words=[dict(text='Hello',start=0,end=.5),dict(text='there',start=1,end=2)])
        self.assertEqual(word_boundaries(s)[0]['time'],1)
        s['text']='Good morning'
        self.assertIsNone(word_boundaries(s)[0]['time'])
