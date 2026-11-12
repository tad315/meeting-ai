"""Single-recording, expiring exchange service. Never exposes the main app."""
import hashlib
import asyncio
import json
import os
import re
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from .gpu_payloads import MAX_GPU_RESULT_BYTES, ERROR_MESSAGES
from . import job_events

bridge = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
# Mounted inside the main app under /gpu (step_one); the app sets ROOT at startup.
ROOT = Path(os.environ.get('VOXMEET_BRIDGE_DATA', Path(__file__).resolve().parents[1] / 'data/gpu-bridge'))
KNOWN_FEATURES = ('multilingual','llm','live_asr','live_speakers','live_refine','shared_turns','flac_audio')


def atomic_write(target, text):
    """Unique temp file + retry: on Windows a concurrent reader or antivirus
    scan can briefly lock the target and make os.replace fail with WinError 5."""
    temp = target.with_name(f'{target.name}.{secrets.token_hex(4)}.part')
    temp.write_text(text)
    for attempt in range(10):
        try:
            temp.replace(target)
            return
        except PermissionError:
            if attempt == 9:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(.05 * (attempt + 1))


@bridge.get('/bridge-health')
async def bridge_health():
    from .gpu_connection import install_id
    return {'instance': install_id(), 'scheduler': 'fair-live-chat-v1', 'worker_readiness': 'preload-v2.5',
            'result_transport': 'long-record-v2.6', 'transport': 'single-port-v3'}


def authorized(sid, request):
    if not re.fullmatch('[0-9a-f]{32}', sid):
        raise HTTPException(404)
    folder = ROOT / sid
    try:
        config = json.loads((folder/'session.json').read_text())
    except (OSError, ValueError):
        raise HTTPException(404)
    header = request.headers.get('authorization', '')
    digest = hashlib.sha256(header.removeprefix('Bearer ').encode()).hexdigest()
    if not header.startswith('Bearer ') or not secrets.compare_digest(digest, config['token_hash']):
        raise HTTPException(401)
    if time.time() > config['expires'] or (folder/'closed').exists():
        raise HTTPException(410, 'Session expired or closed. Create a new session in the demo.')
    worker = config.get('worker_id')
    if worker and (ROOT / worker / 'closed').exists():
        raise HTTPException(410, 'Worker closed.')
    return folder


@bridge.post('/worker/{sid}/heartbeat')
async def heartbeat(sid: str, request: Request):
    folder = authorized(sid, request)
    if json.loads((folder/'session.json').read_text()).get('kind') != 'worker':
        raise HTTPException(403)
    body = await request.body()
    if body:
        if len(body) > 2048: raise HTTPException(413)
        try:
            payload = json.loads(body)
            features = payload.get('features', [])
            if not isinstance(features, list) or len(features) > 32 or any(not isinstance(f, str) for f in features): raise ValueError()
            # A newer worker may advertise capabilities this relay does not know.
            # Ignore them instead of rejecting the heartbeat (worker would look offline).
            features = [f for f in features if f in KNOWN_FEATURES]
            runtime=None
            if 'runtime_id' in payload:
                if not isinstance(payload['runtime_id'],str) or not re.fullmatch('[0-9a-f]{32}',payload['runtime_id']):raise ValueError()
                if payload.get('startup_state') not in ('warming','ready','error'):raise ValueError()
                if payload.get('assistant_state') not in ('warming','loading','ready','on_demand','error'):raise ValueError()
                if type(payload.get('gpu_count')) is not int or not 1<=payload['gpu_count']<=16:raise ValueError()
                if not isinstance(payload.get('worker_build'),str) or len(payload['worker_build'])>80:raise ValueError()
                runtime={key:payload[key] for key in ('runtime_id','startup_state','assistant_state','gpu_count','worker_build')}
                runtime['updated_at']=time.time()
                if runtime['startup_state']!='ready':features=[]
        except (ValueError, AttributeError,KeyError): raise HTTPException(400)
        (folder/'features.json').write_text(json.dumps(features))
        if runtime is not None:
            atomic_write(folder/'runtime.json', json.dumps(runtime))
        else:
            # An older worker rerun under the same session must not inherit a
            # previous process's ready flag.
            (folder/'runtime.json').unlink(missing_ok=True)
    (folder/'heartbeat').touch()
    return {'ok': True}


def pending_job(sid, folder, exclude='', deferred=(), lane='all'):
    index=folder/'pending'
    markers=list(index.iterdir()) if index.is_dir() else None
    candidates=[ROOT/p.name/'session.json' for p in markers if re.fullmatch('[0-9a-f]{32}',p.name)] if markers is not None else list(ROOT.glob('*/session.json'))
    pending=[]
    now=time.time()
    assistant_busy=False
    if exclude:
        try:
            active=json.loads((ROOT/exclude/'session.json').read_text())
            assistant_busy=active.get('worker_id')==sid and active.get('kind')=='llm'
        except (OSError,ValueError):pass
    for path in candidates:
        job=path.parent
        if job.name==exclude or job.name in deferred:continue
        if any((job/name).exists() for name in ('closed','result.json','error.json')):
            if markers is not None:(index/job.name).unlink(missing_ok=True)
            continue
        try:
            config=json.loads(path.read_text())
            if config.get('worker_id')==sid and config['expires']>time.time():
                kind=config.get('kind')
                if lane=='assistant' and kind!='llm':continue
                if lane=='audio' and kind=='llm':continue
                # The notebook supplies exclude while its assistant thread is busy.
                # Do not offer a second LLM job that it cannot accept: that would
                # make it poll the same job repeatedly and block caption intake.
                if assistant_busy and kind=='llm':continue
                created=path.stat().st_mtime
                # Continuous captions must not starve a waiting question. Once
                # aged, the oldest assistant request competes with new ASR jobs.
                # Translation is on GPU1: dispatch it immediately, then ASR
                # continues on GPU0 while the assistant thread translates.
                # An ASR job aged 2s receives the same priority to avoid starvation.
                urgent=(kind=='llm' and config.get('realtime_translation') is True) or (kind=='live_asr' and now-created>=2)
                priority=-1 if urgent else 0 if kind=='live_asr' or (kind=='llm' and now-created>=2) else 2 if kind=='live_refine' else 1
                pending.append((priority,created,job.name))
            elif markers is not None:(index/job.name).unlink(missing_ok=True)
        except (OSError,ValueError,KeyError):
            if markers is not None:(index/job.name).unlink(missing_ok=True)
    return min(pending)[2] if pending else None


@bridge.get('/worker/{sid}/next')
async def next_job(sid: str, request: Request, inline: bool=False, wait: float=0, exclude: str='', deferred: str='', lane: str='all'):
    if lane not in ('all','audio','assistant'):raise HTTPException(400)
    folder=authorized(sid,request)
    if json.loads((folder/'session.json').read_text()).get('kind')!='worker':raise HTTPException(403)
    if exclude and not re.fullmatch('[0-9a-f]{32}',exclude):raise HTTPException(400)
    deferred_ids=deferred.split(',') if deferred else []
    if len(deferred_ids)>20 or any(not re.fullmatch('[0-9a-f]{32}',value) for value in deferred_ids):raise HTTPException(400)
    # The worker snapshots its assistant-thread state into exclude. A long
    # poll keeps that stale snapshot after the thread finishes, adding 15s
    # between translations. Return promptly so it can refresh the snapshot.
    deadline=time.monotonic()+min(1 if exclude else 15,max(0,wait))
    while True:
        authorized(sid,request)
        (folder/'heartbeat').touch()
        # A completed result means the client's exclusion snapshot is stale.
        # End this poll, rather than keeping the next translation blocked.
        if exclude and any((ROOT/exclude/name).exists() for name in ('result.json','error.json')):
            return Response(status_code=204) if inline else {'job':None}
        job=pending_job(sid,folder,exclude,deferred_ids,lane)
        if job:
            # First delivery time, not GPU start time. This lets diagnostics
            # separate queue delay from delivery/model/return time.
            try:
                with (ROOT/job/'dispatch.json').open('x',encoding='utf-8') as dispatched:
                    json.dump({'first_dispatched_at':time.time()},dispatched)
            except FileExistsError:pass
            if inline:
                # Same authentication and worker ownership checks as job discovery.
                return FileResponse(ROOT/job/'job.zip',media_type='application/zip',
                    headers={'X-VoxMeet-Job':job,'Cache-Control':'no-store'})
            return {'job':job}
        if time.monotonic()>=deadline:
            return Response(status_code=204) if inline else {'job':None}
        # Woken immediately when the app publishes a job (same process);
        # the timeout keeps a periodic re-check as a safety net.
        await job_events.wait(.25)


@bridge.post('/exchange/{sid}/failed')
async def failed(sid: str, request: Request):
    folder = authorized(sid, request)
    # Accept only known error codes, never tracebacks, URLs or credentials.
    body=await request.body()
    if len(body)>512:raise HTTPException(413)
    try:code=json.loads(body).get('code','processing_failed') if body else 'processing_failed'
    except (ValueError,AttributeError):raise HTTPException(400)
    if code not in ERROR_MESSAGES:raise HTTPException(400)
    (folder/'error.json').write_text(json.dumps({'code':code,'error':ERROR_MESSAGES[code]}))
    job_events.notify()
    return {'received': True}


@bridge.post('/exchange/{sid}/progress')
async def progress(sid: str, request: Request):
    folder=authorized(sid,request)
    body=await request.body()
    if len(body)>1024:raise HTTPException(413)
    try:
        data=json.loads(body)
        stage=data['stage'];percent=data.get('progress')
        if stage not in ('loading_models','recognizing_speech','identifying_speakers','verifying_speakers','returning_result'):raise ValueError()
        if percent is not None and (type(percent) not in (int,float) or not 0<=percent<=100):raise ValueError()
    except (ValueError,KeyError,TypeError):raise HTTPException(400)
    atomic_write(folder/'progress.json', json.dumps(dict(stage=stage,progress=percent,updated_at=time.time())))
    config=json.loads((folder/'session.json').read_text())
    if config.get('worker_id'):(ROOT/config['worker_id']/'heartbeat').touch()
    return {'received':True}


@bridge.get('/exchange/{sid}/audio')
async def audio(sid: str, request: Request):
    folder = authorized(sid, request)
    return FileResponse(folder/'job.zip', media_type='application/zip')


@bridge.post('/exchange/{sid}/result')
async def result(sid: str, request: Request):
    folder = authorized(sid, request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_GPU_RESULT_BYTES:
            raise HTTPException(413)
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict): raise ValueError()
    except ValueError:
        raise HTTPException(400, 'Expected JSON object.')
    # No await after rechecking expiration and before exclusive write.
    authorized(sid, request)
    path = folder/'result.json'
    if path.exists():
        if path.read_bytes() == bytes(body): return {'received':True}
        raise HTTPException(409, 'A different result was already received.')
    partial = folder/'result.part'
    partial.write_bytes(body)
    partial.replace(path)
    (folder/'received.json').write_text(json.dumps({'received_at':time.time()}))
    job_events.notify()
    return {'received':True}
