"""Bounded GPU result transport; diagnostics are not transcription evidence."""
MAX_GPU_RESULT_BYTES = 32 * 1024 * 1024

ERROR_MESSAGES = {
    'processing_failed': 'Worker GPU xử lý thất bại. Kiểm tra output Kaggle; audio và bản transcript trước vẫn được giữ.',
    'result_too_large': 'Kết quả GPU vượt giới hạn truyền dữ liệu. Bản JSON đã lưu trên Kaggle có thể được khôi phục; không cần tải lại audio.',
    'gpu_oom': 'GPU không đủ bộ nhớ khi xử lý. Audio vẫn được giữ; kiểm tra output Kaggle trước khi thử lại.',
    'asr_timeout': 'Nhận dạng audio vượt thời gian chờ. Kiểm tra file tiến độ trên Kaggle; audio vẫn được giữ.',
}


def compact_gpu_result(result):
    """Remove dense debug-only arrays without modifying words, tracks or scores.

    The complete result remains on the worker. The omitted candidate-cosine
    matrix is not used by server alignment validation or speaker assignment.
    """
    compact=dict(result)
    diagnostics=result.get('speaker_diagnostic')
    if isinstance(diagnostics,list):
        cleaned=[]
        for entry in diagnostics:
            row=dict(entry)
            references=row.get('references')
            if isinstance(references,dict):
                row['references']={label:{key:value for key,value in info.items() if key!='candidate_cosine'}
                                   for label,info in references.items()}
            row.pop('embedding',None)
            cleaned.append(row)
        compact['speaker_diagnostic']=cleaned
    return compact


def diagnostic_log_summary(info):
    """Small log, independent of recording length; no giant numeric matrices."""
    result={key:info.get(key) for key in ('kind','policy','ready','reason','reference_labels')}
    result['references']={label:{key:row.get(key) for key in
        ('candidates','retained','support_seconds','reliable','median_similarity')}
        for label,row in info.get('references',{}).items()}
    return result
