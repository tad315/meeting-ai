import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv('MEETING_AI_DATA', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect():
    db = sqlite3.connect(DATA / 'meetings.sqlite', timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def init():
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS meetings (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        for row in db.execute('SELECT id, payload FROM meetings').fetchall():
            item = json.loads(row['payload'])
            if (item.get('live_translation') or {}).get('enabled') and item['live_translation'].get('status')!='ready':
                state=dict(item['live_translation'],enabled=False,status='error',error='Dịch bị gián đoạn khi khởi động lại. Chọn chiều dịch để tiếp tục; bản dịch đã lưu vẫn còn.')
                item['live_translation']=state
                db.execute('UPDATE meetings SET payload=? WHERE id=?',(json.dumps(item),item['id']))
            if item.get('qa_status') == 'processing':
                item.update(qa_status='error',qa_error='Question interrupted. Ask again.')
                db.execute('UPDATE meetings SET payload=? WHERE id=?', (json.dumps(item), item['id']))
            if item.get('summary_status') == 'processing':
                item.update(summary_status='error', summary_error='Summary interrupted. Generate it again.')
                db.execute('UPDATE meetings SET payload=? WHERE id=?', (json.dumps(item), item['id']))
            if item.get('translation_status') == 'processing':
                item.update(translation_status='error', translation_error='Phiên dịch bị gián đoạn. Hãy thử lại.')
                db.execute('UPDATE meetings SET payload=? WHERE id=?', (json.dumps(item), item['id']))
            if item['status'] in ('recording', 'processing', 'queued', 'decoding', 'transcribing', 'cancelling'):
                item.update(status='interrupted', error='Processing was interrupted. Retry using the saved audio.')
                db.execute('UPDATE meetings SET payload=? WHERE id=?', (json.dumps(item), item['id']))


def create(title, source, provider):
    item = dict(id=uuid.uuid4().hex, title=title, source=source, provider=provider,
                created_at=datetime.now(timezone.utc).isoformat(), status='new', error=None,
                segments=[], speakers={}, summary=None, messages=[], revision=0,
                duration=0, audio=None, auto_summary_error=None)
    save(item)
    return item


def save(item):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO meetings VALUES (?, ?)', (item['id'], json.dumps(item)))


def get(mid):
    with connect() as db:
        row = db.execute('SELECT payload FROM meetings WHERE id=?', (mid,)).fetchone()
    return json.loads(row[0]) if row else None


def patch(mid, **changes):
    """Merge independent live/audio and assistant writes under one transaction."""
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT payload FROM meetings WHERE id=?',(mid,)).fetchone()
        if row is None:raise ValueError('Recording not found.')
        item=json.loads(row[0]);item.update(changes)
        db.execute('UPDATE meetings SET payload=? WHERE id=?',(json.dumps(item),mid))
    return item


def listing():
    with connect() as db:
        rows = db.execute('SELECT payload FROM meetings ORDER BY rowid DESC').fetchall()
    return [{k: v for k, v in json.loads(row[0]).items() if k not in ('segments', 'messages', 'summary', 'translation')} for row in rows]


def replace_segments(item, segments, final=False):
    item['segments'] = segments
    for segment in segments:
        speaker = segment.get('speaker')
        if speaker is not None:
            item['speakers'].setdefault(str(speaker), f'Speaker {int(speaker) + 1}')
        segment['original_text'] = segment.get('original_text', segment['text'])
        segment['provisional'] = not final
    item['revision'] += 1
    item['summary'] = None
    item['messages'] = []
    item['qa'] = []
    save(item)
