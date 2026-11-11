"""On-demand tunnel and per-recording notebook generation for interactive Colab."""
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
import urllib.request
from . import store
from .gpu_exchange import make_package
from .relay_version import relay_code_version

processes = []
log_stream = None
# connect() and the watchdog must never start/stop relays at the same time.
relay_lock = threading.Lock()


def shutdown():
    global log_stream
    for process in processes:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill()
    processes.clear()
    if log_stream: log_stream.close(); log_stream = None


def reusable_link():
    try:
        state = json.loads((store.DATA/'gpu-bridge/tunnel-state.json').read_text())
        url, instance = state['url'], state['instance']
        if not re.fullmatch(r'https://[a-z0-9-]+\.trycloudflare\.com', url) or not instance:
            return None
        for endpoint in ('http://127.0.0.1:8010', url):
            with urllib.request.urlopen(endpoint + '/bridge-health', timeout=8) as response:
                health = json.load(response)
                if health.get('instance') != instance or health.get('code') != relay_code_version():
                    return None
        return url
    except (OSError, ValueError, KeyError):
        return None


def remember_link(url, instance):
    target = store.DATA/'gpu-bridge/tunnel-state.json'
    partial = target.with_suffix('.part')
    partial.write_text(json.dumps(dict(url=url, instance=instance)))
    partial.replace(target)
    return url


def local_relay_instance():
    """Reuse only our existing relay, never an unrelated service on port 8010."""
    try:
        state = json.loads((store.DATA/'gpu-bridge/tunnel-state.json').read_text())
        instance = state['instance']
        if not isinstance(instance, str) or not re.fullmatch('[0-9a-f]{32}', instance):
            return None
        with urllib.request.urlopen('http://127.0.0.1:8010/bridge-health', timeout=3) as response:
            health = json.load(response)
        if health.get('instance') == instance:
            if health.get('code') == relay_code_version():
                return instance
            # Our relay, but started with older code (it survives app restarts).
            stop_stale_relay()
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    return None


def stop_stale_relay():
    """Stop only a VoxMeet relay process listening on port 8010 (Windows)."""
    if os.name != 'nt':
        return False
    script = ("Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { "
              "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($_.OwningProcess)\"; "
              "if ($p -and $p.CommandLine -like '*app.gpu_bridge:bridge*') { Stop-Process -Id $p.ProcessId -Force } }")
    try:
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True,
                       timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        return False
    for _ in range(20):
        try:
            urllib.request.urlopen('http://127.0.0.1:8010/bridge-health', timeout=1).close()
        except OSError:
            return True
        time.sleep(.5)
    return False


def start_relay(instance):
    """Start the local relay on port 8010 with a known instance id."""
    folder = store.DATA/'gpu-bridge'
    folder.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess,'CREATE_NO_WINDOW',0) | getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    env = os.environ.copy()
    env['VOXMEET_BRIDGE_DATA'] = str(folder)
    env['VOXMEET_BRIDGE_INSTANCE'] = instance
    relay_log = (folder/'relay.log').open('ab')
    try:
        relay = subprocess.Popen([sys.executable,'-m','uvicorn','app.gpu_bridge:bridge',
                                  '--host','127.0.0.1','--port','8010','--no-access-log'],
                                 cwd=store.ROOT, env=env, stdout=relay_log,stderr=relay_log,
                                 creationflags=flags)
    finally: relay_log.close()
    processes.append(relay)
    time.sleep(2)
    return relay.poll() is None


def ensure_relay():
    if not relay_lock.acquire(blocking=False):
        return False  # a connect() is in progress
    try:
        return _ensure_relay()
    finally:
        relay_lock.release()


def _ensure_relay():
    """Keep an existing Kaggle link working: if the relay behind the saved tunnel
    stopped (or runs old code), start it again with the SAME instance id. The
    cloudflared tunnel and the worker notebook URL stay valid, so Kaggle
    reconnects by itself without downloading a new notebook."""
    try:
        state = json.loads((store.DATA/'gpu-bridge/tunnel-state.json').read_text())
        instance = state['instance']
        if not isinstance(instance, str) or not re.fullmatch('[0-9a-f]{32}', instance):
            return False
    except (OSError, ValueError, KeyError):
        return False
    try:
        with urllib.request.urlopen('http://127.0.0.1:8010/bridge-health', timeout=3) as response:
            health = json.load(response)
        if health.get('instance') != instance:
            return False  # some other service owns the port; never touch it
        if health.get('code') == relay_code_version():
            return True
        if not stop_stale_relay():
            return False
    except (OSError, ValueError, AttributeError):
        pass  # relay is down
    return start_relay(instance)


def public_link_ready(url, instance):
    try:
        with urllib.request.urlopen(url+'/bridge-health', timeout=3) as response:
            return json.load(response).get('instance') == instance
    except (OSError, ValueError, AttributeError):
        return False


def connect():
    with relay_lock:
        return _connect()


def _connect():
    global log_stream
    previous = reusable_link()
    if previous:
        return previous
    executable = store.ROOT/'data/gpu-tools/cloudflared.exe'
    if not executable.is_file():
        raise ValueError('Run setup-gpu-link.ps1 once, then try again.')
    folder = store.DATA/'gpu-bridge'
    folder.mkdir(parents=True,exist_ok=True)
    log = folder/'tunnel.log'
    # A running cloudflared process is NOT proof that its public URL still exists.
    # Retire owned stale processes instead of returning the first URL in an old log.
    shutdown()
    instance = local_relay_instance()
    flags = getattr(subprocess,'CREATE_NO_WINDOW',0) | getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    if not instance:
        instance = uuid.uuid4().hex
        if not start_relay(instance):
            shutdown()
            raise ValueError('GPU transfer port 8010 is unavailable. See data/gpu-bridge/relay.log.')
        # Save the instance before public registration so a later app restart can
        # recognize this relay even if Cloudflare failed to establish its link.
        remember_link('https://pending.trycloudflare.com', instance)
    log_stream = log.open('w')
    tunnel = subprocess.Popen([str(executable),'tunnel','--url','http://127.0.0.1:8010',
                               '--no-autoupdate'],stdout=log_stream,stderr=log_stream,
                              creationflags=flags)
    processes.append(tunnel)
    for _ in range(40):
        if any(p.poll() is not None for p in processes): break
        contents = log.read_text(errors='replace')
        if 'Unauthorized: Tunnel not found' in contents:
            break
        match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',contents)
        if match and 'Registered tunnel connection' in contents:
            url = match.group(0)
            if public_link_ready(url, instance):
                return remember_link(url, instance)
        time.sleep(1)
    shutdown()
    raise ValueError('Could not establish a healthy GPU transfer link. Check your network and retry.')


def prepare(item, url):
    sid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
    folder = store.DATA/'gpu-bridge'/sid
    folder.mkdir(parents=True)
    make_package(item,folder/'job.zip')
    config = dict(recording_id=item['id'],revision=item['revision'],expires=time.time()+3600,
                  token_hash=hashlib.sha256(token.encode()).hexdigest())
    (folder/'session.json').write_text(json.dumps(config))
    notebook = json.loads((store.ROOT/'colab/voxmeet_gpu.ipynb').read_text(encoding='utf-8'))
    notebook['cells'][0]['source'] = [
        '# VoxMeet · Connected recording\n',
        'Enable T4 and HF_TOKEN. Run setup; restart if instructed. Then run the processing cell once.\n',
        'This notebook downloads only the selected recording and returns the result automatically.\n',
        'Keep the local demo open. Transfer expires after one hour. Audio passes through Cloudflare to Google Colab.\n',
        '**Private notebook: contains a temporary access key. Do not share it.**\n']
    setup = f'GPU_LINK = {url!r} + "/exchange/{sid}"\nGPU_KEY = {token!r}\n'
    for cell in notebook['cells']:
        if cell['cell_type'] != 'code': continue
        text = ''.join(cell['source'])
        if 'uploaded = files.upload()' in text:
            start = text.index('uploaded = files.upload()')
            end = text.index('with zipfile.ZipFile', start)
            text = text[:start] + setup + '''import requests
print('Downloading the selected recording from your demo...', flush=True)
response = requests.get(GPU_LINK + '/audio', headers={'Authorization':'Bearer '+GPU_KEY}, timeout=180)
response.raise_for_status()
raw_zip = response.content
''' + text[end:]
            text = text.replace('files.download(str(result_path))', '''print('Returning transcript to the demo...', flush=True)
response = requests.post(GPU_LINK + '/result', headers={'Authorization':'Bearer '+GPU_KEY},
                         data=result_path.read_bytes(), timeout=120)
response.raise_for_status()
print('Returned successfully. Open the recording in the local demo.')''')
        elif 'files.download(str(result_path))' in text:
            text = '''# Retry sending if the network failed. The JSON is still available locally.
response = requests.post(GPU_LINK + '/result', headers={'Authorization':'Bearer '+GPU_KEY},
                         data=result_path.read_bytes(), timeout=120)
response.raise_for_status()
print('Returned successfully.')
'''
        cell['source'] = text.splitlines(True)
    (folder/'connected.ipynb').write_text(json.dumps(notebook),encoding='utf-8')
    return sid
