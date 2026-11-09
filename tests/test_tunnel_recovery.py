import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from app import gpu_connection, store


class TunnelRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.data=Path(self.tmp.name)
        self.folder=self.data/'gpu-bridge';self.folder.mkdir()
        self.instance='a'*32
        (self.folder/'tunnel-state.json').write_text(json.dumps(dict(url='https://stale.trycloudflare.com',instance=self.instance)))
        self.p=patch.object(store,'DATA',self.data);self.p.start()
    def tearDown(self):
        self.p.stop();self.tmp.cleanup()
    def test_healthy_local_relay_can_be_reused_when_public_url_dies(self):
        with patch('app.gpu_connection.urllib.request.urlopen',side_effect=[OSError('DNS'),io.BytesIO(json.dumps({'instance':self.instance}).encode())]):
            self.assertIsNone(gpu_connection.reusable_link())
            self.assertEqual(gpu_connection.local_relay_instance(),self.instance)
    def test_unrelated_relay_or_unhealthy_public_link_is_never_reused(self):
        with patch('app.gpu_connection.urllib.request.urlopen',return_value=io.BytesIO(b'{"instance":"someone-else"}')):
            self.assertIsNone(gpu_connection.local_relay_instance())
        with patch('app.gpu_connection.urllib.request.urlopen',side_effect=OSError('DNS')):
            self.assertFalse(gpu_connection.public_link_ready('https://dead.trycloudflare.com',self.instance))
    def test_recovery_does_not_launch_second_relay_or_return_url_from_stale_log(self):
        executable=self.data/'data/gpu-tools/cloudflared.exe';executable.parent.mkdir(parents=True);executable.touch()
        process=MagicMock();process.poll.return_value=None
        def start(*args,**kwargs):
            (self.folder/'tunnel.log').write_text('https://new.trycloudflare.com\nRegistered tunnel connection')
            return process
        with patch.object(store,'ROOT',self.data),patch.object(gpu_connection,'reusable_link',return_value=None), \
                patch.object(gpu_connection,'local_relay_instance',return_value=self.instance), \
                patch.object(gpu_connection,'shutdown'),patch.object(gpu_connection,'processes',[]), \
                patch.object(gpu_connection,'public_link_ready',return_value=True), \
                patch('app.gpu_connection.subprocess.Popen',side_effect=start) as popen:
            try:
                self.assertEqual(gpu_connection.connect(),'https://new.trycloudflare.com')
                self.assertEqual(popen.call_count,1)
                self.assertIn('cloudflared.exe',popen.call_args.args[0][0])
            finally:
                if gpu_connection.log_stream:
                    gpu_connection.log_stream.close();gpu_connection.log_stream=None

if __name__=='__main__':unittest.main()
