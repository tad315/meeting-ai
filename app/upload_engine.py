"""CPU-only upload ASR and diarization, with cooperative cancellation."""
import threading
import wave

from .local_models import ASR, EMBEDDING, SEGMENTATION
from .transcript import assign_speakers, group_words

_lock = threading.Lock()
_asr = None


class ProcessingCancelled(Exception):
    pass


def run(path, num_speakers=None, report=None, cancel=None):
    global _asr
    report = report or (lambda stage, progress: None)
    cancel = cancel or threading.Event()

    def checkpoint(stage, progress):
        if cancel.is_set():
            raise ProcessingCancelled()
        report(stage, progress)

    with _lock:
        import numpy as np
        import sherpa_onnx
        from faster_whisper import WhisperModel

        checkpoint('loading_models', None)
        if _asr is None:
            _asr = WhisperModel(str(ASR), device='cpu', compute_type='int8', cpu_threads=4,
                                num_workers=1, local_files_only=True)
        with wave.open(str(path)) as audio:
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        duration = len(samples) / 16000
        checkpoint('recognizing_speech', 0)
        parts, info = _asr.transcribe(samples, language='en', beam_size=5, word_timestamps=True,
                                     vad_filter=True, condition_on_previous_text=False,
                                     vad_parameters={'min_silence_duration_ms': 500})
        words = []
        for part in parts:
            checkpoint('recognizing_speech', min(100, round(100 * part.end / max(duration, .1))))
            words.extend(dict(start=w.start, end=w.end, word=w.word, confidence=round(w.probability, 3))
                         for w in (part.words or []))
        if not words:
            checkpoint('saving', 100)
            return []
        checkpoint('identifying_speakers', 0)
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEGMENTATION)),
                num_threads=2, provider='cpu'),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMBEDDING), num_threads=2, provider='cpu'),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers or -1, threshold=.5),
            min_duration_on=.3, min_duration_off=.5)
        if not config.validate():
            raise ValueError('The local speaker models are invalid. Run setup.ps1 -LocalModels again.')
        diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)

        def on_progress(done, total):
            # Native API supports nonzero return to abort; do not raise through it.
            report('identifying_speakers', round(100 * done / max(total, 1)))
            return 1 if cancel.is_set() else 0

        result = diarizer.process(samples, callback=on_progress).sort_by_start_time()
        checkpoint('aligning_words', None)
        labels = {}
        turns = []
        for turn in result:
            labels.setdefault(turn.speaker, str(len(labels)))
            turns.append(dict(start=turn.start, end=turn.end, speaker=labels[turn.speaker]))
        segments = group_words(assign_speakers(words, turns))
        checkpoint('saving', 100)
        return segments
