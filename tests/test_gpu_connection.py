import hashlib
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_demo_features as demo
from app import gpu_bridge, gpu_connection, store
from app.gpu_exchange import REVISION


class ConnectionTests(unittest.TestCase):
    setUp = demo.DemoTests.setUp
    tearDown = demo.DemoTests.tearDown
    imported = demo.DemoTests.imported

    def create(self):
        item = store.get(self.imported()['id'])
        wav = Path(self.tmp.name)/'audio.wav';wav.write_bytes(b'fixture')
        item.update(audio=str(wav),duration=4)
        store.save(item)
        with patch('app.gpu_connection.connect',return_value='https://test.trycloudflare.com'):
            r=self.client.post(f"/api/recordings/{item['id']}/gpu-session")
        self.assertEqual(r.status_code,201,r.text)
        item=r.json();sid=item['gpu_session']['id']
        folder=store.DATA/'gpu-bridge'/sid
        notebook=json.loads((folder/'connected.ipynb').read_text())
        token=None
        for cell in notebook['cells']:
            if cell['cell_type']=='code':
                code=''.join(cell['source']);compile(code,'cell','exec')
                for line in code.splitlines():
                    if line.startswith('GPU_KEY = '):
                        import ast
                        token=ast.literal_eval(line.split(' = ',1)[1])
        self.assertTrue(token)
        payload=dict(demo.PAYLOAD,schema='voxmeet-gpu-v1',recording_id=item['id'],revision=item['revision'],
                     audio_sha256=hashlib.sha256(b'fixture').hexdigest(),
                     provenance={'diarization_revision':REVISION})
        return item,sid,token,payload

    def test_roundtrip_and_auth_isolation(self):
        item,sid,token,payload=self.create()
        with patch.object(gpu_bridge,'ROOT',store.DATA/'gpu-bridge'), TestClient(gpu_bridge.bridge) as relay:
            route=f'/exchange/{sid}'
            self.assertEqual(relay.get(route+'/audio').status_code,401)
            self.assertEqual(relay.get('/api/recordings').status_code,404)
            headers={'Authorization':'Bearer '+token}
            self.assertEqual(relay.get(route+'/audio',headers=headers).status_code,200)
            self.assertEqual(relay.post(route+'/result',json=payload,headers=headers).status_code,200)
            response=self.client.post(f"/api/recordings/{item['id']}/gpu-session/check")
            self.assertEqual(response.json()['gpu_session']['status'],'applied',response.text)
            self.assertEqual(response.json()['revision'],2)
            self.assertEqual(relay.get(route+'/audio',headers=headers).status_code,410)

    def test_edit_after_export_cannot_be_overwritten(self):
        item,sid,token,payload=self.create()
        self.client.patch(f"/api/recordings/{item['id']}/segments/U0001",json=dict(revision=1,text='Edited',speaker='0'))
        (store.DATA/'gpu-bridge'/sid/'result.json').write_text(json.dumps(payload))
        response=self.client.post(f"/api/recordings/{item['id']}/gpu-session/check").json()
        self.assertEqual(response['gpu_session']['status'],'error')
        self.assertEqual(response['segments'][0]['text'],'Edited')

    def test_expired_session(self):
        item,sid,token,_=self.create()
        path=store.DATA/'gpu-bridge'/sid/'session.json'
        config=json.loads(path.read_text());config['expires']=time.time()-1;path.write_text(json.dumps(config))
        with patch.object(gpu_bridge,'ROOT',store.DATA/'gpu-bridge'),TestClient(gpu_bridge.bridge) as relay:
            self.assertEqual(relay.get(f'/exchange/{sid}/audio',headers={'Authorization':'Bearer '+token}).status_code,410)
