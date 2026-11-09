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

bridge = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
ROOT = Path(os.environ.get('VOXMEET_BRIDGE_DATA', Path(__file__).resolve().parents[1] / 'data/gpu-bridge'))


@bridge.get('/bridge-health')
async def bridge_health():
    return {'instance': os.environ.get('VOXMEET_BRIDGE_INSTANCE', '')}


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
            features = json.loads(body).get('features', [])
            if not isinstance(features, list) or any(f not in ('multilingual','llm','live_asr','live_speakers','live_refine','shared_turns') for f in features): raise ValueError()
        except (ValueError, AttributeError): raise HTTPException(400)
        (folder/'features.json').write_text(json.dumps(features))
    (folder/'heartbeat').touch()
    return {'ok': True}


def pending_job(sid, folder, exclude=''):
    index=folder/'pending'
    markers=list(index.iterdir()) if index.is_dir() else None
    candidates=[ROOT/p.name/'session.json' for p in markers if re.fullmatch('[0-9a-f]{32}',p.name)] if markers is not None else list(ROOT.glob('*/session.json'))
    pending=[]
    for path in candidates:
        job=path.parent
        if job.name==exclude:continue
        if any((job/name).exists() for name in ('closed','result.json','error.json')):
            if markers is not None:(index/job.name).unlink(missing_ok=True)
            continue
        try:
            config=json.loads(path.read_text())
            if config.get('worker_id')==sid and config['expires']>time.time():
                priority=0 if config.get('kind')=='live_asr' else 2 if config.get('kind')=='live_refine' else 1
                pending.append((priority,path.stat().st_mtime,job.name))
            elif markers is not None:(index/job.name).unlink(missing_ok=True)
        except (OSError,ValueError,KeyError):
            if markers is not None:(index/job.name).unlink(missing_ok=True)
    return min(pending)[2] if pending else None


@bridge.get('/worker/{sid}/next')
async def next_job(sid: str, request: Request, inline: bool=False, wait: float=0, exclude: str=''):
    folder=authorized(sid,request)
    if json.loads((folder/'session.json').read_text()).get('kind')!='worker':raise HTTPException(403)
    if exclude and not re.fullmatch('[0-9a-f]{32}',exclude):raise HTTPException(400)
    deadline=time.monotonic()+min(15,max(0,wait))
    while True:
        authorized(sid,request)
        (folder/'heartbeat').touch()
        job=pending_job(sid,folder,exclude)
        if job:
            if inline:
                # Same authentication and worker ownership checks as job discovery.
                return FileResponse(ROOT/job/'job.zip',media_type='application/zip',
                    headers={'X-VoxMeet-Job':job,'Cache-Control':'no-store'})
            return {'job':job}
        if time.monotonic()>=deadline:
            return Response(status_code=204) if inline else {'job':None}
        await asyncio.sleep(.1)


@bridge.post('/exchange/{sid}/failed')
async def failed(sid: str, request: Request):
    folder = authorized(sid, request)
    # Do not accept arbitrary traceback text or secrets from remote runtimes.
    (folder/'error.json').write_text(json.dumps({'error': 'Community-1 worker failed. Check the notebook output, then retry.'}))
    return {'received': True}


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
        if len(body) > 5 * 1024 * 1024:
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
    return {'received':True}
