"""Step 1: audio upload, English transcription and speaker attribution only."""
import asyncio
import contextlib
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import store
from .providers import capabilities, check_provider, friendly_error, normalize, transcribe
from .transcript import evidence_text
from .upload_engine import ProcessingCancelled
from .demo_features import draft_summary, import_transcript
import json
import copy
from .gpu_exchange import make_package, validate_result
from . import gpu_connection
from .meeting_qa import answer as answer_meeting_question
from .turn_editing import split_turn, word_boundaries

MAX_BYTES = 100 * 1024 * 1024
EXTENSIONS = {'.wav', '.mp3', '.m4a', '.mp4', '.flac', '.ogg', '.webm'}
ACTIVE = {'recording', 'queued', 'decoding', 'transcribing', 'cancelling'}
tasks = set()
locks = set()
cancel_events = {}
summary_locks = set()
qa_locks = set()


@asynccontextmanager
async def lifespan(app):
    store.init()
    app.state.queue = asyncio.Lock()
    app.state.gpu_lock = asyncio.Lock()
    yield
    gpu_connection.shutdown()
    pending = list(tasks)
    for event in cancel_events.values():
        event.set()
    # Native inference stops at phase boundaries. Wait before freeing resources
    # rather than cancelling the await and leaving a detached worker running.
    await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(title='VoxMeet · Step 1', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def local_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    host = request.headers.get('host')
    if origin and origin not in {f'http://{host}', f'https://{host}'}:
        return PlainTextResponse('Cross-origin access is disabled.', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if request.url.path == '/' or request.url.path.endswith(('.js', '.css')):
        response.headers['Cache-Control'] = 'no-cache'
    return response


def get(mid):
    item = store.get(mid)
    if item is None:
        raise HTTPException(404, 'Recording not found.')
    return item


def update(item, **changes):
    item.update(changes)
    store.save(item)


def launch(coroutine):
    task = asyncio.create_task(coroutine)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def public(item):
    # Do not expose server file paths, or future-step data, in this version.
    return {key: item.get(key) for key in ('id', 'title', 'created_at', 'status', 'error',
             'duration', 'segments', 'speakers', 'provider', 'filename', 'warning', 'revision',
             'stage', 'progress', 'processing_seconds', 'num_speakers', 'summary', 'summary_error', 'summary_status', 'provenance', 'gpu_session', 'qa', 'qa_status', 'qa_error', 'qa_pending')} | {
                 'has_audio': bool(item.get('audio') and Path(item['audio']).is_file())}


@app.get('/api/health')
async def health():
    caps = capabilities()
    return dict(status='ok', milestone=1, max_file_mb=100, max_minutes=30,
                **{key: caps[key] for key in ('local_installed', 'local_ready', 'local_models', 'llm_api_configured', 'ollama_model',
                   'deepgram_configured', 'ffmpeg_available', 'local_model')})


@app.get('/api/recordings')
async def recordings():
    return [public(item) for item in store.listing() if item.get('source') == 'upload']


@app.get('/api/recordings/{mid}')
async def recording(mid: str):
    return public(get(mid))


async def decode(mid):
    item = get(mid)
    try:
        async with app.state.queue:
            update(item, status='decoding', error=None)
            target = store.DATA / mid / 'audio.wav'
            duration = await asyncio.to_thread(normalize, Path(item['source_path']), target)
            update(item, status='uploaded', audio=str(target), duration=duration)
    except Exception as exc:
        update(item, status='error', error=friendly_error(exc))
    finally:
        locks.discard(mid)


@app.post('/api/upload', status_code=202)
async def upload(file: UploadFile):
    try:
        if not capabilities()['ffmpeg_available']:
            raise HTTPException(503, 'FFmpeg is missing. See Setup for installation instructions.')
        suffix = Path(file.filename or '').suffix.lower()
        if suffix not in EXTENSIONS:
            raise HTTPException(400, 'Choose a WAV, MP3, M4A, MP4, FLAC, OGG or WebM recording.')
        filename = (file.filename or 'Recording').replace('\\', '/').split('/')[-1][:200]
        item = store.create(Path(filename).stem[:150] or 'Recording', 'upload', 'local')
        folder = store.DATA / item['id']
        folder.mkdir(exist_ok=True)
        source = folder / ('source' + suffix)
        size = 0
        try:
            with source.open('wb') as output:
                while block := await file.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_BYTES:
                        raise ValueError('Maximum file size is 100 MB.')
                    output.write(block)
            if not size:
                raise ValueError('The recording is empty.')
        except Exception as exc:
            source.unlink(missing_ok=True)
            update(item, status='error', error=friendly_error(exc))
            raise HTTPException(400, item['error'])
        update(item, filename=filename, source_path=str(source), status='queued')
        locks.add(item['id'])
        launch(decode(item['id']))
        return public(item)
    finally:
        await file.close()


class TranscribeRequest(BaseModel):
    provider: Literal['local', 'deepgram'] = 'local'
    num_speakers: int | None = Field(default=None, ge=1, le=12)


async def run_transcription(mid):
    item = get(mid)
    cancel = cancel_events[mid]
    started = time.monotonic()
    loop = asyncio.get_running_loop()
    done = False
    last_update = 0.0

    def save_progress(stage, percentage):
        nonlocal last_update
        if done:
            return
        now = time.monotonic()
        if now - last_update < .5 and stage == item.get('stage'):
            return
        last_update = now
        update(item, stage=stage, progress=percentage, processing_seconds=round(now - started, 1),
               status='cancelling' if cancel.is_set() else 'transcribing')

    def report(stage, percentage):
        loop.call_soon_threadsafe(save_progress, stage, percentage)

    try:
        async with app.state.queue:
            if cancel.is_set():
                raise ProcessingCancelled()
            update(item, status='transcribing', stage='loading_models', progress=None, error=None, warning=None)
            segments = await transcribe(Path(item['audio']), item['provider'], num_speakers=item.get('num_speakers'),
                                        report=report, cancel=cancel)
            if cancel.is_set():
                raise ProcessingCancelled()
            # Keep unassigned speech visible rather than inventing a speaker.
            item['speakers'] = {}
            store.replace_segments(item, segments, final=True)
            warning = None
            if not segments:
                warning = 'No speech was detected in this recording.'
            elif any(segment.get('speaker') is None for segment in segments):
                warning = 'Some speech could not be assigned to a speaker. Those turns are marked Unassigned.'
            done = True
            update(item, status='ready', stage='complete', progress=100, warning=warning, provenance=None,
                   processing_seconds=round(time.monotonic() - started, 1))
    except ProcessingCancelled:
        done = True
        update(item, status='cancelled', stage='cancelled', progress=None,
               error=None, warning='Processing cancelled. Your audio and any earlier transcript are saved.')
    except Exception as exc:
        done = True
        update(item, status='error', error=friendly_error(exc))
    finally:
        done = True
        locks.discard(mid)
        cancel_events.pop(mid, None)


@app.post('/api/recordings/{mid}/transcribe', status_code=202)
async def start(mid: str, body: TranscribeRequest):
    item = get(mid)
    if mid in locks or (mid in summary_locks or mid in qa_locks) or item['status'] in ACTIVE:
        raise HTTPException(409, 'This recording is already being processed.')
    if not item.get('audio') or not Path(item['audio']).is_file():
        raise HTTPException(400, 'Upload and prepare a valid recording first.')
    if body.provider == 'deepgram' and body.num_speakers is not None:
        raise HTTPException(400, 'A known speaker count is supported only in local mode.')
    try:
        check_provider(body.provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    update(item, provider=body.provider, num_speakers=body.num_speakers, status='queued',
           stage='queued', progress=None, processing_seconds=0, error=None, warning=None)
    locks.add(mid)
    cancel_events[mid] = threading.Event()
    launch(run_transcription(mid))
    return public(item)


@app.post('/api/recordings/{mid}/cancel', status_code=202)
async def cancel_transcription(mid: str):
    item = get(mid)
    event = cancel_events.get(mid)
    if event is None:
        raise HTTPException(409, 'There is no transcription to cancel.')
    event.set()
    update(item, status='cancelling')
    return public(item)


@app.post('/api/recordings/{mid}/prepare', status_code=202)
async def retry_decode(mid: str):
    item = get(mid)
    if mid in locks or item['status'] in ACTIVE:
        raise HTTPException(409, 'This recording is already being processed.')
    if not item.get('source_path') or not Path(item['source_path']).is_file():
        raise HTTPException(400, 'No source audio was saved. Upload the file again.')
    update(item, status='queued', error=None)
    locks.add(mid)
    launch(decode(mid))
    return public(item)


@app.get('/api/recordings/{mid}/audio')
async def audio(mid: str):
    item = get(mid)
    if not item.get('audio') or not Path(item['audio']).is_file():
        raise HTTPException(404, 'Audio is not ready.')
    return FileResponse(item['audio'], media_type='audio/wav')


@app.get('/api/recordings/{mid}/export')
async def export(mid: str):
    item = get(mid)
    if item['status'] != 'ready':
        raise HTTPException(409, 'Finish transcription before exporting.')
    text = f"{item['title']}\nEnglish · speaker-labelled transcript\n\n"
    text += evidence_text(item['segments'], item['speakers'])
    return PlainTextResponse(text, headers={'Content-Disposition': f'attachment; filename="transcript-{mid}.txt"'})


class EditTurn(BaseModel):
    revision: int
    text: str = Field(min_length=1, max_length=12000)
    speaker: str | None = None


@app.patch('/api/recordings/{mid}/segments/{sid}')
async def edit_turn(mid: str, sid: str, body: EditTurn):
    item = get(mid)
    if mid in locks or item['status'] in ACTIVE or item['revision'] != body.revision:
        raise HTTPException(409, 'Transcript changed or is processing. Reopen it and retry.')
    if body.speaker is not None and body.speaker not in item['speakers']:
        raise HTTPException(400, 'Choose an existing speaker or Unassigned.')
    if not body.text.strip():
        raise HTTPException(400, 'Text cannot be blank.')
    segment = next((s for s in item['segments'] if s['id'] == sid), None)
    if segment is None:
        raise HTTPException(404, 'Turn not found.')
    segment.setdefault('original_text', segment['text'])
    segment.setdefault('original_speaker', segment['speaker'])
    segment.update(text=body.text.strip(), speaker=body.speaker, edited=True)
    update(item, revision=item['revision'] + 1, summary=None, messages=[], qa=[], summary_error=None)
    return public(item)


@app.post('/api/import-transcript', status_code=201)
async def import_recording(file: UploadFile):
    try:
        raw = await file.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError('Maximum JSON size is 5 MB.')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('Expected a JSON object.')
        segments, speakers = import_transcript(payload)
        title = str(payload.get('meeting') or 'Imported Colab transcript')[:150]
        item = store.create(title, 'upload', 'local')
        update(item, segments=segments, speakers=speakers, revision=1, status='ready',
               duration=max(s['end'] for s in segments),
               warning='Imported transcript from Colab. Audio is not attached; speaker labels and words are unverified.',
               filename=file.filename)
        return public(item)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        await file.close()


class SummaryRequest(BaseModel):
    provider: Literal['ollama', 'api'] = 'ollama'


async def run_summary(mid, snapshot, provider):
    try:
        async with app.state.queue:
            result = await draft_summary(snapshot, provider)
        current = get(mid)
        if current['revision'] != snapshot['revision']:
            update(current, summary_status='error', summary_error='Transcript changed. Generate a new draft from your edits.')
        else:
            update(current, summary=result, summary_status='ready', summary_error=None)
    except Exception as exc:
        current = get(mid)
        update(current, summary_status='error', summary_error=friendly_error(exc))
    finally:
        summary_locks.discard(mid)


@app.post('/api/recordings/{mid}/summary', status_code=202)
async def create_summary(mid: str, body: SummaryRequest):
    item = get(mid)
    if mid in locks or (mid in summary_locks or mid in qa_locks) or item['status'] in ACTIVE:
        raise HTTPException(409, 'This recording is already processing.')
    if not item['segments']:
        raise HTTPException(400, 'Create or import a transcript first.')
    if body.provider == 'api' and not capabilities()['llm_api_configured']:
        raise HTTPException(400, 'Configure LLM_API_URL, LLM_API_KEY and LLM_API_MODEL, then restart.')
    update(item, summary_status='processing', summary_error=None)
    summary_locks.add(mid)
    launch(run_summary(mid, copy.deepcopy(item), body.provider))
    return public(item)


@app.get('/api/recordings/{mid}/summary/export')
async def export_summary(mid: str):
    item = get(mid)
    summary = item.get('summary')
    if not summary or summary['revision'] != item['revision']:
        raise HTTPException(409, 'Generate a summary for the current transcript first.')
    lines = [item['title'], summary['label'], '']
    for section in summary['sections']:
        for b in section['bullets']:
            lines.append('- ' + b['text'] + ' [' + ', '.join(b['ids']) + ']')
    return PlainTextResponse('\n'.join(lines), headers={'Content-Disposition': f'attachment; filename="summary-{mid}.txt"'})



@app.get('/api/gpu-notebook')
async def gpu_notebook():
    return FileResponse(store.ROOT / 'colab/voxmeet_gpu.ipynb',
                        filename='voxmeet_gpu.ipynb', media_type='application/x-ipynb+json')


@app.get('/api/recordings/{mid}/gpu-package')
async def gpu_package(mid: str):
    item = get(mid)
    if mid in locks or (mid in summary_locks or mid in qa_locks) or item['status'] in ACTIVE:
        raise HTTPException(409, 'Wait for current processing to finish.')
    if not item.get('audio') or not Path(item['audio']).is_file():
        raise HTTPException(400, 'Prepare audio first.')
    target = store.DATA / mid / 'gpu-job.zip'
    target.parent.mkdir(parents=True, exist_ok=True)
    # No await while packaging: prevents mutation of this recording during export.
    make_package(item, target)
    return FileResponse(target, filename=f'voxmeet-{mid}.zip', media_type='application/zip')


def apply_gpu_result(item, raw):
    mid = item['id']
    segments, speakers, provenance = validate_result(json.loads(raw), item)
    # Preserve the full previous transcript before replacement.
    backup = store.DATA / mid / f'transcript-before-gpu-r{item["revision"]}-{time.time_ns()}.json'
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
    (backup.parent / f'gpu-result-r{item["revision"] + 1}.json').write_bytes(raw)
    update(item, segments=segments, speakers=speakers, provenance=provenance,
           revision=item['revision'] + 1, status='ready', stage='complete', progress=100,
           summary=None, summary_status=None, summary_error=None, messages=[], qa=[], error=None,
           warning='Whisper + Community-1 transcript. Review speaker changes and unclear words before summarizing.')


@app.post('/api/recordings/{mid}/gpu-result')
async def gpu_result(mid: str, file: UploadFile):
    try:
        raw = await file.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError('Maximum result size is 5 MB.')
        item = get(mid)
        if mid in locks or (mid in summary_locks or mid in qa_locks) or item['status'] in ACTIVE:
            raise HTTPException(409, 'Wait for current processing to finish.')
        if not item.get('audio') or not Path(item['audio']).is_file():
            raise ValueError('Prepare audio first.')
        apply_gpu_result(item, raw)
        return public(item)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        await file.close()



@app.post('/api/recordings/{mid}/gpu-session', status_code=201)
async def create_gpu_session(mid: str):
    async with app.state.gpu_lock:
        item = get(mid)
        if mid in locks or (mid in summary_locks or mid in qa_locks) or item['status'] in ACTIVE:
            raise HTTPException(409, 'Wait for current processing to finish.')
        if not item.get('audio') or not Path(item['audio']).is_file():
            raise HTTPException(400, 'Prepare audio first.')
        locks.add(mid)
        try:
            url = await asyncio.to_thread(gpu_connection.connect)
            sid = gpu_connection.prepare(item, url)
            old = item.get('gpu_session', {}).get('id')
            if old:
                folder = store.DATA/'gpu-bridge'/old
                if folder.is_dir(): (folder/'closed').touch()
            update(item, gpu_session=dict(id=sid,status='waiting',error=None))
            return public(item)
        except ValueError as exc:
            raise HTTPException(400,str(exc))
        finally:
            locks.discard(mid)


@app.get('/api/recordings/{mid}/connected-notebook')
async def connected_notebook(mid: str):
    item = get(mid)
    sid = (item.get('gpu_session') or {}).get('id')
    if not sid: raise HTTPException(404,'Create a GPU session first.')
    return FileResponse(store.DATA/'gpu-bridge'/sid/'connected.ipynb',
                        filename=f'voxmeet-connected-{mid}.ipynb',
                        headers={'Cache-Control':'no-store'})


@app.post('/api/recordings/{mid}/gpu-session/check')
async def check_gpu_session(mid: str):
    item = get(mid)
    session = item.get('gpu_session') or {}
    if session.get('status') != 'waiting': return public(item)
    if mid in locks or (mid in summary_locks or mid in qa_locks) or item['status'] in ACTIVE:
        return public(item)
    folder = store.DATA/'gpu-bridge'/session['id']
    try:
        if (folder/'result.json').is_file():
            apply_gpu_result(item,(folder/'result.json').read_bytes())
            session.update(status='applied',error=None)
            (folder/'closed').touch()
        else:
            config = json.loads((folder/'session.json').read_text())
            if time.time() > config['expires']:
                session.update(status='expired',error='Transfer expired. Create a new GPU session.')
    except (ValueError, OSError) as exc:
        session.update(status='error',error=str(exc) if isinstance(exc,ValueError) else 'GPU session files are unavailable. Create a new session.')
        if folder.is_dir(): (folder/'closed').touch()
    update(item,gpu_session=session)
    return public(item)



@app.get('/api/recordings/{mid}/segments/{sid}/boundaries')
async def turn_boundaries(mid: str, sid: str):
    item = get(mid)
    segment = next((s for s in item['segments'] if s['id'] == sid), None)
    if segment is None: raise HTTPException(404, 'Turn not found.')
    # Recover word timings from the retained GPU result for existing imports.
    if not segment.get('words') and not segment.get('edited'):
        for path in sorted((store.DATA/mid).glob('gpu-result-r*.json'), reverse=True):
            payload = json.loads(path.read_text(encoding='utf-8'))
            original = next((s for s in payload.get('utterances', []) if s['id']==sid),None)
            if original and original['text']==segment['text'] and original['start']==segment['start'] and original['end']==segment['end']:
                segment['words'] = original.get('words', [])
                break
    return dict(revision=item['revision'], boundaries=word_boundaries(segment))


class SplitTurnRequest(BaseModel):
    revision: int
    offset: int = Field(gt=0)
    at: float
    left_speaker: str | None = None
    right_speaker: str | None = None


@app.post('/api/recordings/{mid}/segments/{sid}/split')
async def split_segment(mid: str, sid: str, body: SplitTurnRequest):
    item = get(mid)
    if mid in locks or item['status'] in ACTIVE or item['revision'] != body.revision:
        raise HTTPException(409, 'Transcript changed or is processing. Reopen it and retry.')
    if any(label is not None and label not in item['speakers'] for label in (body.left_speaker,body.right_speaker)):
        raise HTTPException(400,'Choose existing speaker labels.')
    index = next((i for i,s in enumerate(item['segments']) if s['id']==sid),None)
    if index is None: raise HTTPException(404,'Turn not found.')
    try:
        parts = split_turn(item['segments'][index],body.offset,body.at,body.left_speaker,body.right_speaker)
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    backup = store.DATA/mid/f'transcript-before-split-r{item["revision"]}-{time.time_ns()}.json'
    backup.parent.mkdir(parents=True,exist_ok=True)
    backup.write_text(json.dumps(item,ensure_ascii=False),encoding='utf-8')
    item['segments'][index:index+1]=parts
    update(item,revision=item['revision']+1,summary=None,messages=[], qa=[],summary_error=None)
    return public(item)



class QuestionRequest(BaseModel):
    question: str = Field(min_length=1,max_length=1000)
    provider: Literal['ollama','api'] = 'ollama'


async def run_question(mid, snapshot, question, provider):
    try:
        async with app.state.queue:
            result = await answer_meeting_question(snapshot,question,provider)
        current=get(mid)
        if current['revision'] != snapshot['revision']:
            update(current,qa_status='error',qa_error='Transcript changed. Ask again using the corrected transcript.')
        else:
            entry=dict(question=question,revision=current['revision'],provider=provider,**result)
            update(current,qa_status='ready',qa_error=None,qa_pending=None,qa=(current.get('qa') or [])+[entry])
    except Exception as exc:
        update(get(mid),qa_status='error',qa_error=friendly_error(exc))
    finally:
        qa_locks.discard(mid)


@app.post('/api/recordings/{mid}/questions',status_code=202)
async def ask_question(mid: str, body: QuestionRequest):
    item=get(mid)
    if mid in locks or mid in summary_locks or mid in qa_locks or item['status'] in ACTIVE:
        raise HTTPException(409,'Wait for current processing to finish.')
    if not item['segments'] or not body.question.strip():
        raise HTTPException(400,'Create a transcript and enter a question first.')
    if body.provider=='api' and not capabilities()['llm_api_configured']:
        raise HTTPException(400,'Configure the language model API first.')
    qa_locks.add(mid)
    update(item,qa_status='processing',qa_error=None,qa_pending=body.question.strip())
    launch(run_question(mid,copy.deepcopy(item),body.question.strip(),body.provider))
    return public(item)


from .live import router as live_router
app.include_router(live_router)

WEB = store.ROOT / 'web/step-one'
app.mount('/', StaticFiles(directory=WEB, html=True), name='step-one')
