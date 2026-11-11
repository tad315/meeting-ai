"""Two-channel local streaming. Channel labels are NOT speaker identities."""
import asyncio, json, os, time, uuid, wave
from pathlib import Path
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from . import store

router=APIRouter()
RATE=16000
MAX_SECONDS=1800

class AudioWriter:
    """Append our standard PCM WAV without truncating earlier recording parts."""
    def __init__(self,path,channels,resume):
        import struct
        self.struct=struct
        if not resume:
            with wave.open(str(path),'wb') as w:
                w.setparams((channels,2,RATE,0,'NONE','not compressed'));w.writeframes(b'')
        with wave.open(str(path),'rb') as w:
            if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(channels,2,RATE):
                raise ValueError('Saved audio format cannot be resumed.')
        self.f=open(path,'r+b')
        if self.f.read(44)[36:40]!=b'data':
            self.f.close();raise ValueError('Unsupported WAV header; existing audio was not changed.')
        self.f.seek(0,2);self.size=self.f.tell()-44
    def writeframes(self,data):
        self.f.seek(0,2);self.f.write(data);self.size+=len(data)
        self.f.seek(4);self.f.write(self.struct.pack('<I',36+self.size))
        self.f.seek(40);self.f.write(self.struct.pack('<I',self.size));self.f.flush()
    def close(self):self.f.close()

def model_dir():
    preferred=store.DATA/'models/streaming-en-giga'
    return Path(os.getenv('LIVE_MODEL_DIR',preferred if preferred.is_dir() else store.DATA/'models/streaming-en'))

def model_files():
    root=model_dir()
    manifest=json.loads((root/'manifest.json').read_text())
    return {k:str(root/v['name']) for k,v in manifest['files'].items()}

def create_recognizer():
    import sherpa_onnx
    return sherpa_onnx.OnlineRecognizer.from_transducer(**model_files(),num_threads=2,
        sample_rate=RATE,enable_endpoint_detection=True,rule2_min_trailing_silence=.8,
        rule3_min_utterance_length=20,decoding_method='greedy_search',provider='cpu')

@router.get('/api/live/status')
async def status():
    try:ready=all(Path(p).is_file() for p in model_files().values())
    except (OSError,ValueError,KeyError):ready=False
    from .community_queue import status as worker_status
    worker = worker_status()
    return dict(translation_ready=worker.get('assistant_ready',False) and 'llm' in worker.get('features',[]) and (worker.get('gpu_count') or 0)>=2 and worker.get('available_for_jobs',False),gpu_state=worker.get('startup_state'),assistant_state=worker.get('assistant_state'),gpu_provider=worker.get('provider'),gpu_ready=worker['connected'] and 'live_asr' in worker.get('features',[]),speaker_refinement_ready=False,speaker_labels_mode='record_only',ready=ready,model='Streaming Zipformer English · CPU',max_minutes=30)

class Session:
    def __init__(self,item,recognizer,enabled,resume=False):
        self.item=item;self.r=recognizer;self.enabled=enabled
        self.labels=item.get('live_source_speakers',{'0':'0','1':'1'})
        self.streams=[recognizer.create_stream() for _ in range(2)]
        self.frames=round(item.get('duration',0)*RATE) if resume else 0
        self.starts=[self.frames/RATE]*2;self.ids=[uuid.uuid4().hex for _ in range(2)]
        self.turns=list(item.get('segments',[])) if resume else []
        self.partial=['',''];self.closed=False
        folder=store.DATA/item['id'];folder.mkdir(parents=True,exist_ok=True)
        self.stereo=AudioWriter(folder/'live-sources.wav',2,resume)
        self.mono=AudioWriter(folder/'audio.wav',1,resume)
        item.update(audio=str(folder/'audio.wav'),live_sources=str(folder/'live-sources.wav'))
        self.last_save=self.frames/RATE

    def decode(self,finish=False):
        changes=[]
        for channel in range(2):
            if not self.enabled[channel]:continue
            stream=self.streams[channel]
            if finish:
                stream.accept_waveform(RATE,np.zeros(8000,dtype=np.float32));stream.input_finished()
            while self.r.is_ready(stream):self.r.decode_stream(stream)
            text=self.r.get_result(stream).strip()
            endpoint=finish or self.r.is_endpoint(stream)
            if text:
                turn=dict(id=self.ids[channel],start=self.starts[channel],end=self.frames/RATE,
                          text=text,speaker=self.labels[str(channel)],provisional=True,source_channel=channel)
                if text != self.partial[channel] or endpoint:
                    changes.append(dict(**turn,committed=endpoint))
                if endpoint:self.turns.append(turn)
            self.partial[channel]=text
            if endpoint and not finish:
                self.r.reset(stream);self.starts[channel]=self.frames/RATE
                self.ids[channel]=uuid.uuid4().hex;self.partial[channel]=''
        return changes

    def feed(self,packet):
        if not packet or len(packet)%4 or len(packet)>25600:raise ValueError('Invalid audio packet.')
        data=np.frombuffer(packet,dtype='<i2').reshape(-1,2)
        if self.frames+len(data)>RATE*MAX_SECONDS:raise ValueError('30-minute recording limit reached.')
        self.stereo.writeframes(packet)
        mix=data.astype(np.int32).sum(axis=1)/max(sum(self.enabled),1)
        self.mono.writeframes(np.clip(mix,-32768,32767).astype('<i2').tobytes())
        self.frames+=len(data)
        for i in range(2):
            if self.enabled[i]:self.streams[i].accept_waveform(RATE,data[:,i].astype(np.float32)/32768)
        updates=self.decode()
        if any(u['committed'] for u in updates) or self.frames/RATE-self.last_save>=2:
            self.persist();self.last_save=self.frames/RATE
        return updates

    def persist(self):
        self.item=store.patch(self.item['id'],duration=self.frames/RATE,
            audio=self.item['audio'],live_sources=self.item['live_sources'],
            segments=sorted(self.turns,key=lambda s:s['start']))

    def finish(self,interrupted=False):
        if self.closed:return
        try:self.decode(finish=True)
        finally:
            self.closed=True;self.stereo.close();self.mono.close()
            self.persist()
            self.item=store.patch(self.item['id'],status='ready',warning=
                ('Connection interrupted; only received audio was saved. ' if interrupted else '')+
                'Live transcript is saved and ready for summary/chat. Labels identify audio sources, not people; timestamps are approximate. Reprocessing is optional.')

@router.websocket('/api/live/stream')
async def stream(ws:WebSocket):
    host=ws.headers.get('host');origin=ws.headers.get('origin')
    if origin not in {f'http://{host}',f'https://{host}'}:
        await ws.close(code=1008);return
    await ws.accept()
    gate=ws.app.state.live_lock
    if gate.locked():
        await ws.send_json(dict(type='error',message='A live capture is already running in another tab. Return to that tab.'))
        await ws.close();return
    session=None;normal=False
    async with gate:
        try:
            config=await asyncio.wait_for(ws.receive_json(),30)
            enabled=config.get('sources')
            if not isinstance(enabled,list) or len(enabled)!=2 or any(type(v)is not bool for v in enabled) or not any(enabled):
                raise ValueError('Select at least one audio source.')
            await ws.send_json(dict(type='loading'))
            if config.get('provider') == 'kaggle':
                from .live_gpu import capture
                await capture(ws, config)
                return
            if config.get('language', 'en') != 'en':
                raise ValueError('Live tiếng Việt cần worker Kaggle; CPU cũ chỉ hỗ trợ tiếng Anh.')
            recognizer=await asyncio.to_thread(create_recognizer)
            resume_id=config.get('resume_id')
            if resume_id:
                item=store.get(resume_id) if isinstance(resume_id,str) else None
                if not item or not item.get('live_sources'):raise ValueError('This recording cannot be resumed.')
                if item['status'] not in ('ready','interrupted'):raise ValueError('Meeting is busy.')
                if item['duration']>=MAX_SECONDS:raise ValueError('Meeting reached the 30-minute limit. Start a new meeting.')
                # Preflight both files before opening either for append.
                for name,ch in [('live-sources.wav',2),('audio.wav',1)]:
                    with wave.open(str(store.DATA/item['id']/name),'rb') as w:
                        if (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes())!=(ch,2,RATE,round(item['duration']*RATE)):
                            raise ValueError('Saved audio length or format differs; resume stopped to protect it.')
                labels=item.get('live_source_speakers',{'0':'0','1':'1'})
                expected={'0':'Room microphone (source)','1':'Meeting audio (source)'}
                if any(item['speakers'].get(labels[k])!=expected[k] for k in expected):
                    next_id=max((int(k) for k in item['speakers'] if k.isdigit()),default=-1)+1
                    labels={'0':str(next_id),'1':str(next_id+1)}
                    speakers={**item['speakers'],**{labels[k]:v for k,v in expected.items()}}
                    item=store.patch(item['id'],speakers=speakers,live_source_speakers=labels)
            else:
                item=store.create('Live meeting '+time.strftime('%Y-%m-%d %H:%M'),'upload','live-local')
                item.update(revision=1,speakers={'0':'Room microphone (source)','1':'Meeting audio (source)'},
                        provenance={'engine':'streaming-zipformer','live':True,'speaker_labels':'audio_sources',
                                    'model':json.loads((model_dir()/'manifest.json').read_text()) if (model_dir()/'manifest.json').exists() else {}})
                store.save(item)
            session=Session(item,recognizer,enabled,resume=bool(resume_id))
            session.persist();session.item=store.patch(item['id'],status='recording')
            await ws.send_json(dict(type='ready',id=item['id'],frames=session.frames))
            while True:
                message=await asyncio.wait_for(ws.receive(),30)
                if message['type']=='websocket.disconnect':break
                if message.get('text') is not None:
                    if json.loads(message['text']).get('type')!='stop':raise ValueError('Unknown command.')
                    normal=True;break
                packet=message.get('bytes');t=time.perf_counter()
                changes=await asyncio.to_thread(session.feed,packet)
                from . import live_translation
                live_translation.observe(item['id'],changes)
                await ws.send_json(dict(type='update',turns=changes,frames=session.frames,
                    processing_ms=round((time.perf_counter()-t)*1000)))
        except WebSocketDisconnect:pass
        except Exception as exc:
            try:await ws.send_json(dict(type='error',message=str(exc) if isinstance(exc,ValueError) else 'Live processing stopped. Received audio will be saved. Check model setup.'))
            except Exception:pass
        finally:
            if session:
                import anyio
                # A browser disconnect cancels the request scope. Final audio
                # close/save must finish before that cancellation unwinds.
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(session.finish,not normal)
                try:await ws.send_json(dict(type='saved',id=session.item['id'],duration=session.frames/RATE))
                except Exception:pass
            try:await ws.close()
            except Exception:pass
