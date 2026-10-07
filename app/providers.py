import asyncio
import importlib.util
import os
import shutil
import subprocess
import wave
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .store import ROOT
from .local_models import model_status
from .transcript import group_words

load_dotenv(ROOT / '.env')


def ffmpeg_path():
    configured = os.getenv('FFMPEG_PATH')
    if configured:
        return configured if Path(configured).is_file() else (shutil.which(configured) or '')
    installed = shutil.which('ffmpeg')
    bundled = ROOT.parent / 'frontend/src-tauri/binaries/ffmpeg-x86_64-pc-windows-msvc.exe'
    return installed or (str(bundled) if bundled.is_file() else '')


def capabilities():
    try:
        local = bool(importlib.util.find_spec('faster_whisper') and importlib.util.find_spec('sherpa_onnx'))
    except ModuleNotFoundError:
        local = False
    models = model_status()
    return dict(local_installed=local, local_models=models, local_ready=local and all(models.values()),
                hf_configured=bool(os.getenv('HF_TOKEN')),
                deepgram_configured=bool(os.getenv('DEEPGRAM_API_KEY')),
                llm_api_configured=all(os.getenv(k) for k in ('LLM_API_URL', 'LLM_API_KEY', 'LLM_API_MODEL')),
                ffmpeg_available=bool(ffmpeg_path()), ollama_model=os.getenv('OLLAMA_MODEL', 'qwen2.5:3b'),
                local_model='base.en')


def check_provider(provider):
    caps = capabilities()
    if provider == 'deepgram':
        if not caps['deepgram_configured']:
            raise ValueError('Set DEEPGRAM_API_KEY in .env and restart the server to use API transcription.')
    elif provider == 'local':
        if not caps['local_installed']:
            raise ValueError('Run setup.ps1 -LocalModels to install local transcription and speaker recognition.')
        if not caps['local_ready']:
            raise ValueError('Local models are missing. Run setup.ps1 -LocalModels to download them. No API key is needed.')
    else:
        raise ValueError('Unknown transcription provider.')


def normalize(source: Path, target: Path):
    binary = ffmpeg_path()
    if not binary:
        raise ValueError('FFmpeg is missing. Set FFMPEG_PATH in .env.')
    # No shell interpolation. Restrict input protocols to local files.
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    result = subprocess.run([binary, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                             '-i', str(source), '-t', '1801', '-vn', '-ac', '1', '-ar', '16000',
                             '-c:a', 'pcm_s16le', '-y', str(target)],
                            capture_output=True, timeout=600, creationflags=flags)
    if result.returncode:
        raise ValueError('Could not decode this audio file. Use a valid WAV, MP3, M4A, MP4, FLAC or WebM file.')
    with wave.open(str(target)) as audio:
        duration = audio.getnframes() / audio.getframerate()
    if duration > 1800:
        target.unlink(missing_ok=True)
        raise ValueError('This first version supports recordings up to 30 minutes.')
    if duration < 0.1:
        target.unlink(missing_ok=True)
        raise ValueError('The recording is too short. Upload at least 0.1 seconds of audio.')
    return duration


def local_transcribe(path: Path, num_speakers=None, report=None, cancel=None):
    check_provider('local')
    from .upload_engine import run
    return run(path, num_speakers=num_speakers, report=report, cancel=cancel)


async def transcribe(path, provider, num_speakers=None, report=None, cancel=None):
    check_provider(provider)
    if provider == 'local':
        return await asyncio.to_thread(local_transcribe, path, num_speakers, report, cancel)
    from .upload_engine import ProcessingCancelled
    if cancel and cancel.is_set():
        raise ProcessingCancelled()
    if report:
        report('requesting_api', None)
    async def chunks():
        with path.open('rb') as audio:
            while block := audio.read(65536):
                yield block
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post('https://api.deepgram.com/v1/listen',
                                     params=dict(model='nova-3', language='en', diarize_model='latest',
                                                 punctuate='true', smart_format='false'),
                                     headers={'Authorization': f"Token {os.environ['DEEPGRAM_API_KEY']}",
                                              'Content-Type': 'audio/wav'}, content=chunks())
        if response.is_error:
            raise ValueError(f'Transcription provider returned HTTP {response.status_code}. Check your key and account quota.')
        if cancel and cancel.is_set():
            raise ProcessingCancelled()
        words = response.json()['results']['channels'][0]['alternatives'][0].get('words', [])
        return group_words(words)


async def chat(messages, provider, schema=None, keep_alive=0, max_tokens=1000):
    async with httpx.AsyncClient(timeout=900) as client:
        if provider == 'ollama':
            url = os.getenv('OLLAMA_URL', 'http://localhost:11434').rstrip('/') + '/api/chat'
            response = await client.post(url, json=dict(model=os.getenv('OLLAMA_MODEL', 'qwen2.5:3b'),
                                                        messages=messages, stream=False,
                                                        **({'format': schema} if schema else {}),
                                                        keep_alive=keep_alive,
                                                        options={'temperature': 0, 'num_ctx': 8192, 'num_predict': max_tokens}))
            if response.is_error:
                raise ValueError(f'Ollama returned HTTP {response.status_code}. Start Ollama and download the configured model.')
            return response.json()['message']['content']
        if provider != 'api' or not capabilities()['llm_api_configured']:
            raise ValueError('Configure LLM_API_URL, LLM_API_KEY and LLM_API_MODEL in .env first.')
        response = await client.post(os.environ['LLM_API_URL'].rstrip('/') + '/chat/completions',
                                     headers={'Authorization': f"Bearer {os.environ['LLM_API_KEY']}"},
                                     json=dict(model=os.environ['LLM_API_MODEL'], messages=messages, temperature=0.1))
        if response.is_error:
            raise ValueError(f'The language-model provider returned HTTP {response.status_code}.')
        return response.json()['choices'][0]['message']['content']


def friendly_error(error):
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException)):
        return 'Could not reach the AI provider in time. Check the service, network and model settings.'
    # Avoid returning remote request headers or local secrets in an exception.
    return f'Processing failed ({type(error).__name__}). Check the configured models and provider access.'
