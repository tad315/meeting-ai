"""Authenticated, expiring notebook worker; no automatic CPU fallback."""
import hashlib
import json
import secrets
import time
import uuid
from . import store
from .gpu_exchange import make_package


def root():
    return store.DATA / 'gpu-bridge'


def current():
    try:
        sid = (root() / 'current-worker').read_text()
        folder = root() / sid
        config = json.loads((folder / 'session.json').read_text())
        if config['expires'] <= time.time() or (folder / 'closed').exists():
            return None
        return sid, folder, config
    except (OSError, ValueError, KeyError):
        return None


def status():
    worker = current()
    age = None
    if worker:
        try:
            age = max(0, time.time() - (worker[1] / 'heartbeat').stat().st_mtime)
        except OSError:
            pass
    features = []
    if worker:
        try: features = json.loads((worker[1]/'features.json').read_text())
        except (OSError, ValueError): pass
    return {'features': features, 'connected': age is not None and age < 90,
            'configured': worker is not None, 'heartbeat_age_seconds': age}


def create(url):
    old = current()
    if old:
        (old[1] / 'closed').touch()
    sid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
    folder = root() / sid
    folder.mkdir(parents=True)
    config = dict(kind='worker', expires=time.time()+8*3600,
                  token_hash=hashlib.sha256(token.encode()).hexdigest())
    (folder / 'session.json').write_text(json.dumps(config))
    notebook = json.loads((store.ROOT / 'colab/community_worker.ipynb').read_text(encoding='utf-8'))
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code':
            text = ''.join(cell['source']).replace('WORKER_URL_PLACEHOLDER', repr(url + '/worker/' + sid))
            text = text.replace('WORKER_KEY_PLACEHOLDER', repr(token))
            text = text.replace('COMMON_TURN_SOURCE_PLACEHOLDER', repr((store.ROOT/'app/speech_turns.py').read_text(encoding='utf-8')))
            cell['source'] = text.splitlines(True)
    (folder / 'worker.ipynb').write_text(json.dumps(notebook), encoding='utf-8')
    (root() / 'current-worker').write_text(sid)


def enqueue(item):
    worker = current()
    if not worker or not status()['connected']:
        raise ValueError('Community-1 worker is offline. Start the Kaggle worker in Setup first.')
    sid = uuid.uuid4().hex
    folder = root() / sid
    folder.mkdir()
    make_package(item, folder / 'job.zip')
    config = dict(worker_id=worker[0], recording_id=item['id'], revision=item['revision'],
                  expires=worker[2]['expires'], token_hash=worker[2]['token_hash'])
    # Publish only after the package is complete.
    (folder / 'session.json').write_text(json.dumps(config))
    publish_pending(worker, sid)
    return folder


def refresh_notebook(worker):
    """Refresh worker code without rotating its private URL/key or disconnecting it."""
    import ast
    sid, folder, config = worker
    existing = json.loads((folder/'worker.ipynb').read_text(encoding='utf-8'))
    credentials = {}
    for cell in existing['cells']:
        if cell.get('cell_type') != 'code':continue
        for node in ast.parse(''.join(cell['source'])).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in ('WORKER_URL','WORKER_KEY'):
                        credentials[target.id]=ast.literal_eval(node.value)
    url, token = credentials.get('WORKER_URL'), credentials.get('WORKER_KEY')
    if (not isinstance(url,str) or not url.endswith('/worker/'+sid) or not isinstance(token,str)
            or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(),config['token_hash'])):
        raise ValueError('Không thể cập nhật notebook phiên này; hãy kết nối lại trong Setup.')
    notebook=json.loads((store.ROOT/'colab/community_worker.ipynb').read_text(encoding='utf-8'))
    for cell in notebook['cells']:
        if cell.get('cell_type')=='code':
            source=''.join(cell['source']).replace('WORKER_URL_PLACEHOLDER',repr(url)).replace('WORKER_KEY_PLACEHOLDER',repr(token))
            source=source.replace('COMMON_TURN_SOURCE_PLACEHOLDER',repr((store.ROOT/'app/speech_turns.py').read_text(encoding='utf-8')))
            cell['source']=source.splitlines(keepends=True)
    temp=folder/'worker-refresh.part'
    temp.write_text(json.dumps(notebook),encoding='utf-8')
    temp.replace(folder/'worker.ipynb')



def publish_pending(worker, job_id):
    """Index only unfinished jobs, rather than scanning all historical exports."""
    pending=worker[1]/'pending'
    if not pending.is_dir():
        pending.mkdir(exist_ok=True)
        for path in root().glob('*/session.json'):
            if any((path.parent/name).exists() for name in ('closed','result.json','error.json')):continue
            try:config=json.loads(path.read_text())
            except (OSError,ValueError):continue
            if config.get('worker_id')==worker[0] and config.get('expires',0)>time.time():
                (pending/path.parent.name).touch()
    (pending/job_id).touch()
