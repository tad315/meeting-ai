"""Single-port Kaggle link: self-healing quick tunnel + rendezvous, and the
security gate that lets the public tunnel reach only /gpu."""
import io
import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import community_queue, gpu_connection, step_one, store


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.patch = patch.object(store, 'DATA', self.data)
        self.patch.start()
        gpu_connection.link.update(url=None, checked=0.0, published=0.0, published_url=None, failures=0)
        self.folder = gpu_connection.bridge_folder()

    def tearDown(self):
        gpu_connection.link.update(url=None, checked=0.0, published=0.0, published_url=None, failures=0)
        self.patch.stop()
        self.tmp.cleanup()

    def health(self, instance=None):
        return lambda *a, **k: io.BytesIO(json.dumps({'instance': instance or gpu_connection.install_id()}).encode())

    def test_identity_and_topic_are_persistent_and_private(self):
        self.assertEqual(gpu_connection.install_id(), gpu_connection.install_id())
        topic = gpu_connection.rendezvous_topic()
        self.assertEqual(topic, gpu_connection.rendezvous_topic())
        self.assertRegex(topic, r'^voxmeet-[0-9a-f]{32}$')

    def test_healthy_saved_link_is_reused_without_new_tunnel(self):
        gpu_connection.remember_link('https://ok.trycloudflare.com')
        gpu_connection.link.update(url=None, checked=0)
        with patch('app.gpu_connection.urllib.request.urlopen', side_effect=self.health()), \
                patch.object(gpu_connection, 'start_tunnel') as start:
            self.assertEqual(gpu_connection.connect(), 'https://ok.trycloudflare.com')
            start.assert_not_called()

    def test_link_from_another_service_is_never_trusted(self):
        with patch('app.gpu_connection.urllib.request.urlopen', side_effect=self.health('someone-else')):
            self.assertFalse(gpu_connection.public_link_ready('https://x.trycloudflare.com'))

    def test_dead_link_is_replaced_after_two_failed_checks(self):
        gpu_connection.remember_link('https://dead.trycloudflare.com')
        gpu_connection.link['checked'] = 0
        with patch('app.gpu_connection.urllib.request.urlopen', side_effect=OSError('530')), \
                patch.object(gpu_connection, 'start_tunnel', return_value='https://new.trycloudflare.com') as start:
            self.assertEqual(gpu_connection.ensure_link(), 'https://dead.trycloudflare.com')  # one blip tolerated
            start.assert_not_called()
            self.assertTrue(gpu_connection.status()['repairing'])
            self.assertEqual(gpu_connection.ensure_link(), 'https://new.trycloudflare.com')
            start.assert_called_once()

    def test_explicit_connect_replaces_dead_link_immediately(self):
        gpu_connection.remember_link('https://dead.trycloudflare.com')
        with patch('app.gpu_connection.urllib.request.urlopen', side_effect=OSError('530')), \
                patch.object(gpu_connection, 'start_tunnel', return_value='https://new.trycloudflare.com'):
            self.assertEqual(gpu_connection.connect(), 'https://new.trycloudflare.com')

    def test_watchdog_never_blocks_behind_connect(self):
        with gpu_connection.lock, patch.object(gpu_connection, 'start_tunnel') as start:
            self.assertIsNone(gpu_connection.ensure_link())
            start.assert_not_called()

    def test_new_tunnel_waits_for_own_health_then_publishes(self):
        (store.ROOT / 'data/gpu-tools').mkdir(parents=True, exist_ok=True) if False else None
        exe = self.data / 'cloudflared.exe'; exe.write_text('')
        class Proc:
            pid = 1
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): pass
        def popen(args, stdout, stderr, creationflags):
            self.assertIn('http://127.0.0.1:%d' % gpu_connection.PORT, args)
            stdout.write('https://fresh-link.trycloudflare.com\nRegistered tunnel connection\n'); stdout.flush()
            return Proc()
        published = []
        with patch.object(gpu_connection.store, 'ROOT', self.data.parent), \
                patch.object(Path, 'is_file', return_value=True), \
                patch('app.gpu_connection.subprocess.Popen', side_effect=popen), \
                patch.object(gpu_connection, 'public_link_ready', return_value=True), \
                patch.object(gpu_connection, 'publish', side_effect=published.append):
            try:
                self.assertEqual(gpu_connection.start_tunnel(), 'https://fresh-link.trycloudflare.com')
            finally:
                gpu_connection.tunnel = None
                if gpu_connection.log_stream:
                    gpu_connection.log_stream.close(); gpu_connection.log_stream = None
        self.assertEqual(published, ['https://fresh-link.trycloudflare.com'])
        self.assertEqual(gpu_connection.saved_url(), 'https://fresh-link.trycloudflare.com')

    def test_publish_sends_only_the_url_to_the_private_topic(self):
        sent = []
        def fake(request, timeout):
            sent.append((request.full_url, json.loads(request.data)))
            return io.BytesIO(b'{}')
        with patch('app.gpu_connection.urllib.request.urlopen', side_effect=fake):
            self.assertTrue(gpu_connection.publish('https://a.trycloudflare.com'))
        url, body = sent[0]
        self.assertTrue(url.endswith('/' + gpu_connection.rendezvous_topic()))
        self.assertEqual(set(body), {'url', 'at'})

    def test_failed_publish_is_retried_on_next_healthy_check(self):
        gpu_connection.remember_link('https://ok.trycloudflare.com')
        gpu_connection.link.update(checked=0, published=time.time(), published_url='https://old.trycloudflare.com')
        with patch('app.gpu_connection.urllib.request.urlopen', side_effect=self.health()), \
                patch.object(gpu_connection, 'publish') as publish:
            gpu_connection.ensure_link()
            publish.assert_called_once_with('https://ok.trycloudflare.com')

    def test_expired_quick_tunnel_is_reported(self):
        log = self.folder / 'tunnel.log'
        log.write_text('INF Registered tunnel connection\nERR error="Unauthorized: Tunnel not found"\n')
        self.assertTrue(community_queue.tunnel_expired())
        log.write_text('ERR Unauthorized: Tunnel not found\nINF Registered tunnel connection\n')
        self.assertFalse(community_queue.tunnel_expired())


class NotebookLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(store, 'DATA', Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def credentials(self, worker):
        import ast
        source = ''.join(json.loads((worker[1] / 'worker.ipynb').read_text(encoding='utf-8'))['cells'][-1]['source'])
        found = {}
        for node in ast.parse(source).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and \
                    node.targets[0].id in ('WORKER_URL', 'WORKER_KEY', 'RENDEZVOUS_TOPIC'):
                found[node.targets[0].id] = ast.literal_eval(node.value)
        return found

    def test_notebook_uses_gpu_prefix_topic_and_follows_new_url_keeping_key(self):
        community_queue.create('https://one.trycloudflare.com', 'voxmeet-' + 'a' * 32)
        worker = community_queue.current()
        first = self.credentials(worker)
        self.assertEqual(first['WORKER_URL'], f'https://one.trycloudflare.com/gpu/worker/{worker[0]}')
        self.assertEqual(first['RENDEZVOUS_TOPIC'], 'voxmeet-' + 'a' * 32)
        self.assertGreater(worker[2]['expires'] - __import__('time').time(), 6 * 24 * 3600)
        community_queue.refresh_notebook(worker, 'https://two.trycloudflare.com')
        second = self.credentials(worker)
        self.assertEqual(second['WORKER_URL'], f'https://two.trycloudflare.com/gpu/worker/{worker[0]}')
        self.assertEqual(second['WORKER_KEY'], first['WORKER_KEY'])
        self.assertEqual(second['RENDEZVOUS_TOPIC'], first['RENDEZVOUS_TOPIC'])

    def test_worker_relink_adopts_published_url(self):
        import ast
        source = ''.join(json.loads((store.ROOT / 'colab/community_worker.ipynb').read_text(encoding='utf-8'))['cells'][-1]['source'])
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'relink')
        class Reply:
            text = '{"event":"open"}\n' + json.dumps(dict(event='message', message=json.dumps(dict(url='https://new-one.trycloudflare.com')))) + '\n'
        scope = dict(json=json, re=__import__('re'), RENDEZVOUS_TOPIC='voxmeet-x',
                     WORKER_URL='https://old.trycloudflare.com/gpu/worker/' + 'b' * 32, BASE='',
                     requests=type('R', (), {'get': staticmethod(lambda *a, **k: Reply())}))
        exec(compile(ast.Module([node], []), '<relink>', 'exec'), scope)
        self.assertTrue(scope['relink']())
        self.assertEqual(scope['WORKER_URL'], 'https://new-one.trycloudflare.com/gpu/worker/' + 'b' * 32)
        self.assertEqual(scope['BASE'], 'https://new-one.trycloudflare.com/gpu')
        self.assertFalse(scope['relink']())  # already current
        Reply.text = json.dumps(dict(event='message', message=json.dumps(dict(url='https://evil.example.com'))))
        self.assertFalse(scope['relink']())


class GateTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(step_one.app)

    def test_tunnel_reaches_only_gpu_api(self):
        public = {'host': 'abc.trycloudflare.com', 'cf-ray': '1'}
        self.assertEqual(self.client.get('/gpu/bridge-health', headers=public).status_code, 200)
        for path in ('/api/recordings', '/', '/live', '/api/health', '/gpuX', '/app.js'):
            self.assertEqual(self.client.get(path, headers=public).status_code, 404, path)
        # Cloudflare headers alone (any host) are also treated as public.
        self.assertEqual(self.client.get('/api/recordings', headers={'cf-connecting-ip': '1.2.3.4'}).status_code, 404)

    def test_worker_api_requires_its_token(self):
        public = {'host': 'abc.trycloudflare.com'}
        self.assertIn(self.client.get('/gpu/worker/' + 'c' * 32 + '/next', headers=public).status_code, (401, 404))

    def test_local_app_still_rejects_foreign_host(self):
        self.assertEqual(self.client.get('/api/health', headers={'host': 'evil.example'}).status_code, 400)
        self.assertEqual(self.client.get('/api/health').status_code, 200)


if __name__ == '__main__':
    unittest.main()
