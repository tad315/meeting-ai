"""Compatibility adapter for shared Live/Record turn processing."""
import re
from .speech_turns import sentence_end, sentence_turns, clear_acoustic, label_turn

def validate_tracks(result,duration):
    import math
    tracks=result.get('tracks')
    if not isinstance(tracks,list) or len(tracks)>2000:raise ValueError('Nhãn speaker không hợp lệ.')
    clean=[]
    for track in tracks:
        if not isinstance(track,dict):raise ValueError('Nhãn speaker không hợp lệ.')
        a,b,label=track.get('start'),track.get('end'),track.get('speaker')
        if (any(type(x) not in (float,int) or not math.isfinite(x) for x in (a,b))
            or not 0<=a<b<=duration+.1 or label is not None and not re.fullmatch(r'speaker_[1-8]',str(label))):
            raise ValueError('Timestamp/nhãn speaker không hợp lệ.')
        evidence=track.get('evidence',{})
        # Legacy per-cluster IDs do not establish per-turn speaker support.
        trusted=(isinstance(evidence,dict) and evidence.get('policy')=='per-track-purity-v1'
            and evidence.get('accepted') is True
            and type(evidence.get('within_track_similarity')) in (float,int)
            and math.isfinite(evidence['within_track_similarity']) and .55<=evidence['within_track_similarity']<=1.001
            and type(evidence.get('support_seconds')) in (float,int)
            and math.isfinite(evidence['support_seconds']) and evidence['support_seconds']>=3)
        if isinstance(evidence,dict) and evidence.get('policy') in ('audio-reference-v1','audio-reference-v2','audio-reference-v2.1'):
            def finite_number(key,minimum,maximum):
                value=evidence.get(key)
                return type(value) in (int,float) and math.isfinite(value) and minimum<=value<=maximum
            trusted=(evidence.get('accepted') is True and finite_number('similarity',.60,1.001)
                     and finite_number('margin',.08,2.001) and finite_number('support_seconds',3,1000000))
        clean.append(dict(start=a,end=b,speaker=label if trusted else None))
    return clean
