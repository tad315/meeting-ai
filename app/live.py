"""Two-channel local streaming. Channel labels are NOT speaker identities."""
import asyncio, json, os, time, uuid, wave
from pathlib import Path
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from . import store

router=APIRouter()
RATE=16000
MAX_SECONDS=1800

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
    return dict(ready=ready,model='Streaming Zipformer English · CPU',max_minutes=30)

class Session:
    def __init__(self,item,recognizer,enabled):
        self.item=item;self.r=recognizer;self.enabled=enabled
        self.streams=[recognizer.create_stream() for _ in range(2)]
        self.frames=0;self.starts=[0.,0.];self.ids=[uuid.uuid4().hex for _ in range(2)]
        self.turns=[];self.partial=['',''];self.closed=False
        folder=store.DATA/item['id'];folder.mkdir(parents=True,exist_ok=True)
        self.stereo=wave.open(str(folder/'live-sources.wav'),'wb')
        self.stereo.setparams((2,2,RATE,0,'NONE','not compressed'))
        self.mono=wave.open(str(folder/'audio.wav'),'wb')
        self.mono.setparams((1,2,RATE,0,'NONE','not compressed'))
        item.update(audio=str(folder/'audio.wav'),live_sources=str(folder/'live-sources.wav'))
        self.last_save=0

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
                          text=text,speaker=str(channel),provisional=True,source_channel=channel)
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
        if self.frames/RATE-self.last_save>=5:
            self.persist();self.last_save=self.frames/RATE
        return updates

    def persist(self):
        self.item.update(duration=self.frames/RATE,segments=sorted(self.turns,key=lambda s:s['start']))
        store.save(self.item)

    def finish(self,interrupted=False):
        if self.closed:return
        try:self.decode(finish=True)
        finally:
            self.closed=True;self.stereo.close();self.mono.close()
            self.item.update(status='ready',revision=1,warning=
                ('Connection interrupted; only received audio was saved. ' if interrupted else '')+
                'Live draft. Labels identify audio sources, not individual people. Review or apply Colab GPU processing before summarizing. Timestamps are approximate.')
            self.persist()

@router.websocket('/api/live/stream')
async def stream(ws:WebSocket):
    host=ws.headers.get('host');origin=ws.headers.get('origin')
    if origin not in {f'http://{host}',f'https://{host}'}:
        await ws.close(code=1008);return
    await ws.accept()
    gate=ws.app.state.queue
    if gate.locked():
        await ws.send_json(dict(type='error',message='Another AI job is running. Wait for it to finish.'))
        await ws.close();return
    session=None;normal=False
    async with gate:
        try:
            config=await asyncio.wait_for(ws.receive_json(),30)
            enabled=config.get('sources')
            if not isinstance(enabled,list) or len(enabled)!=2 or any(type(v)is not bool for v in enabled) or not any(enabled):
                raise ValueError('Select at least one audio source.')
            await ws.send_json(dict(type='loading'))
            recognizer=await asyncio.to_thread(create_recognizer)
            item=store.create('Live meeting '+time.strftime('%Y-%m-%d %H:%M'),'upload','live-local')
            item.update(status='recording',speakers={'0':'Room microphone (source)','1':'Meeting audio (source)'},
                        provenance={'engine':'streaming-zipformer','live':True,'speaker_labels':'audio_sources',
                                    'model':json.loads((model_dir()/'manifest.json').read_text()) if (model_dir()/'manifest.json').exists() else {}})
            session=Session(item,recognizer,enabled);session.persist()
            await ws.send_json(dict(type='ready',id=item['id']))
            while True:
                message=await asyncio.wait_for(ws.receive(),30)
                if message['type']=='websocket.disconnect':break
                if message.get('text') is not None:
                    if json.loads(message['text']).get('type')!='stop':raise ValueError('Unknown command.')
                    normal=True;break
                packet=message.get('bytes');t=time.perf_counter()
                changes=await asyncio.to_thread(session.feed,packet)
                await ws.send_json(dict(type='update',turns=changes,frames=session.frames,
                    processing_ms=round((time.perf_counter()-t)*1000)))
        except WebSocketDisconnect:pass
        except Exception as exc:
            try:await ws.send_json(dict(type='error',message=str(exc) if isinstance(exc,ValueError) else 'Live processing stopped. Received audio will be saved. Check model setup.'))
            except Exception:pass
        finally:
            if session:
                await asyncio.to_thread(session.finish,not normal)
                try:await ws.send_json(dict(type='saved',id=session.item['id'],duration=session.frames/RATE))
                except Exception:pass
            try:await ws.close()
            except Exception:pass
