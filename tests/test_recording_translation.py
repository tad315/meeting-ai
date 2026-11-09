import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app import recording_translation as translation, step_one, store


def sample():
    return dict(revision=3, title='Meeting', status='ready', speakers={'a':'Speaker A'},
        segments=[dict(id='first', text='Do not spend more than 30 dollars.', start=1, end=3, speaker='a'),
                  dict(id='second', text='We have not approved it.', start=4, end=6, speaker='a')])


class TranslationTests(unittest.TestCase):
    def test_complete_translation_preserves_original_and_exports_both(self):
        item=sample(); original=copy.deepcopy(item)
        progress=AsyncMock()
        response=json.dumps({'translations':[{'id':'2','text':'Chúng ta chưa phê duyệt.'},
            {'id':'1','text':'Không chi quá 30 đô la.'}]})
        with patch.object(translation.remote_tasks,'chat',AsyncMock(return_value=response)) as chat:
            result=asyncio.run(translation.translate(item,'vi',progress))
        self.assertEqual(item,original)
        self.assertEqual(result['segments'][0],{'id':'first','text':'Không chi quá 30 đô la.'})
        self.assertIn('Vietnamese', chat.call_args.args[0][0]['content'])
        progress.assert_awaited_once_with(1,1)
        item['translation']=result
        output=translation.bilingual_text(item)
        self.assertIn('Do not spend more than 30 dollars.',output)
        self.assertIn('Không chi quá 30 đô la.',output)
        self.assertIn('00:01 · Speaker A',output)
        item['segments'][0]['text']='Changed without revision bump'
        with self.assertRaises(ValueError):translation.bilingual_text(item)

    def test_missing_duplicate_and_unknown_ids_rejected_then_retry(self):
        batch=[dict(id='1'),dict(id='2')]
        for rows in [[dict(id='1',text='a')], [dict(id='1',text='a'),dict(id='1',text='b')],
                     [dict(id='1',text='a'),dict(id='99',text='b')]]:
            with self.assertRaises(ValueError):translation.decode(json.dumps({'translations':rows}),batch)
        good=json.dumps({'translations':[dict(id='1',text='A'),dict(id='2',text='B')]})
        with patch.object(translation.remote_tasks,'chat',AsyncMock(side_effect=['invalid',good])) as chat:
            result=asyncio.run(translation.translate(sample(),'en',AsyncMock()))
        self.assertEqual(chat.await_count,2)
        self.assertEqual(result['target_language'],'en')

    def test_split_long_turns_keeps_all_words_and_bounded_units(self):
        item=sample(); text=('First sentence. ' * 120)+'Last words.'
        item['segments']=[dict(id='long',text=text,start=0,end=10)]
        units=translation.units_for(item)
        self.assertGreater(len(units),1)
        self.assertTrue(all(len(u['text'])<=450 for u in units))
        self.assertEqual(' '.join(u['text'] for u in units).split(),text.split())


class TranslationAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data_patch=patch.object(store,'DATA',Path(self.tmp.name));self.data_patch.start()
        self.client=TestClient(step_one.app).__enter__()
        self.item=store.create('Meeting','upload','community')
        self.item.update(sample(),summary={'keep':True},qa=[{'keep':True}]);store.save(self.item)
        self.url='/api/recordings/'+self.item['id']
    def tearDown(self):
        step_one.summary_locks.discard(self.item['id'])
        self.client.__exit__(None,None,None);self.data_patch.stop();self.tmp.cleanup()

    def test_worker_required_active_capture_blocked_and_unknown_language_rejected(self):
        with patch('app.remote_tasks.require_worker',side_effect=ValueError('Worker offline')):
            self.assertEqual(self.client.post(self.url+'/translation',json={'target_language':'vi'}).status_code,400)
        self.assertEqual(self.client.post(self.url+'/translation',json={'target_language':'fr'}).status_code,422)
        store.patch(self.item['id'],status='recording')
        self.assertEqual(self.client.post(self.url+'/translation',json={'target_language':'vi'}).status_code,409)

    def test_background_progress_and_completed_result_survive_reopen(self):
        def discard(coroutine):coroutine.close()
        with patch('app.remote_tasks.require_worker'),patch.object(step_one,'launch',discard):
            response=self.client.post(self.url+'/translation',json={'target_language':'vi'})
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()['translation_status'],'processing')
        self.assertEqual(self.client.delete(self.url).status_code,409)
        async def fake(item,target,progress):
            await progress(1,1)
            return dict(revision=item['revision'],fingerprint=translation.fingerprint(item),
                target_language=target,segments=[dict(id='first',text='Bản dịch'),dict(id='second',text='Chưa duyệt')])
        with patch.object(translation,'translate',fake):
            asyncio.run(step_one.run_translation(self.item['id'],copy.deepcopy(self.item),'vi'))
        reopened=self.client.get(self.url).json()
        self.assertEqual(reopened['translation_status'],'ready')
        self.assertEqual(reopened['summary'],{'keep':True})
        self.assertEqual(reopened['qa'],[{'keep':True}])
        self.assertEqual(reopened['segments'],self.item['segments'])
        self.assertEqual(reopened['translation_progress'],dict(done=1,total=1))
        self.assertEqual(self.client.get(self.url+'/translation/export').status_code,200)
        store.patch(self.item['id'],revision=4)
        self.assertEqual(self.client.get(self.url+'/translation/export').status_code,409)

    def test_failed_or_stale_result_preserves_previous_translation(self):
        old={'keep':'previous translation'}
        store.patch(self.item['id'],translation=old)
        with patch.object(translation,'translate',AsyncMock(side_effect=ValueError('Invalid response'))):
            asyncio.run(step_one.run_translation(self.item['id'],copy.deepcopy(self.item),'vi'))
        item=store.get(self.item['id'])
        self.assertEqual(item['translation'],old)
        self.assertEqual(item['translation_status'],'error')
        result=dict(revision=3,fingerprint=translation.fingerprint(self.item),segments=[])
        store.patch(self.item['id'],revision=4)
        with patch.object(translation,'translate',AsyncMock(return_value=result)):
            asyncio.run(step_one.run_translation(self.item['id'],copy.deepcopy(self.item),'vi'))
        self.assertEqual(store.get(self.item['id'])['translation'],old)

    def test_restart_clears_stuck_translation_state(self):
        store.patch(self.item['id'],translation_status='processing')
        store.init()
        item=store.get(self.item['id'])
        self.assertEqual(item['translation_status'],'error')
        self.assertEqual(item['segments'],self.item['segments'])


if __name__=='__main__':unittest.main()
