import asyncio
import contextlib
import json
import os
import wave
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from websockets.asyncio.client import connect as ws_connect

from . import store
from .intelligence import answer_question, summarize
from .providers import capabilities, check_provider, friendly_error, normalize, transcribe
from .transcript import evidence_text, group_words, stabilize

jobs = set()
busy = set()
MAX_BYTES = 250 * 1024 * 1024
ALLOWED_PROVIDERS = {'local', 'deepgram'}
ALLOWED_LLM = {'ollama', 'api'}


@asynccontextmanager
async def lifespan(app):
    store.init()
    yield
    for task in jobs:
        task.cancel()
    await asyncio.gather(*jobs, return_exceptions=True)


app = FastAPI(title='VoxMeet — English Meeting Assistant', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


def origin_allowed(origin, host):
    return not origin or origin in {f'http://{host}', f'https://{host}'}


@app.middleware('http')
async def same_origin(request: Request, call_next):
    if not origin_allowed(request.headers.get('origin'), request.headers.get('host')):
        return PlainTextResponse('Cross-origin requests are disabled.', status_code=403)
    return await call_next(request)


def meeting(mid):
    result = store.get(mid)
    if result is None:
        raise HTTPException(404, 'Meeting not found.')
    return result


def available(mid):
    item = meeting(mid)
    if mid in busy or item['status'] in ('recording', 'processing'):
        raise HTTPException(409, 'Wait until this meeting finishes processing.')
    return item


def validate_provider(provider):
    try:
        check_provider(provider)
    except ValueError as e:
        raise HTTPException(400, str(e))


def launch(coroutine):
    task = asyncio.create_task(coroutine)
    jobs.add(task)
    task.add_done_callback(jobs.discard)


@app.get('/api/health')
async def health():
    return {'status': 'ok', **capabilities()}


@app.get('/api/meetings')
async def list_meetings():
    return store.listing()


@app.get('/api/meetings/{mid}')
async def get_meeting(mid: str):
    return meeting(mid)


class NewMeeting(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    provider: str = 'local'


@app.post('/api/meetings')
async def new_meeting(body: NewMeeting):
    validate_provider(body.provider)
    return store.create(body.title.strip() or 'Untitled meeting', 'live', body.provider)


async def auto_summarize(item, llm):
    if llm not in ALLOWED_LLM or not item['segments']:
        return
    try:
        item['summary'] = await summarize(item, llm)
        item['auto_summary_error'] = None
    except Exception as e:
        item['auto_summary_error'] = friendly_error(e)
    store.save(item)


async def process_file(mid, source, llm=None):
    item = meeting(mid)
    busy.add(mid)
    item.update(status='processing', error=None)
    store.save(item)
    try:
        target = store.DATA / mid / 'audio.wav'
        if source != target:
            item['duration'] = await asyncio.to_thread(normalize, source, target)
        item['audio'] = str(target)
        store.save(item)
        segments = await transcribe(target, item['provider'])
        segments = stabilize(segments, item['segments'])
        store.replace_segments(item, segments, final=True)
        item['status'] = 'ready'
        store.save(item)
        await auto_summarize(item, llm)
    except Exception as e:
        item.update(status='error', error=friendly_error(e))
        store.save(item)
    finally:
        busy.discard(mid)


@app.post('/api/upload')
async def upload(file: UploadFile = File(...), title: str = Form('Imported meeting'),
                 provider: str = Form('local'), llm: str = Form('none')):
    validate_provider(provider)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.wav', '.mp3', '.m4a', '.mp4', '.flac', '.ogg', '.webm'}:
        raise HTTPException(400, 'Choose a WAV, MP3, M4A, MP4, FLAC, OGG or WebM file.')
    item = store.create(title[:150].strip() or 'Imported meeting', 'upload', provider)
    folder = store.DATA / item['id']
    folder.mkdir()
    source = folder / ('source' + suffix)
    total = 0
    try:
        with source.open('wb') as output:
            while block := await file.read(1024 * 1024):
                total += len(block)
                if total > MAX_BYTES:
                    raise ValueError('Maximum file size is 250 MB.')
                output.write(block)
        if not total:
            raise ValueError('The uploaded file is empty.')
    except Exception as e:
        source.unlink(missing_ok=True)
        item.update(status='error', error=friendly_error(e))
        store.save(item)
        raise HTTPException(400, item['error'])
    finally:
        await file.close()
    item.update(status='processing', source_path=str(source), llm=llm)
    store.save(item)
    launch(process_file(item['id'], source, llm))
    return item


@app.post('/api/meetings/{mid}/retry')
async def retry(mid: str):
    item = available(mid)
    validate_provider(item['provider'])
    source = Path(item.get('audio') or item.get('source_path') or '')
    if not source.is_file():
        raise HTTPException(400, 'No saved audio. Please import or record again.')
    item['status'] = 'processing'
    store.save(item)
    launch(process_file(mid, source, item.get('llm')))
    return {'status': 'processing'}


@app.get('/api/meetings/{mid}/audio')
async def audio(mid: str):
    item = meeting(mid)
    if not item['audio'] or not Path(item['audio']).is_file():
        raise HTTPException(404, 'Audio is not ready.')
    return FileResponse(item['audio'], media_type='audio/wav')


class EditSegment(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@app.patch('/api/meetings/{mid}/segments/{sid}')
async def edit_segment(mid: str, sid: str, body: EditSegment):
    item = available(mid)
    segment = next((s for s in item['segments'] if s['id'] == sid), None)
    if not segment:
        raise HTTPException(404, 'Segment not found.')
    if not body.text.strip():
        raise HTTPException(400, 'Transcript text cannot be blank.')
    segment['text'] = body.text.strip()
    item['revision'] += 1
    item['summary'], item['messages'] = None, []
    store.save(item)
    return item


class Name(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@app.patch('/api/meetings/{mid}/speakers/{speaker}')
async def rename(mid: str, speaker: str, body: Name):
    item = available(mid)
    if speaker not in item['speakers']:
        raise HTTPException(404, 'Speaker not found.')
    item['speakers'][speaker] = body.name.strip() or f'Speaker {int(speaker)+1}'
    item['revision'] += 1
    item['summary'], item['messages'] = None, []
    store.save(item)
    return item


class IntelligenceRequest(BaseModel):
    provider: str = 'ollama'
    question: str = Field(default='', max_length=2000)


@app.post('/api/meetings/{mid}/summary')
async def summary(mid: str, body: IntelligenceRequest):
    item = available(mid)
    busy.add(mid)
    try:
        item['summary'] = await summarize(item, body.provider)
        item['auto_summary_error'] = None
        store.save(item)
        return item['summary']
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    finally:
        busy.discard(mid)


@app.post('/api/meetings/{mid}/chat')
async def question(mid: str, body: IntelligenceRequest):
    item = available(mid)
    if not item['segments'] or not body.question.strip():
        raise HTTPException(400, 'A completed transcript and a question are required.')
    busy.add(mid)
    try:
        answer = await answer_question(item, body.question.strip(), body.provider)
        item['messages'].extend([dict(role='user', text=body.question.strip()), dict(role='assistant', **answer)])
        store.save(item)
        return answer
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    finally:
        busy.discard(mid)


@app.get('/api/meetings/{mid}/export')
async def export(mid: str):
    item = meeting(mid)
    text = item['title'] + '\nEnglish transcript\n\n' + evidence_text(item['segments'], item['speakers'])
    if item['summary']:
        text += '\n\nSummary\n' + item['summary']['text']
    return PlainTextResponse(text, headers={'Content-Disposition': f'attachment; filename="meeting-{mid}.txt"'})


def pcm_snapshot(raw_path, target):
    with wave.open(str(target), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        with raw_path.open('rb') as source:
            while block := source.read(65536):
                wav.writeframesraw(block)


@app.websocket('/api/live/{mid}')
async def live(ws: WebSocket, mid: str):
    if not origin_allowed(ws.headers.get('origin'), ws.headers.get('host')):
        await ws.close(code=1008)
        return
    item = store.get(mid)
    if not item or item['status'] != 'new' or mid in busy:
        await ws.close(code=1008)
        return
    await ws.accept()
    folder = store.DATA / mid
    folder.mkdir(exist_ok=True)
    raw_path, wav_path = folder / 'live.pcm', folder / 'audio.wav'
    item.update(status='recording', audio=str(wav_path))
    busy.add(mid)
    store.save(item)
    stopped = asyncio.Event()
    remote = None
    worker = None
    received = 0
    completed = False
    llm = 'none'

    async def send(payload):
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await ws.send_json(payload)

    async def remote_results():
        async for raw in remote:
            result = json.loads(raw)
            if result.get('type') == 'Error':
                raise ValueError('Streaming provider reported an error. Check your account and retry.')
            if result.get('type') != 'Results':
                continue
            words = result.get('channel', {}).get('alternatives', [{}])[0].get('words', [])
            segments = group_words(words)
            if result.get('is_final'):
                for segment in segments:
                    segment['id'] = f"s{len(item['segments'])+1}"
                    segment['original_text'] = segment['text']
                    segment['provisional'] = False
                    item['segments'].append(segment)
                    if segment['speaker'] is not None:
                        label = segment['speaker']
                        item['speakers'].setdefault(label, f'Speaker {int(label)+1}')
                item['revision'] += 1
                store.save(item)
                await send(dict(type='transcript', meeting=item, interim=[]))
            else:
                await send(dict(type='interim', segments=segments))

    async def local_results():
        last_size = 0
        while True:
            try:
                await asyncio.wait_for(stopped.wait(), timeout=8)
            except asyncio.TimeoutError:
                pass
            size = raw_path.stat().st_size
            if size > last_size and size >= 32000:
                snapshot = folder / 'snapshot.wav'
                # Snapshot while the event loop is paused: writer cannot race the copy.
                pcm_snapshot(raw_path, snapshot)
                await send(dict(type='status', message='Analyzing voices locally… speaker labels may be revised.'))
                segments = await transcribe(snapshot, 'local')
                segments = stabilize(segments, item['segments'])
                store.replace_segments(item, segments, final=stopped.is_set() and raw_path.stat().st_size == size)
                last_size = size
                await send(dict(type='transcript', meeting=item, interim=[]))
            if stopped.is_set() and raw_path.stat().st_size == last_size:
                return
            if stopped.is_set() and size < 32000:
                return

    try:
        config = await asyncio.wait_for(ws.receive_json(), 15)
        if config.get('sample_rate') != 16000:
            raise ValueError('The recording must use 16 kHz mono PCM audio.')
        llm = config.get('llm', 'none')
        check_provider(item['provider'])
        if item['provider'] == 'deepgram':
            remote = await ws_connect(
                'wss://api.deepgram.com/v1/listen?model=nova-3&language=en&diarize_model=v1'
                '&punctuate=true&interim_results=true&encoding=linear16&sample_rate=16000&channels=1&endpointing=300',
                additional_headers={'Authorization': f"Token {os.environ['DEEPGRAM_API_KEY']}"},
                open_timeout=20)
        with raw_path.open('wb', buffering=0) as output:
            worker = asyncio.create_task(remote_results() if remote else local_results())
            await send(dict(type='ready'))
            while True:
                # Detect provider/model failure even while the client is silent.
                receiver = asyncio.create_task(ws.receive())
                done, _ = await asyncio.wait({receiver, worker}, return_when=asyncio.FIRST_COMPLETED)
                if worker in done:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                    await worker
                    raise ValueError('The transcription stream closed unexpectedly. Saved audio can be retried.')
                message = receiver.result()
                if message['type'] == 'websocket.disconnect':
                    raise WebSocketDisconnect()
                if message.get('bytes'):
                    block = message['bytes']
                    received += len(block)
                    limit = 16000 * 2 * (600 if item['provider'] == 'local' else 3600)
                    if len(block) % 2 or len(block) > 65536:
                        raise ValueError('Invalid PCM audio frame.')
                    if received > limit:
                        raise ValueError('Recording limit reached (local: 10 minutes; API: 60 minutes). Retry saved audio.')
                    output.write(block)
                    item['duration'] = received / 32000
                    if remote:
                        await remote.send(block)
                elif message.get('text'):
                    control = json.loads(message['text'])
                    if control.get('type') == 'stop':
                        completed = True
                        break
        item['status'] = 'processing'
        store.save(item)
        await send(dict(type='status', message='Finalizing the transcript…'))
        stopped.set()
        if remote:
            await remote.send(json.dumps({'type': 'CloseStream'}))
            await asyncio.wait_for(worker, timeout=30)
        else:
            await worker
        pcm_snapshot(raw_path, wav_path)
        for segment in item['segments']:
            segment['provisional'] = False
        item['status'] = 'ready'
        store.save(item)
        await send(dict(type='transcript', meeting=item, interim=[]))
        if llm in ALLOWED_LLM:
            await send(dict(type='status', message='Preparing your meeting summary…'))
            await auto_summarize(item, llm)
        await send(dict(type='complete', meeting=item))
    except Exception as e:
        item.update(status='interrupted' if isinstance(e, WebSocketDisconnect) else 'error',
                    error='Recording connection lost. Retry saved audio.' if isinstance(e, WebSocketDisconnect) else friendly_error(e))
        store.save(item)
        await send(dict(type='error', message=item['error']))
    finally:
        stopped.set()
        if worker and not worker.done():
            worker.cancel()
        if worker:
            await asyncio.gather(worker, return_exceptions=True)
        if remote:
            await remote.close()
        if raw_path.exists():
            pcm_snapshot(raw_path, wav_path)
        busy.discard(mid)
        with contextlib.suppress(Exception):
            await ws.close()


app.mount('/', StaticFiles(directory=store.ROOT / 'web', html=True), name='web')
