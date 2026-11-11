import re
import uuid

def sentence_end(text):
    text=text.strip().rstrip('”\"\'»)]')
    if text.endswith(('?', '!', '。', '！', '？')):return True
    if not text.endswith('.'):return False
    # Common abbreviations are not sentence boundaries (especially 5 a.m.).
    last=text.split()[-1].lower() if text.split() else ''
    if last in ('mr.','mrs.','ms.','dr.','prof.','jr.','sr.','e.g.','i.e.','a.m.','p.m.','m.'):
        return False
    if re.fullmatch(r'(?:[a-z]\.){1,4}',last):return False
    return True


def sentence_turns(turn):
    words=turn.get('words') or []
    if not words:return [turn]
    groups=[];current=[]
    for word in words:
        if current and word['start']-current[-1]['end']>=1:
            groups.append(current);current=[]
        current.append(word)
        if sentence_end(' '.join(w['word'] for w in current)):
            groups.append(current);current=[]
    if current:groups.append(current)
    result=[]
    for index,group in enumerate(groups):
        item=dict(turn,id=turn['id'] if index==0 else uuid.uuid5(uuid.NAMESPACE_URL,f"{turn['id']}:{index}").hex,
            start=group[0]['start'],end=group[-1]['end'],words=group,
            text=' '.join(w['word'].strip() for w in group),caption_parent=turn['id'])
        result.append(item)
    return result


def clear_acoustic(turn):
    if not turn.get('speaker_status') and not turn.get('speaker_spans'):return turn
    updated=dict(turn,speaker=str(turn['source_channel']) if turn['source_channel'] is not None else None)
    updated.pop('speaker_status',None);updated.pop('speaker_spans',None)
    updated['words']=[{k:v for k,v in w.items() if k!='speaker'} for w in turn.get('words',[])]
    return updated


def label_turn(turn,tracks,offset,duration,channel):
    if turn.get('source_channel')!=channel or not turn.get('words'):return turn
    if turn['start']<offset-.05 or turn['end']>offset+duration+.05:return turn
    base=clear_acoustic(turn)
    spans=[];words=[];votes={};total=0
    for word in base['words']:
        candidates=[(max(0,min(word['end'],offset+t['end'])-max(word['start'],offset+t['start'])),t['speaker']) for t in tracks]
        overlap,label=max(candidates,key=lambda pair:pair[0],default=(0,None))
        width=max(.01,word['end']-word['start']);total+=width
        label=(f'{channel}_{label}' if channel is not None else label) if label and overlap/width>=.5 else None
        words.append(dict(word,speaker=label))
        if label:votes[label]=votes.get(label,0)+width
        if spans and spans[-1]['speaker']==label:
            spans[-1]['text']+=' '+word['word'].strip();spans[-1]['end']=word['end'];spans[-1]['words'].append(words[-1])
        else:
            spans.append(dict(start=word['start'],end=word['end'],text=word['word'].strip(),speaker=label,words=[words[-1]]))
    if not votes:return base
    if len(votes)==1:
        label=next(iter(votes))
        # Keep small timestamp gaps inside a sentence, never display isolated
        # unassigned words. Insufficient coverage leaves the WHOLE sentence pending.
        if votes[label]/max(.01,total)<.8:return base
        return dict(base,speaker=label,words=words,speaker_status='provisional_acoustic')
    # Splitting a sentence requires sustained acoustic support on BOTH sides.
    # A one-word fluctuation must not create an apparent change of person.
    runs,sustained=stable_acoustic_runs(words)
    if not sustained:return base
    spans=[dict(start=r[0]['start'],end=r[-1]['end'],text=' '.join(w['word'].strip() for w in r),
                speaker=r[0]['speaker'],words=r) for r in runs]
    return dict(base,words=words,speaker_spans=spans,speaker=None,speaker_status='provisional_acoustic')




SPEAKER_MATCH=.60
SPEAKER_MARGIN=.08
SPEAKER_NOVEL=.40
SPEAKER_SUPPORT_SECONDS=3.0


def normalized_words(words):
    import math
    clean=[]
    for value in words:
        if not isinstance(value,dict):raise ValueError('Invalid word timestamp.')
        text=value.get('word',value.get('text',''))
        a,b=value.get('start'),value.get('end')
        if not isinstance(text,str) or any(type(x) not in (float,int) or not math.isfinite(x) for x in (a,b)) or not 0<=a<=b:
            raise ValueError('Invalid word timestamp.')
        if text.strip():clean.append(dict(value,word=text.strip(),text=text.strip()))
    if any(clean[i]['start']<clean[i-1]['start']-.05 for i in range(1,len(clean))):
        raise ValueError('Word timestamps are out of order.')
    return clean


def record_sentences(words,tracks=()):
    """Same sentence splitter and acoustic boundary guard as Live.

    Tracks are audio evidence, not textual inference. Strong changes may split
    a sentence; word-sized changes cannot. No word is discarded or duplicated.
    """
    clean=normalized_words(words)
    if not clean:return []
    root=dict(id='record-root',text=' '.join(w['word'] for w in clean),words=clean,
        start=clean[0]['start'],end=clean[-1]['end'],speaker=None,source_channel=None)
    result=[]
    for sentence in sentence_turns(root):
        labelled=label_turn(sentence,tracks,0,clean[-1]['end']+.1,None) if tracks else sentence
        parts=labelled.get('speaker_spans') or [labelled]
        for part in parts:
            item=dict(sentence,start=part['start'],end=part['end'],text=part['text'],
                words=part['words'],speaker=part.get('speaker'))
            item.pop('speaker_spans',None)
            item['id']=f'U{len(result)+1:04d}'
            result.append(item)
    return result


def cluster_record_embeddings(candidates, max_speakers=8):
    """Complete-link cosine clustering on verified sentence/audio units.

    Weak/ambiguous clusters stay unassigned instead of forcing every sentence
    into a requested speaker count. This is an experimental acoustic policy.
    """
    import numpy as np
    vectors=[];indexes=[]
    for i,candidate in enumerate(candidates):
        vector=candidate.get('vector')
        if vector is None:continue
        vector=np.asarray(vector,dtype=np.float32);norm=np.linalg.norm(vector)
        if norm>0 and np.isfinite(vector).all() and candidate['end']-candidate['start']>=1.5:vectors.append(vector/norm);indexes.append(i)
    output=[None]*len(candidates)
    if not vectors:return output
    matrix=np.clip(np.asarray(vectors)@np.asarray(vectors).T,-1,1)
    groups=[[i] for i in range(len(vectors))]
    active=matrix.copy();np.fill_diagonal(active,-2)
    for _ in range(len(vectors)-1):
        a,b=np.unravel_index(int(np.argmax(active)),active.shape)
        if active[a,b]<SPEAKER_MATCH:break
        groups[a]+=groups[b];groups[b]=[]
        merged=np.minimum(active[a,:],active[b,:])
        active[a,:]=merged;active[:,a]=merged
        active[b,:]=-2;active[:,b]=-2;active[a,a]=-2
    qualified=[]
    for group in groups:
        if not group:continue
        intervals=sorted((candidates[indexes[i]]['start'],candidates[indexes[i]]['end']) for i in group)
        total=0.;right=float('-inf')
        for a,b in intervals:total+=max(0,b-max(a,right));right=max(right,b)
        if total>=SPEAKER_SUPPORT_SECONDS:qualified.append(group)
    qualified.sort(key=lambda g:min(candidates[indexes[i]]['start'] for i in g))
    centroids=[]
    for number,group in enumerate(qualified[:max_speakers]):
        centroid=np.mean([vectors[i] for i in group],axis=0);centroid/=np.linalg.norm(centroid)
        centroids.append(centroid)
        for i in group:output[indexes[i]]=f'SPEAKER_{number:02d}'
    # Short samples can match existing identities, but cannot create a person.
    for i,candidate in enumerate(candidates):
        if output[i] is not None or candidate.get('vector') is None or not centroids:continue
        vector=np.asarray(candidate['vector'],dtype=np.float32);norm=np.linalg.norm(vector)
        if not np.isfinite(vector).all() or norm<=0:continue
        scores=np.asarray(centroids)@(vector/norm);rank=np.argsort(scores)[::-1]
        best=int(rank[0]);margin=float(scores[best]-(scores[int(rank[1])] if len(rank)>1 else -1))
        if scores[best]>=SPEAKER_MATCH and margin>=SPEAKER_MARGIN:output[i]=f'SPEAKER_{best:02d}'
    return output


import numpy as np

class SpeakerMemory:
    def __init__(self):self.prototypes=[]
    def identify(self,vector):
        vector=np.asarray(vector,dtype=np.float32);norm=np.linalg.norm(vector)
        known=[p for p in self.prototypes if p['speaker'] is not None]
        if not known or not np.isfinite(vector).all() or norm<=0:return None
        scores=[float(np.dot(vector/norm,p['vector'])) for p in known];rank=np.argsort(scores)[::-1]
        best=int(rank[0]);margin=scores[best]-(scores[int(rank[1])] if len(rank)>1 else -1)
        return known[best] if scores[best]>=SPEAKER_MATCH and margin>=SPEAKER_MARGIN else None
    def observe(self,vector,start,end):
        vector=np.asarray(vector,dtype=np.float32)
        norm=np.linalg.norm(vector)
        if not np.isfinite(vector).all() or norm<=0:return None
        if end-start<1.5:return self.identify(vector)
        vector=vector/norm
        scores=[float(np.dot(vector,p['vector'])) for p in self.prototypes]
        order=np.argsort(scores)[::-1] if scores else []
        best=int(order[0]) if len(order) else -1
        margin=scores[best]-(scores[int(order[1])] if len(order)>1 else -1) if scores else 0
        if best>=0 and scores[best]>=SPEAKER_MATCH and margin>=SPEAKER_MARGIN:
            item=self.prototypes[best]
        elif not scores or max(scores)<SPEAKER_NOVEL:
            if len(self.prototypes)>=16:return None
            item=dict(vector=vector,intervals=[],seconds=0.,speaker=None)
            self.prototypes.append(item)
        else:return None
        # Overlapping windows replay the same evidence; do not count it twice.
        fresh=all(min(end,b)-max(start,a)<=.1 for a,b in item['intervals'])
        if fresh:
            item['intervals'].append((start,end));item['seconds']+=end-start
            item['vector']=(item['vector']*.9+vector*.1)
            item['vector']/=np.linalg.norm(item['vector'])
        if item['speaker'] is None and item['seconds']>=SPEAKER_SUPPORT_SECONDS:
            count=sum(p['speaker'] is not None for p in self.prototypes)
            if count<8:item['speaker']='speaker_'+str(count+1)
        return item


def baseline_sentence_labels(words, tracks):
    """Keep sentence boundaries; reuse full-recording VBx identities, never recluster.

    Sum acoustic overlap per identity. Split sustained changes within a sentence,
    but absorb isolated word-level boundary jitter into its sentence majority.
    No acoustic coverage means unknown, not an invented identity.
    """
    result=[]
    for unit in record_sentences(words):
        scored=[]; totals={}
        for word in unit['words']:
            by_speaker={}
            for track in tracks:
                label=track.get('speaker')
                if label is None:continue
                a=max(word['start'],track['start']); b=min(word['end'],track['end'])
                if b>a:by_speaker.setdefault(label,[]).append((a,b))
                elif word['start']==word['end'] and track['start']<=a<track['end']:
                    by_speaker.setdefault(label,[]).append((a,a+.01))
            scores={}
            for label, intervals in by_speaker.items():
                right=float('-inf'); coverage=0
                for a,b in sorted(intervals):
                    coverage+=max(0,b-max(a,right)); right=max(right,b)
                scores[label]=coverage
            ranked=sorted(scores,key=scores.get,reverse=True)
            label=ranked[0] if ranked and (len(ranked)==1 or scores[ranked[0]]>scores[ranked[1]]+1e-6) else None
            for key,value in scores.items():totals[key]=totals.get(key,0)+value
            scored.append(dict(word,speaker=label))
        ranked=sorted(totals,key=totals.get,reverse=True)
        majority=ranked[0] if ranked and (len(ranked)==1 or totals[ranked[0]]>totals[ranked[1]]+1e-6) else None
        runs=[]
        for word in scored:
            if runs and runs[-1][0]['speaker']==word['speaker']:runs[-1].append(word)
            else:runs.append([word])
        runs,sustained=stable_acoustic_runs(scored)
        for group in runs if sustained else [scored]:
            label=group[0]['speaker'] if sustained else majority
            result.append(dict(unit,id=f'U{len(result)+1:04d}',start=group[0]['start'],end=group[-1]['end'],
                text=' '.join(w['word'] for w in group),speaker=label,
                words=[dict(w,speaker=label) for w in group]))
    return result


def acoustic_resegment(samples, tracks, embed, sample_rate=16000, include_vectors=False):
    """Audio-only multi-scale references and change-aware sequence decoding.

    Long windows establish voice references; short windows locate changes.
    Baseline labels seed references, but are not treated as ground truth.
    Fallback is explicit in diagnostics when two separable voices cannot be built.
    """
    import numpy as np
    tracks=sorted(tracks,key=lambda t:t['start'])
    cache={}
    def vector(a,b):
        key=(round(a*sample_rate),round(b*sample_rate))
        if key not in cache:
            clip=samples[max(0,key[0]):min(len(samples),key[1])]
            value=np.asarray(embed.at(a,b) if hasattr(embed,'at') else embed(clip),dtype=np.float32).reshape(-1) if len(clip) else np.array([])
            norm=float(np.linalg.norm(value))
            cache[key]=value/norm if value.size and np.isfinite(value).all() and norm>0 else None
        return cache[key]
    def starts(a,b,width,step):
        if b-a<width:return []
        values=list(np.arange(a,b-width+.001,step))
        if not values or b-width-values[-1]>.1:values.append(b-width)
        return values
    intervals=[]
    for t in tracks:
        a,b=float(t['start']),float(t['end'])
        if b<=a:continue
        if intervals and a<=intervals[-1][1]+.12:intervals[-1][1]=max(b,intervals[-1][1])
        else:intervals.append([a,b])
    if hasattr(embed,'prepare'):
        ranges=[]
        for t in tracks:
            a,b=float(t['start']),float(t['end'])
            ranges.extend((v,v+2.8) for v in starts(a+.08,b-.08,2.8,2.8))
            if .35<=b-a<2.96:ranges.append((a,b))
        for a,b in intervals:
            if b-a>=.8:
                width=min(1.6,b-a)
                ranges.extend((v,v+width) for v in starts(a,b,width,.4))
        embed.prepare(ranges)
    references={};reference_info={}
    labels=sorted({t['speaker'] for t in tracks if t.get('speaker') is not None})
    for label in labels:
        candidates=[]
        for t in tracks:
            if t['speaker']!=label:continue
            a,b=float(t['start']),float(t['end'])
            # Prefer independent 2.8s windows entirely within a baseline track.
            for start in starts(a+.08,b-.08,2.8,2.8):
                v=vector(start,start+2.8)
                if v is not None:candidates.append((v,start,start+2.8))
        # A speaker with two long but mutually inconsistent windows still
        # needs evidence from short turns. Never skip them just because two
        # long candidates exist (the previous v2 failure on rapid dialogue).
        for t in tracks:
            if t['speaker']==label and .8<=t['end']-t['start']<2.96:
                v=vector(t['start'],t['end'])
                if v is not None:candidates.append((v,t['start'],t['end']))
        if not candidates:
            reference_info[label]=dict(candidates=0,retained=0,reason='no_valid_embedding');continue
        matrix=np.stack([x[0] for x in candidates]);similarity=matrix@matrix.T
        def union_support(indices):
            total=0.;right=float('-inf')
            for i in sorted(indices,key=lambda i:candidates[i][1]):
                a,b=candidates[i][1:]
                total+=max(0,b-max(a,right));right=max(right,b)
            return total
        # Choose the best-supported coherent neighborhood. A mixed long
        # interval must not win just because it is longer than a brief reply.
        neighborhoods=[np.flatnonzero(row>=.55) for row in similarity]
        medoid=max(range(len(candidates)),key=lambda i:(len(neighborhoods[i]),union_support(neighborhoods[i]),float(np.median(similarity[i]))))
        keep=neighborhoods[medoid]
        center=matrix[keep].mean(axis=0);center/=max(float(np.linalg.norm(center)),1e-9)
        support=union_support(keep)
        reliable=len(keep)>=2 and support>=3
        reference_info[label]=dict(candidates=len(candidates),retained=len(keep),support_seconds=float(support),
            reliable=bool(reliable),median_similarity=float(np.median(similarity[medoid])),
            intervals=[[float(candidates[i][1]),float(candidates[i][2])] for i in keep],
            candidate_intervals=[[float(x[1]),float(x[2])] for x in candidates],
            candidate_cosine=similarity.round(4).tolist())
        if reliable:references[label]=center
    reference_labels=sorted(references)
    ready=len(reference_labels)>=2
    if ready:
        ref=np.stack([references[k] for k in reference_labels]);cross=ref@ref.T
        ready=bool(np.max(cross-np.eye(len(ref))*2)<.85)
    windows=[]
    for interval_id,(a,b) in enumerate(intervals):
        if b-a<.8:continue
        width=min(1.6,b-a)
        for start in starts(a,b,width,.4):
            end=start+width;v=vector(start,end)
            if v is None:continue
            coverage={label:sum(max(0,min(end,t['end'])-max(start,t['start'])) for t in tracks if t['speaker']==label) for label in labels}
            baseline=max(coverage,key=coverage.get) if coverage else None
            scores=[float(v@references[k]) for k in reference_labels]
            windows.append(dict(start=float(start),end=float(end),vector=v,baseline=baseline,scores=scores,interval=interval_id))
    # Viterbi over cosine emissions. A substantial acoustic change removes
    # the switch penalty; a silence starts a fresh sequence.
    decoded=[]
    if ready and windows:
        n=len(reference_labels);cost=None;backs=[]
        for i,w in enumerate(windows):
            emission=np.asarray(w['scores'],dtype=float)
            if cost is None or w['interval']!=windows[i-1]['interval']:
                if cost is not None:
                    state=int(np.argmax(cost));path=[state]
                    for back in reversed(backs[1:]):state=int(back[state]);path.append(state)
                    decoded.extend(reversed(path))
                cost=emission;backs=[np.arange(n)]
            else:
                change=float(w['vector']@windows[i-1]['vector'])<.4
                penalty=0. if change else .08
                transitions=cost[:,None]-penalty*(1-np.eye(n))
                back=np.argmax(transitions,axis=0)
                cost=emission+transitions[back,np.arange(n)];backs.append(back)
        state=int(np.argmax(cost));path=[state]
        for back in reversed(backs[1:]):state=int(back[state]);path.append(state)
        decoded.extend(reversed(path))
    for i,w in enumerate(windows):
        w['decoded']=reference_labels[decoded[i]] if decoded else None
    windows=verify_window_sequence(windows,reference_labels,ready)
    # Brief acoustic turns need their own crop. A 1.6s context may contain
    # two or three people and drown a half-second greeting in its neighbors.
    # These queries can identify existing references but cannot create one.
    for t in tracks:
        a,b=float(t['start']),float(t['end'])
        if not .35<=b-a<1.2:continue
        v=vector(a,b)
        if v is None:continue
        scores=[float(v@references[k]) for k in reference_labels]
        rank=sorted(range(len(scores)),key=lambda k:scores[k],reverse=True)
        best=rank[0] if rank else None
        margin=scores[best]-scores[rank[1]] if len(rank)>1 else 0.
        accepted=bool(ready and best is not None and scores[best]>=.45 and margin>=.20)
        windows.append(dict(kind='short_track',start=a,end=b,vector=v,baseline=t['speaker'],scores=scores,
            interval=-1,score=scores[best] if best is not None else None,margin=margin,verified=accepted,
            speaker=reference_labels[best] if accepted else t['speaker'],verification='short_direct' if accepted else 'baseline_fallback'))
    output=tracks_from_windows(tracks,windows)
    diagnostic=[dict(kind='reference_summary',policy='audio-reference-v2.1',ready=ready,references=reference_info,
        reference_labels=reference_labels,reason='ok' if ready else 'insufficient_or_indistinguishable_references')]
    diagnostic.extend(dict({k:v for k,v in w.items() if k!='vector'}, **({'embedding':w['vector'].tolist()} if include_vectors else {})) for w in windows)
    return output,diagnostic


def verify_window_sequence(windows, labels, ready):
    """Rescore stored cosine evidence; temporal agreement is not an extra sample.

    Short speech may have lower absolute cosine but clear relative separation.
    Permit that evidence only within a consistent temporal neighborhood.
    """
    import math
    windows=[dict(w) for w in windows]
    # Compute support once per contiguous acoustic run, not once per window.
    # Keep the exact winning-score/interval criteria used by the old scan.
    run_support=[0]*len(windows)
    keys=[];strong=[]
    for window in windows:
        values=window.get('scores',[])
        valid_run=(len(values)==len(labels) and bool(values) and
                   all(type(x) in (int,float) and math.isfinite(x) for x in values))
        winner=max(range(len(values)),key=lambda k:values[k]) if valid_run else None
        keys.append((window.get('interval'),winner) if valid_run else None)
        ranked_values=sorted(values,reverse=True) if valid_run else []
        strong.append(int(len(ranked_values)>1 and ranked_values[0]>=.30 and ranked_values[0]-ranked_values[1]>=.15))
    start=0
    while start<len(windows):
        end=start+1
        while keys[start] is not None and end<len(windows) and keys[end]==keys[start]:end+=1
        total=sum(strong[start:end])
        run_support[start:end]=[total]*(end-start)
        start=end
    for i,w in enumerate(windows):
        scores=w.get('scores',[])
        valid=(ready and len(labels)>=2 and len(scores)==len(labels)
               and all(type(x) in (int,float) and math.isfinite(x) and -1.001<=x<=1.001 for x in scores))
        rank=sorted(range(len(scores)),key=lambda k:scores[k],reverse=True) if valid else []
        chosen=rank[0] if rank else None
        score=scores[chosen] if chosen is not None else None
        margin=score-scores[rank[1]] if len(rank)>1 else 0.
        if w.get('kind')=='short_track':
            accepted=bool(valid and score>=.45 and margin>=.20)
            w.update(score=score,margin=margin,verified=accepted,speaker=labels[chosen] if accepted else w.get('baseline'),
                     verification='short_direct' if accepted else 'baseline_fallback')
            continue
        agrees=run_support[i]>=3
        direct=bool(valid and score>=.45 and margin>=.10)
        temporal=bool(valid and score>=.30 and margin>=.07 and agrees)
        # A sequence decoder is a regularizer, never permission to pick a
        # speaker contrary to the strongest acoustic score.
        decoded=w.get('decoded')
        accepted=(direct or temporal) and (decoded is None or decoded==labels[chosen])
        w.update(score=score,margin=margin,verified=bool(accepted),
                 speaker=labels[chosen] if accepted else w.get('baseline'),
                 verification='direct' if accepted and direct else 'temporal_relative' if accepted else 'baseline_fallback')
    return windows


def tracks_from_windows(tracks,windows):
    """Disjoint nearest-center regions avoid smearing across speaker changes."""
    output=[]
    centers=sorted([w for w in windows if w.get('kind')!='short_track' or w.get('verified')],key=lambda w:(w['start']+w['end'])/2)
    boundaries=[((a['start']+a['end'])+(b['start']+b['end']))/4 for a,b in zip(centers,centers[1:])]
    for t in tracks:
        cuts=sorted({float(t['start']),float(t['end'])}|{b for b in boundaries if t['start']<b<t['end']})
        for a,b in zip(cuts,cuts[1:]):
            mid=(a+b)/2
            nearby=[w for w in centers if w['start']<=mid<=w['end']]
            brief=[w for w in nearby if w.get('kind')=='short_track' and w.get('verified')]
            nearest=min(brief or nearby,key=lambda w:abs((w['start']+w['end'])/2-mid)) if nearby else None
            label=nearest['speaker'] if nearest and nearest.get('verified') else t['speaker']
            if output and output[-1]['speaker']==label and abs(output[-1]['end']-a)<1e-6:output[-1]['end']=b
            else:output.append(dict(start=a,end=b,speaker=label))
    return output


def stable_acoustic_runs(scored):
    runs=[]
    for word in scored:
        if runs and runs[-1][0]['speaker']==word['speaker']:runs[-1].append(word)
        else:runs.append([word])
    # A weak one-word edge must not veto a well-supported speaker change
    # elsewhere in the sentence. First locate sustained acoustic runs.
    strong=[i for i,r in enumerate(runs) if r[0]['speaker'] is not None and len(r)>=2 and r[-1]['end']-r[0]['start']>=.8]
    sustained=len({runs[i][0]['speaker'] for i in strong})>=2
    if sustained:
        for i,r in enumerate(runs):
            if i in strong:continue
            center=(r[0]['start']+r[-1]['end'])/2
            nearest=min(strong,key=lambda j:abs((runs[j][0]['start']+runs[j][-1]['end'])/2-center))
            for word in r:word['speaker']=runs[nearest][0]['speaker']
        runs=[]
        for word in scored:
            if runs and runs[-1][0]['speaker']==word['speaker']:runs[-1].append(word)
            else:runs.append([word])
    return runs,sustained


def overlay_verified_tracks(base, overlays):
    """Overlay confirmed short identities without overlapping Live intervals."""
    accepted=[t for t in overlays if t.get('speaker') and t.get('evidence',{}).get('accepted') is True]
    if not accepted:return base
    boundaries=sorted({t[k] for t in list(base)+accepted for k in ('start','end')})
    result=[]
    for a,b in zip(boundaries,boundaries[1:]):
        if b<=a:continue
        mid=(a+b)/2
        short=[t for t in accepted if t['start']<=mid<t['end']]
        original=[t for t in base if t['start']<=mid<t['end']]
        selected=max(short,key=lambda t:t['evidence'].get('margin',0)) if short else original[0] if original else None
        if selected is None:continue
        item=dict(selected,start=a,end=b)
        if result and result[-1]['end']==a and result[-1]['speaker']==item['speaker'] and result[-1].get('evidence')==item.get('evidence'):
            result[-1]['end']=b
        else:result.append(item)
    return result
