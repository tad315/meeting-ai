"""Revisable live captions over the existing authenticated ASR worker.

There is one in-flight inference and an event for newer audio, not a FIFO of
snapshots. Audio is always written before scheduling. This is rolling remote
inference, not native model streaming and not a sub-second latency guarantee.
"""
import asyncio
import json
import time
import uuid
import wave
import numpy as np
from . import store, remote_tasks
from .live import AudioWriter, RATE, MAX_SECONDS
from .live_gpu import validated_turns, wav_bytes
from .live_word_turns import sentence_turns, label_turn, validate_tracks


def endpoint(samples, rate=RATE):
    """Return a quiet boundary after at least one second, or None.

    This energy heuristic schedules a final decode; it does not remove quiet
    speech or decide whether an audio window should be transcribed.
    """
    width=rate//50
    count=len(samples)//width
    if count < 80: return None
    blocks=samples[:count*width].astype(np.float32).reshape(-1,width)
    rms=np.sqrt(np.mean(blocks*blocks,axis=1))
    quiet=0
    for i,value in enumerate(rms):
        quiet=quiet+1 if value<180 else 0
        if quiet>=30 and i>=79:
            return (i+1)*width
    return None


def snapshot_turn(result, channel, start, duration, identity):
    # Do not request per-window speaker clustering on the fast caption path.
    result=dict(result, speaker_labels=False)
    rows=validated_turns(result,channel,start,duration,0)
    if not rows: return None
    return dict(id=identity,start=rows[0]['start'],end=rows[-1]['end'],
        text=' '.join(r['text'] for r in rows),speaker=str(channel),source_channel=channel,
        provisional=True,words=[dict(start=start+r['start'],end=start+r['end'],word=r['text'].strip(),text=r['text'].strip())
            for r in result['segments'] if r['text'].strip()])


async def capture(ws,config):
    enabled=config['sources']
    language=config.get('language','vi')
    if language not in ('vi','en','auto'):raise ValueError('Ngôn ngữ không hợp lệ.')
    remote_tasks.require_worker('live_asr')
    from . import community_queue
    refinement_enabled={'live_refine','shared_turns'}.issubset(community_queue.status().get('features',[])) 
    resume=config.get('resume_id')
    if resume:
        item=store.get(resume) if isinstance(resume,str) else None
        if not item or item.get('provider')!='live-kaggle' or item['status'] not in ('ready','interrupted'):
            raise ValueError('Chỉ tiếp tục phiên live Kaggle đã lưu; phiên khác cần tạo cuộc họp mới.')
        if item.get('language')!=language:raise ValueError('Giữ ngôn ngữ phiên cũ hoặc tạo cuộc họp mới.')
        for name,channels in [('audio.wav',1),('live-sources.wav',2)]:
            with wave.open(str(store.DATA/item['id']/name),'rb') as reader:
                if (reader.getnchannels(),reader.getsampwidth(),reader.getframerate(),reader.getnframes()) != (channels,2,RATE,round(item['duration']*RATE)):
                    raise ValueError('Audio đã lưu không khớp; không ghi đè.')
    else:
        item=store.create('Cuộc họp '+time.strftime('%Y-%m-%d %H:%M'),'upload','live-kaggle')
        item.update(language=language,revision=1,speakers={'0':'Micro trong phòng (nguồn)','1':'Âm thanh cuộc họp (nguồn)'})
    folder=store.DATA/item['id'];folder.mkdir(exist_ok=True)
    frames=round(item.get('duration',0)*RATE) if resume else 0
    if frames>=RATE*MAX_SECONDS:raise ValueError('Phiên đã đạt giới hạn 30 phút.')
    item.update(audio=str(folder/'audio.wav'),live_sources=str(folder/'live-sources.wav'),status='recording',
        live_caption_mode='revisable',live_speaker_mode='delayed_acoustic' if refinement_enabled else 'source_only')
    store.save(item)
    mono=AudioWriter(folder/'audio.wav',1,bool(resume))
    stereo=AudioWriter(folder/'live-sources.wav',2,bool(resume))
    turns=list(item.get('segments',[]))
    cursors=[frames,frames]
    submitted=[frames,frames]
    identities=[uuid.uuid4().hex,uuid.uuid4().hex]
    pending=[None,None]
    wake=asyncio.Event()
    stopping=False
    normal=False
    errors=[]
    last_schedule=frames
    last_persist=time.monotonic()
    last_capability_check=0
    refine_wake=asyncio.Event()
    refinement_finishing=False
    refined=[frames,frames]
    session_start=frames

    async def send(message):
        try:await ws.send_json(message)
        except Exception:pass

    def persist():
        store.patch(item['id'],duration=frames/RATE,segments=sorted(turns,key=lambda s:s['start']))

    def read(channel,start,end):
        with wave.open(str(folder/'live-sources.wav'),'rb') as reader:
            reader.setpos(start)
            values=np.frombuffer(reader.readframes(end-start),dtype='<i2').reshape(-1,2)
            return values[:,channel].copy()

    async def settle(channel,end):
        if pending[channel]:
            sentences=sentence_turns(pending[channel])
            turns.extend(sentences);persist()
            await send(dict(type='update',turns=[dict(t,committed=True) for t in sentences],
                remove_turns=[pending[channel]['id']],frames=frames,processing_ms=0))
            refine_wake.set()
        cursors[channel]=end
        submitted[channel]=end
        pending[channel]=None
        identities[channel]=uuid.uuid4().hex

    async def refine():
        while True:
            await refine_wake.wait();refine_wake.clear()
            for channel in range(2):
                if not enabled[channel]:continue
                settled=[t for t in turns if t.get('source_channel')==channel and t.get('words')]
                latest=round(max((t['end'] for t in settled),default=session_start/RATE)*RATE)
                if latest-refined[channel]<4*RATE and not refinement_finishing:continue
                through=min(latest,refined[channel]+4*RATE)
                if through<=refined[channel] or through-session_start<3*RATE:continue
                start=max(session_start,through-20*RATE)
                try:
                    data=read(channel,start,through)
                    # Relative word timestamps let the worker reuse the same sentence engine.
                    units=[dict(id=t['id'],words=[dict(w,start=w['start']-start/RATE,end=w['end']-start/RATE) for w in t['words']])
                        for t in settled if t['start']>=start/RATE and t['end']<=through/RATE]
                    job=await asyncio.to_thread(remote_tasks.enqueue,
                        dict(kind='live_refine',session=item['id']+'_'+str(channel),offset=start/RATE,units=units),wav_bytes(data))
                    result=await remote_tasks.wait(job,timeout=180,poll_interval=.2)
                    tracks=validate_tracks(result,len(data)/RATE)
                    changed=[];speakers=dict(store.get(item['id']).get('speakers',{}))
                    for index,turn in enumerate(turns):
                        updated=label_turn(turn,tracks,start/RATE,len(data)/RATE,channel)
                        if updated!=turn:
                            turns[index]=updated;changed.append(dict(updated,committed=True))
                            labels=[updated.get('speaker')]+[span.get('speaker') for span in updated.get('speaker_spans',[])]
                            for label in labels:
                                if label and label.startswith(str(channel)+'_speaker_'):
                                    speakers[label]='Người nói '+str(channel+1)+'.'+label.split('_')[-1]
                    store.patch(item['id'],speakers=speakers,live_refinement_status='ready' if any(t['speaker'] for t in tracks) else 'uncertain',
                        live_refinement_timing=result.get('timing'),live_refinement_through=through/RATE)
                    persist()
                    if changed:await send(dict(type='update',turns=changed,speakers=speakers,frames=frames,processing_ms=0))
                    refined[channel]=through
                except Exception:
                    # Never interrupt captions or erase their original text.
                    store.patch(item['id'],live_refinement_status='error')
                    await send(dict(type='notice',message='Nhãn giọng chưa cập nhật được; chữ và audio vẫn được giữ.'))
                    refined[channel]=through
            if refinement_finishing:
                latest=[round(max((t['end'] for t in turns if t.get('source_channel')==c),default=session_start/RATE)*RATE) for c in range(2)]
                if any(enabled[c] and latest[c]>refined[c] for c in range(2)):
                    refine_wake.set()
                else:return
            elif any(enabled[c] and round(max((t['end'] for t in turns if t.get('source_channel')==c),default=session_start/RATE)*RATE)-refined[c]>=4*RATE for c in range(2)):
                refine_wake.set()

    async def consume():
        while True:
            await wake.wait();wake.clear()
            did_work=False
            for channel in range(2):
                if not enabled[channel] or cursors[channel]>=frames:continue
                start=cursors[channel]
                end=min(frames,start+12*RATE)
                # Stop can settle the exact last preview without redundant inference.
                if end<=submitted[channel]:
                    if stopping:await settle(channel,end)
                    continue
                if end-start<RATE and not stopping:continue
                samples=read(channel,start,end)
                if np.max(np.abs(samples.astype(np.int32)),initial=0)==0:
                    await settle(channel,end)
                    continue
                boundary=endpoint(samples)
                final=boundary is not None or end-start>=12*RATE or stopping
                if boundary is not None:
                    end=start+boundary;samples=samples[:boundary]
                if end<=submitted[channel] and not stopping:
                    # Quiet endpoint became detectable after this snapshot was decoded.
                    if final:await settle(channel,end)
                    continue
                did_work=True
                began=time.monotonic()
                try:
                    # No 'session': existing workers skip slow Community-1 clustering.
                    job=await asyncio.to_thread(remote_tasks.enqueue,
                        dict(kind='live_asr',language=language),wav_bytes(samples))
                    result=await remote_tasks.wait(job,timeout=60,poll_interval=.1)
                    turn=snapshot_turn(result,channel,start/RATE,len(samples)/RATE,identities[channel])
                    pending[channel]=turn
                    submitted[channel]=end
                    remote=time.monotonic()-began
                    model=result.get('asr_model')
                    if model not in ('small','turbo'):model=None
                    lag=max(0,frames/RATE-turn['end']) if turn else 0
                    store.patch(item['id'],live_asr_model=model,live_remote_seconds=round(remote,2),
                        live_latency_seconds=round(lag,2),live_queue_depth=0)
                    await send(dict(type='update',turns=[dict(turn,committed=False)] if turn else [],
                        remove_turns=[] if turn else [identities[channel]],frames=frames,
                        processing_ms=round(remote*1000),timing=dict(asr_model=model,
                            remote_seconds=round(remote,2),queue_depth=0,queue_wait_seconds=0,
                            audio_lag_seconds=round(lag,2),caption_mode='revisable')))
                    if final:await settle(channel,end)
                except Exception as exc:
                    errors.append(str(exc) if isinstance(exc,ValueError) else 'Lỗi nhận diện GPU.')
                    await send(dict(type='notice',message='Chưa nhận được chữ từ GPU. Audio vẫn đang được lưu.'))
                    # Avoid a tight retry loop; keep cursor so audio is not silently skipped.
                    await asyncio.sleep(.5)
                    if stopping:
                        await settle(channel,frames)
            if stopping and all(not enabled[c] or cursors[c]>=frames for c in range(2)):return
            # Coalesce new packets into the next decode, never queue every .5 s snapshot.
            if did_work and any(enabled[c] and frames-submitted[c]>=RATE//2 for c in range(2)):
                wake.set()

    consumer=asyncio.create_task(consume())
    refinement=asyncio.create_task(refine()) if refinement_enabled else None
    try:
        await send(dict(type='ready',id=item['id'],frames=frames,caption_mode='revisable',
            speaker_mode='delayed_acoustic' if refinement_enabled else 'source_only'))
        while True:
            message=await asyncio.wait_for(ws.receive(),30)
            if message['type']=='websocket.disconnect':break
            if message.get('text') is not None:
                if json.loads(message['text']).get('type')!='stop':raise ValueError('Lệnh không hợp lệ.')
                normal=True;break
            packet=message.get('bytes')
            if not packet or len(packet)%4 or len(packet)>25600:raise ValueError('Audio packet không hợp lệ.')
            data=np.frombuffer(packet,dtype='<i2').reshape(-1,2).copy()
            data[:,np.logical_not(enabled)]=0
            if frames+len(data)>RATE*MAX_SECONDS:raise ValueError('Đạt giới hạn 30 phút.')
            stereo.writeframes(data.tobytes())
            mix=data.astype(np.int32).sum(axis=1)/max(sum(enabled),1)
            mono.writeframes(np.clip(mix,-32768,32767).astype('<i2').tobytes())
            frames+=len(data)
            if not refinement_enabled and time.monotonic()-last_capability_check>=2:
                last_capability_check=time.monotonic()
                if {'live_refine','shared_turns'}.issubset(community_queue.status().get('features',[])):
                    refinement_enabled=True
                    refinement=asyncio.create_task(refine())
                    store.patch(item['id'],live_speaker_mode='delayed_acoustic')
                    refine_wake.set()
                    await send(dict(type='notice',message='Model giọng đã sẵn sàng; đang cập nhật nhãn cho các câu đã lưu.'))
            if frames-last_schedule>=RATE//2:
                last_schedule=frames;wake.set()
            if time.monotonic()-last_persist>2:
                persist();last_persist=time.monotonic()
            await send(dict(type='update',turns=[],frames=frames,processing_ms=0))
    finally:
        stopping=True;wake.set()
        try:
            await send(dict(type='notice',message='Đã lưu audio. Đang chốt phần chữ tạm…'))
            await asyncio.wait_for(consumer,90)
        except asyncio.TimeoutError:
            errors.append('Hết thời gian chờ GPU. Audio đầy đủ vẫn được giữ.')
        except Exception:
            errors.append('Lỗi chốt chữ tạm. Audio đầy đủ vẫn được giữ.')
        finally:
            consumer.cancel();await asyncio.gather(consumer,return_exceptions=True)
            for channel in range(2):
                if pending[channel]:
                    turns.extend(sentence_turns(pending[channel]));pending[channel]=None
            if refinement:
                refinement_finishing=True
                refine_wake.set()
                await send(dict(type='notice',message='Đang hoàn thiện nhãn người nói; chữ đã được lưu.'))
                try:await asyncio.wait_for(refinement,60)
                except asyncio.TimeoutError:
                    store.patch(item['id'],live_refinement_status='incomplete')
                except Exception:
                    store.patch(item['id'],live_refinement_status='error')
                finally:
                    refinement.cancel();await asyncio.gather(refinement,return_exceptions=True)
            mono.close();stereo.close();persist()
        warning=('Chữ và nhãn giọng đều là bản nháp cần kiểm tra. Nhãn chưa chắc chắn được để chưa xác định.' if refinement_enabled else 'Chữ trực tiếp là bản nháp; nhãn mic/tab là nguồn âm thanh. Worker hiện tại chưa hỗ trợ cập nhật giọng nền.')
        if errors:warning+=' Có đoạn chưa nhận diện xong; hãy xử lý lại audio đã lưu.'
        store.patch(item['id'],status='ready',warning=warning,live_transcript_incomplete=bool(errors) or not normal)
        await send(dict(type='saved',id=item['id'],duration=frames/RATE))
