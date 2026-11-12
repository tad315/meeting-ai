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
from .live_word_turns import sentence_turns


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


def _norm(text):
    import re
    return re.sub(r'[^\w]+',' ',text.lower()).strip()


def stable_prefix(previous, current):
    """How many leading complete sentences two consecutive decodes agree on
    (LocalAgreement-2): text that survived re-decoding with more audio."""
    count=0
    for a,b in zip(previous,current):
        if _norm(a)!=_norm(b) or not _norm(b):break
        count+=1
    return count


def snapshot_turn(result, channel, start, duration, identity):
    # Do not request per-window speaker clustering on the fast caption path.
    result=dict(result, speaker_labels=False)
    # Drop Whisper's invented YouTube outros ("Hãy subscribe cho kênh …")
    # before they are shown, saved, translated or summarized.
    from .hallucination import clean_text
    if isinstance(result.get('segments'),list):
        result['segments']=[dict(r,text=clean_text(r['text'])) if isinstance(r,dict) and isinstance(r.get('text'),str) else r
                            for r in result['segments']]
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
        live_caption_mode='revisable',live_speaker_mode='source_only',live_refinement_status='disabled')
    store.save(item)
    mono=AudioWriter(folder/'audio.wav',1,bool(resume))
    stereo=AudioWriter(folder/'live-sources.wav',2,bool(resume))
    turns=list(item.get('segments',[]))
    cursors=[frames,frames]
    submitted=[frames,frames]
    identities=[uuid.uuid4().hex,uuid.uuid4().hex]
    pending=[None,None]
    wake=asyncio.Event()
    timing_rows=[]  # where the time of recent caption updates went
    agreed=[[],[]]  # complete sentences of the previous decode, per source
    early=[[],[]]   # sentences of the current audio window already committed early
    visible=[set(),set()]  # draft caption ids currently shown in the browser, per source
    stopping=False
    normal=False
    errors=[]
    last_schedule=frames
    last_persist=time.monotonic()

    async def send(message):
        if message.get('type')=='update':
            # Track drafts on screen; whenever text is committed, retire EVERY
            # older draft of that source (not just the newest), so a superseded
            # draft can never linger next to its committed sentence.
            for rid in message.get('remove_turns',[]):
                for ids in visible:ids.discard(rid)
            channels={t.get('source_channel') for t in message.get('turns',[]) if t.get('committed')}
            keep={t['id'] for t in message.get('turns',[]) if not t.get('committed')}
            for channel in channels:
                if channel in (0,1):
                    stale=visible[channel]-keep
                    if stale:
                        message=dict(message,remove_turns=list(message.get('remove_turns',[]))+sorted(stale))
                        visible[channel]-=stale
            for t in message.get('turns',[]):
                if not t.get('committed') and t.get('source_channel') in (0,1):visible[t['source_channel']].add(t['id'])
            from . import live_translation
            live_translation.observe(item['id'],message.get('turns',[]),message.get('remove_turns',[]))
        try:await ws.send_json(message)
        except Exception:pass

    def persist():
        store.patch(item['id'],duration=frames/RATE,segments=sorted(turns,key=lambda s:s['start']))

    def read(channel,start,end):
        with wave.open(str(folder/'live-sources.wav'),'rb') as reader:
            reader.setpos(start)
            values=np.frombuffer(reader.readframes(end-start),dtype='<i2').reshape(-1,2)
            return values[:,channel].copy()

    def unsettled(channel,turn):
        """Sentences of a window decode that were not committed early.
        Matched by text first; if Whisper revised a committed sentence, by time."""
        parts=sentence_turns(turn) if turn.get('words') else [turn]
        done=early[channel]
        index=0
        while index<len(parts) and index<len(done) and _norm(parts[index]['text'])==_norm(done[index]['text']):index+=1
        if index<len(done):
            last_end=done[-1]['end'];index=0
            while index<len(parts) and parts[index]['end']<=last_end+.25:index+=1
        return parts[index:]

    async def settle(channel,end):
        if pending[channel]:
            sentences=unsettled(channel,pending[channel])
            turns.extend(sentences);persist()
            await send(dict(type='update',turns=[dict(t,committed=True) for t in sentences],
                remove_turns=[pending[channel]['id']],frames=frames,processing_ms=0))
        cursors[channel]=end
        submitted[channel]=end
        pending[channel]=None
        identities[channel]=uuid.uuid4().hex
        agreed[channel]=[]
        early[channel]=[]

    async def advance_window(channel,samples,start):
        last_end=early[channel][-1]['end']
        rest=unsettled(channel,pending[channel]) if pending[channel] else []
        following=next((w['start'] for p in rest for w in (p.get('words') or [])),None)
        lo,hi=last_end-.05,(following if following and following>last_end else last_end+.15)+.05
        a,b=max(0,round((lo-start/RATE)*RATE)),min(len(samples),round((hi-start/RATE)*RATE))
        cut=(last_end+hi-.05)/2
        width=RATE//50
        if b-a>=2*width:
            # Quietest 20 ms inside the gap: robust to word-timestamp jitter.
            blocks=samples[a:a+(b-a)//width*width].astype(np.float32).reshape(-1,width)
            quietest=int(np.argmin(np.sqrt(np.mean(blocks*blocks,axis=1))))
            cut=start/RATE+(a+quietest*width+width//2)/RATE
        frame=min(max(cursors[channel]+1,round(cut*RATE)),frames)
        old=identities[channel]
        cursors[channel]=submitted[channel]=frame
        pending[channel]=None
        identities[channel]=uuid.uuid4().hex
        agreed[channel]=[];early[channel]=[]
        await send(dict(type='update',turns=[],remove_turns=[old],frames=frames,processing_ms=0))

    async def commit_stable(channel,turn):
        """Commit finished sentences during continuous speech instead of waiting
        for a pause or the 12 s window; they appear (and go to translation)
        about one decode after they are spoken. Only sentences two consecutive
        decodes agree on are committed. The audio window is NOT cut here
        (word timestamps jitter; a cut could drop a word): later decodes of the
        same window simply skip the sentences already committed."""
        rest=unsettled(channel,turn)
        complete=[p for p in rest[:-1] if p.get('text','').strip()]
        keep=stable_prefix(agreed[channel],[p['text'] for p in complete])
        agreed[channel]=[p['text'] for p in complete]
        if not keep:return
        committed=[dict(p,id=uuid.uuid4().hex) for p in complete[:keep]]
        for part in committed:part.pop('caption_parent',None)
        early[channel].extend(committed)
        agreed[channel]=agreed[channel][keep:]
        turns.extend(committed);persist()
        remainder=rest[keep:]
        draft=None
        if remainder:
            draft=dict(turn,id=identities[channel],start=remainder[0]['start'],end=remainder[-1]['end'],
                text=' '.join(p['text'] for p in remainder),words=[w for p in remainder for w in (p.get('words') or [])])
        await send(dict(type='update',turns=[dict(t,committed=True) for t in committed]+
            ([dict(draft,committed=False)] if draft else []),
            remove_turns=[] if draft else [identities[channel]],frames=frames,processing_ms=0))

    def record_timing(job, result, began):
        """Split one caption update into queue / worker round trip / GPU /
        network so a live session shows where its latency comes from."""
        marks=remote_tasks.timing(job);now=time.time()
        inference=result.get('inference_seconds') if isinstance(result.get('inference_seconds'),(int,float)) else None
        row=dict(total=now-began)
        if marks['dispatched']:row['queue']=max(0,marks['dispatched']-began)
        if marks['dispatched'] and marks['received']:
            row['worker_round_trip']=max(0,marks['received']-marks['dispatched'])
            if inference is not None:row['network_and_overhead']=max(0,row['worker_round_trip']-inference)
        if marks['received']:row['server_pickup']=max(0,now-marks['received'])
        if inference is not None:row['inference']=inference
        timing_rows.append(row);del timing_rows[:-120]
        summary={key:round(float(np.median([r[key] for r in timing_rows if key in r])),3)
                 for key in ('total','queue','worker_round_trip','inference','network_and_overhead','server_pickup')
                 if any(key in r for r in timing_rows)}
        summary['updates']=len(timing_rows)
        return row,summary

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
                if boundary is None and not stopping and end-start>=12*RATE and early[channel]:
                    # 12 s of continuous speech: move the window to the boundary
                    # after the last early-committed sentence instead of cutting
                    # blindly at 12 s (which can split a word). The rest of the
                    # speech is decoded again in the next window.
                    await advance_window(channel,samples,start)
                    wake.set();continue
                final=boundary is not None or end-start>=12*RATE or stopping
                if boundary is not None:
                    end=start+boundary;samples=samples[:boundary]
                if end<=submitted[channel] and not stopping:
                    # Quiet endpoint became detectable after this snapshot was decoded.
                    if final:await settle(channel,end)
                    continue
                did_work=True
                began=time.time()
                try:
                    # No 'session': existing workers skip slow Community-1 clustering.
                    job=await asyncio.to_thread(remote_tasks.enqueue,
                        dict(kind='live_asr',language=language),wav_bytes(samples))
                    result=await remote_tasks.wait(job,timeout=60,poll_interval=.1)
                    turn=snapshot_turn(result,channel,start/RATE,len(samples)/RATE,identities[channel])
                    pending[channel]=turn
                    submitted[channel]=end
                    row,breakdown=record_timing(job,result,began)
                    remote=row['total']
                    model=result.get('asr_model')
                    if model not in ('small','turbo','large-v3'):model=None
                    lag=max(0,frames/RATE-turn['end']) if turn else 0
                    store.patch(item['id'],live_asr_model=model,live_remote_seconds=round(remote,2),
                        live_latency_seconds=round(lag,2),live_queue_depth=0,live_asr_quality=result.get('quality',[]),live_inference_seconds=result.get('inference_seconds'),live_timing_breakdown=breakdown)
                    shown=None
                    if turn:
                        rest=unsettled(channel,turn)
                        if rest:
                            shown=dict(turn,start=rest[0]['start'],end=rest[-1]['end'],text=' '.join(p['text'] for p in rest),
                                words=[w for p in rest for w in (p.get('words') or [])])
                    await send(dict(type='update',turns=[dict(shown,committed=False)] if shown else [],
                        remove_turns=[] if shown else [identities[channel]],frames=frames,
                        processing_ms=round(remote*1000),timing=dict(asr_model=model,
                            remote_seconds=round(remote,2),queue_depth=0,queue_wait_seconds=0,
                            audio_lag_seconds=round(lag,2),caption_mode='revisable',breakdown=breakdown)))
                    if final:await settle(channel,end)
                    elif turn:await commit_stable(channel,turn)
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
    try:
        await send(dict(type='ready',id=item['id'],frames=frames,caption_mode='revisable',
            speaker_mode='source_only'))
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
            mono.close();stereo.close();persist()
        warning='Transcript Live là bản nháp. Mic/tab là nguồn âm thanh; phân biệt người nói thực hiện trong Record.'
        if errors:warning+=' Có đoạn chưa nhận diện xong; hãy xử lý lại audio đã lưu.'
        store.patch(item['id'],status='ready',warning=warning,live_transcript_incomplete=bool(errors) or not normal)
        await send(dict(type='saved',id=item['id'],duration=frames/RATE))
