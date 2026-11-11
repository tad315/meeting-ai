"""Bounded in-memory reuse; exact prompts/snapshots only, no disk transcript cache."""
import copy
import hashlib
import json
import os
import time
from collections import OrderedDict

_entries=OrderedDict()
MAX_ENTRIES=96
TTL_SECONDS=600

def key(scope,provider,payload):
    if not scope:return None
    configuration=[os.getenv(k,'') for k in ('OLLAMA_MODEL','LLM_API_MODEL','LLM_API_URL')]
    return hashlib.sha256(json.dumps([scope,provider,configuration,payload],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def get(token):
    if token is None:return None
    entry=_entries.get(token)
    if entry is None:return None
    expires,value=entry
    if time.monotonic()>=expires:
        _entries.pop(token,None);return None
    _entries.move_to_end(token)
    return copy.deepcopy(value)

def put(token,value):
    if token is None:return
    _entries[token]=(time.monotonic()+TTL_SECONDS,copy.deepcopy(value))
    _entries.move_to_end(token)
    while len(_entries)>MAX_ENTRIES:_entries.popitem(last=False)
