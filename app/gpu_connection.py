"""Kaggle link: the app itself (one port, default 8009) serves the worker API
under /gpu, and one cloudflared quick tunnel exposes ONLY that prefix.

Quick-tunnel URLs die when the PC sleeps or the network drops for a while.
The watchdog then creates a new tunnel and publishes the new URL to a private
rendezvous topic; the running Kaggle notebook reads it and reconnects by itself,
so the notebook never needs to be downloaded again for a new link.
"""
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
import urllib.request
from . import store
from .gpu_exchange import make_package

PORT = int(os.environ.get('VOXMEET_PORT', '8009'))
PREFIX = '/gpu'
RENDEZVOUS = os.environ.get('VOXMEET_RENDEZVOUS', 'https://ntfy.sh').rstrip('/')
URL_PATTERN = r'https://[a-z0-9-]+\.trycloudflare\.com'
CHECK_EVERY = 60          # seconds between public health checks
REPUBLISH_EVERY = 1800    # keep the rendezvous message fresh (ntfy caches ~12 h)

lock = threading.Lock()
tunnel = None
log_stream = None
link = dict(url=None, checked=0.0, published=0.0, published_url=None, failures=0)


def bridge_folder():
    folder = store.DATA/'gpu-bridge'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _persistent(name, make):
    path = bridge_folder()/name
    try:
        value = path.read_text().strip()
        if value:
            return value
    except OSError:
        pass
    value = make()
    path.write_text(value)
    return value


def install_id():
    """Identifies this VoxMeet installation in /gpu/bridge-health."""
    return _persistent('install-id', lambda: uuid.uuid4().hex)


def rendezvous_topic():
    """Private, unguessable topic carrying only the current public URL."""
    return _persistent('rendezvous-topic', lambda: 'voxmeet-' + secrets.token_hex(16))


def shutdown():
    global tunnel, log_stream
    if tunnel is not None and tunnel.poll() is None:
        tunnel.terminate()
        try: tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired: tunnel.kill()
    tunnel = None
    if log_stream:
        log_stream.close(); log_stream = None


def stop_stale_processes():
    """Windows: stop cloudflared/relay processes left by earlier app runs
    (including the retired separate relay on port 8010). Only VoxMeet's own."""
    if os.name != 'nt':
        return
    keep = tunnel.pid if tunnel is not None and tunnel.poll() is None else -1
    script = ("Get-CimInstance Win32_Process | Where-Object { "
              "($_.Name -eq 'cloudflared.exe' -and $_.CommandLine -like '*--url http://127.0.0.1:80*') -or "
              "($_.CommandLine -like '*app.gpu_bridge:bridge*') } | "
              f"Where-Object {{ $_.ProcessId -ne {keep} }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    try:
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True,
                       timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        time.sleep(1)
    except (OSError, subprocess.SubprocessError):
        pass


def public_link_ready(url):
    try:
        with urllib.request.urlopen(url + PREFIX + '/bridge-health', timeout=8) as response:
            return json.load(response).get('instance') == install_id()
    except (OSError, ValueError, AttributeError):
        return False


def saved_url():
    try:
        url = json.loads((bridge_folder()/'tunnel-state.json').read_text()).get('url')
        return url if isinstance(url, str) and re.fullmatch(URL_PATTERN, url) else None
    except (OSError, ValueError, AttributeError):
        return None


def remember_link(url):
    target = bridge_folder()/'tunnel-state.json'
    partial = target.with_suffix('.part')
    partial.write_text(json.dumps(dict(url=url, instance=install_id(), port=PORT, created=time.time())))
    partial.replace(target)
    link.update(url=url, checked=time.time(), failures=0)
    return url


def publish(url):
    """Tell a running Kaggle notebook where the app is now. Best effort."""
    try:
        body = json.dumps(dict(url=url, at=time.time())).encode()
        request = urllib.request.Request(f'{RENDEZVOUS}/{rendezvous_topic()}', data=body, method='POST',
                                         headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(request, timeout=10).close()
        link.update(published=time.time(), published_url=url)
        return True
    except OSError:
        return False


def start_tunnel():
    global tunnel, log_stream
    executable = store.ROOT/'data/gpu-tools/cloudflared.exe'
    if not executable.is_file():
        raise ValueError('Run setup-gpu-link.ps1 once, then try again.')
    shutdown()
    stop_stale_processes()
    log = bridge_folder()/'tunnel.log'
    log_stream = log.open('w')
    flags = getattr(subprocess,'CREATE_NO_WINDOW',0) | getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    tunnel = subprocess.Popen([str(executable), 'tunnel', '--url', f'http://127.0.0.1:{PORT}', '--no-autoupdate'],
                              stdout=log_stream, stderr=log_stream, creationflags=flags)
    for _ in range(45):
        if tunnel.poll() is not None:
            break
        contents = log.read_text(errors='replace')
        match = re.search(URL_PATTERN, contents)
        if match and 'Registered tunnel connection' in contents and public_link_ready(match.group(0)):
            url = remember_link(match.group(0))
            publish(url)
            return url
        time.sleep(1)
    shutdown()
    raise ValueError('Could not establish a healthy GPU transfer link. Check your network and retry.')


def _ensure(force=False):
    url = link['url'] or saved_url()
    now = time.time()
    if url and not force and now - link['checked'] < CHECK_EVERY:
        return url
    if url and public_link_ready(url):
        link.update(url=url, checked=now, failures=0)
        # Retry until the notebook has been told about THIS url (a publish can
        # fail on a weak network right after the tunnel was replaced).
        if link['published_url'] != url or now - link['published'] > REPUBLISH_EVERY:
            publish(url)
        return url
    # One slow health check must not rotate a working link (the notebook would
    # have to rediscover it); replace after two consecutive failures.
    link['failures'] += 1
    if url and link['failures'] < 2 and not force:
        return url
    return start_tunnel()


def ensure_link():
    """Watchdog entry point; never blocks behind a connect() in progress."""
    if not lock.acquire(blocking=False):
        return None
    try:
        return _ensure()
    except ValueError:
        return None
    finally:
        lock.release()


def connect():
    with lock:
        return _ensure(force=True)


def current_url():
    return link['url'] or saved_url()


def status():
    return dict(url=current_url(), failures=link['failures'], repairing=link['failures'] > 0)


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
    setup = f'GPU_LINK = {url!r} + "/gpu/exchange/{sid}"\nGPU_KEY = {token!r}\n'
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
