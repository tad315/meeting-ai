"""Wait for long GPU records without discarding jobs on transient heartbeat gaps."""
import asyncio
import json
import time
from . import community_queue
from .gpu_payloads import ERROR_MESSAGES
from .upload_engine import ProcessingCancelled


async def wait_for_record_result(folder, cancel, report, *, started=None, timeout=3600, poll_interval=2):
    started=time.monotonic() if started is None else started
    previous=None
    while True:
        if cancel.is_set():raise ProcessingCancelled()
        result=folder/'result.json'
        if result.exists():return result
        if (folder/'error.json').exists():
            try:code=json.loads((folder/'error.json').read_text()).get('code','processing_failed')
            except (OSError,ValueError,AttributeError):code='processing_failed'
            raise ValueError(ERROR_MESSAGES.get(code,ERROR_MESSAGES['processing_failed']))
        try:
            config=json.loads((folder/'session.json').read_text())
            worker=community_queue.worker_by_id(config['worker_id'])
        except (OSError,ValueError,KeyError):worker=None
        if worker is None:
            raise ValueError('Phiên kết nối GPU đã hết hạn hoặc được thay thế. Audio và transcript trước vẫn được giữ.')
        if time.monotonic()-started>timeout:
            raise ValueError('Hết thời gian chờ xử lý GPU. Kiểm tra kết quả đã lưu trên Kaggle trước khi chạy lại; audio vẫn được giữ.')
        state=community_queue.worker_status(worker)
        if not state['connected'] or state.get('startup_state')=='warming':
            current=('waiting_gpu_connection',None,'Đang chờ worker kết nối lại. Tác vụ vẫn được giữ để nhận kết quả; có thể hủy nếu muốn.')
        else:
            stage='community_worker';percentage=None
            try:
                progress=json.loads((folder/'progress.json').read_text())
                if progress.get('stage') in ('loading_models','recognizing_speech','identifying_speakers','verifying_speakers','returning_result'):
                    stage=progress['stage'];percentage=progress.get('progress')
            except (OSError,ValueError,AttributeError):pass
            current=(stage,percentage,None)
        if current!=previous:
            report(*current)
            previous=current
        await asyncio.sleep(poll_interval)
