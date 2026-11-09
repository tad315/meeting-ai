"""Bounded, authenticated inference jobs using the existing notebook bridge."""
import asyncio
import json
import time
import uuid
import zipfile
from . import community_queue as queue


def require_worker(feature):
    state = queue.status()
    if not state['connected'] or feature not in state.get('features', []):
        raise ValueError('Cần worker Kaggle phiên bản mới đang online. Vào Setup, kết nối và chạy notebook mới.')


def enqueue(task, audio=None):
    require_worker(task['kind'])
    worker = queue.current()
    folder = queue.root() / uuid.uuid4().hex
    folder.mkdir()
    task = dict(task, job_id=folder.name)
    with zipfile.ZipFile(folder/'job.zip', 'w', zipfile.ZIP_STORED) as z:
        z.writestr('task.json', json.dumps(task, ensure_ascii=False))
        if audio is not None:
            z.writestr('audio.wav', audio)
    config = dict(kind=task['kind'], worker_id=worker[0], expires=worker[2]['expires'],
                  token_hash=worker[2]['token_hash'])
    partial = folder/'session.part'
    partial.write_text(json.dumps(config))
    partial.replace(folder/'session.json')
    queue.publish_pending(worker, folder.name)
    return folder


async def wait(folder, timeout=600, poll_interval=.4):
    if not .05 <= poll_interval <= 1:
        raise ValueError('Invalid polling interval')
    started = time.monotonic()
    try:
        while True:
            if (folder/'error.json').exists():
                raise ValueError('Worker GPU xử lý thất bại. Kiểm tra output notebook rồi thử lại.')
            if (folder/'result.json').exists():
                result = json.loads((folder/'result.json').read_text(encoding='utf-8'))
                if result.get('job_id') != folder.name:
                    raise ValueError('Kết quả không khớp tác vụ đang chờ.')
                return result
            if not queue.status()['connected'] or (folder/'closed').exists():
                raise ValueError('Worker mất kết nối. Dữ liệu đã lưu vẫn được giữ lại.')
            if time.monotonic()-started > timeout:
                raise ValueError('Hết thời gian chờ GPU. Kiểm tra hàng đợi và notebook.')
            await asyncio.sleep(poll_interval)
    finally:
        (folder/'closed').touch()


async def chat(messages, schema, max_tokens):
    task = dict(kind='llm', messages=messages, schema=schema, max_tokens=max_tokens)
    if len(json.dumps(task, ensure_ascii=False)) > 100000:
        raise ValueError('Nội dung vượt giới hạn tác vụ LLM.')
    folder = await asyncio.to_thread(enqueue, task)
    result = await wait(folder, timeout=600)
    text = result.get('text')
    if not isinstance(text, str) or len(text) > 50000:
        raise ValueError('Worker trả về nội dung LLM không hợp lệ.')
    return text
