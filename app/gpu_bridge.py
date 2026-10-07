"""Single-recording, expiring exchange service. Never exposes the main app."""
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

bridge = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
ROOT = Path(os.environ.get('VOXMEET_BRIDGE_DATA', Path(__file__).resolve().parents[1] / 'data/gpu-bridge'))


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
    return folder


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
