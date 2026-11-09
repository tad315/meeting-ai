# Live meeting — bản thử nghiệm đầu tiên

Mở http://127.0.0.1:8009/live.html trong Chrome hoặc Edge.

1. Chọn Room microphone, Meeting tab/system audio hoặc cả hai.
2. Bấm Start meeting. Chọn tab cuộc họp và bật Share audio; sau đó cấp quyền micro.
3. Đợi dòng Recording locally, kiểm tra hai thanh mức âm thanh. Dùng tai nghe để tránh thu trùng tiếng loa.
4. Chữ xuất hiện khi nói, có thể thay đổi. Stop & save lưu audio và transcript vào Recordings.
5. Open saved meeting để nghe lại, sửa transcript hoặc chạy Colab GPU như bản upload; sau đó tóm tắt và hỏi đáp.

Nếu cửa sổ chia sẻ không cung cấp audio, phiên sẽ không bắt đầu. Với Zoom desktop, chỉ dùng lựa chọn chia sẻ có system audio thực sự; không phải mọi trình duyệt/cửa sổ đều hỗ trợ. Không cần truyền hình ảnh đến server: chỉ hai kênh PCM audio được gửi qua WebSocket local.

## Giới hạn hiện tại

- Nhãn nguồn micro/meeting audio không phải nhãn người nói. Chưa có diarization trực tiếp.
- Transcript tiếng Anh tạm thời; model streaming có thể sai từ, đặc biệt tiếng nhỏ, nhiễu hoặc nói chồng. Không có mô hình sinh văn bản sửa bịa nội dung.
- Mục tiêu chữ xuất hiện trong 1–2 giây chưa được xác nhận bằng micro thực. Thời gian xử lý một gói và queued audio không phải độ trễ từ lời nói tới chữ.
- Tối đa 30 phút, một phiên AI tại một thời điểm. Trang bị đóng/mạng ngắt chỉ bảo toàn phần server đã nhận. Chờ trạng thái Saved trước khi đóng.
- Audio có hai bản: live-sources.wav stereo (micro trái, cuộc họp phải), audio.wav mono cho xử lý cuối. Không xóa bản hai nguồn.
- Chưa có khôi phục kết nối liên tục hoặc cơ chế recording offline phía trình duyệt.

## Cài model

` .venv\Scripts\python.exe setup_live.py ` tải vào data/models/streaming-en-giga. Có thể cấu hình LIVE_MODEL_DIR để chọn model khác. Manifest ghi repository, revision và SHA256. Không cần API key; không sử dụng Colab làm server trực tiếp.

## Benchmark ngày 07/10/2026

Ryzen 3 5300U, model 2023-06-21 int8: 77.23 giây audio trên hai luồng có tiếng đồng thời mất 19.42 giây decode; RTF 0.251; p95 mỗi gói 200ms mất 144ms xử lý. Đây không phải độ trễ từ micro đến chữ hoặc điểm WER. Kiểm thử WebSocket bằng 28 giây audio tạo 36 cập nhật transcript, kênh im lặng không sinh chữ; 51 unit/integration tests đạt.



## Turbo Live integration — 2026-11-08

Live and recorded ASR now both use the warmed multilingual Whisper turbo model.
The worker returns its actual model name; Live displays it after the first result.
Live result polling is 100 ms instead of 400 ms. Other jobs retain 400 ms polling.
No automatic gain was enabled: Mix-Headset raw scored better than boosted audio
on the nine English dev crops (23.13% vs 25.37% offline WER).

The timing line distinguishes the remote job round trip and queued chunks. On a
new transcript update it also estimates how far the last word is behind captured
audio, at the next browser animation frame. This uses ASR word end timestamps;
it is a diagnostic estimate, not a synchronized end-to-end latency benchmark.
Queue wait/remote seconds/model are retained on the recording metadata.

This remains chunked Live (2–4 s boundaries, 1 s context overlap), not the
benchmark's rolling LocalAgreement decoder. Speaker processing still runs in
the worker before the chunk result is returned and can add latency. No 0.5–1 s
screen latency or accurate live speaker labels have been established.

Next session: run the demo from E:\VoxMeet\meeting-ai, download the newly generated
session worker notebook once from Setup and run it in a private Kaggle GPU
session. An already-running old worker cannot adopt Python changes automatically.
After a result arrives verify “Whisper turbo” in Live. Test tab audio alone first,
then room microphone separately. Keep an audio sample and the timing line for
comparison. Existing recordings, summaries, and chats were not reprocessed.


## Revisable captions — 2026-11-09

Live now decodes the growing current utterance, starting after one second of
available audio and scheduling subsequent snapshots every 0.5 s. An existing
draft card is replaced in place when recognition changes. Quiet boundaries or
the 12 s context limit settle the draft. Stop also settles pending captions.
There is only one in-flight decode and an event for newer audio, rather than a
FIFO of stale snapshots. Both sources are still processed sequentially, so the
network and inference remain practical latency limits.

The fast path no longer sends the per-chunk speaker session argument, avoiding
blocking ASR behind unstable Community-1 clustering. Labels explicitly identify
capture sources; reliable live speaker diarization remains unfinished. Existing
worker notebooks with live_asr capability are compatible; no worker replacement
is required for this server/UI change. Native streaming ASR and a 0.5–1 s browser
latency guarantee have not been established. Test on a real Kaggle worker.

Drafts are displayed immediately when each decode returns, but only settled
drafts are used by meeting summary/chat. All received audio is saved even if
recognition fails. Growing context may revise words and punctuation; it does
not guarantee verbatim transcription or correct speaker changes.


## Sentence boundaries and delayed speakers — 2026-11-09

Word timestamps are retained in each Live draft and saved sentence. Settling
splits by punctuation/long pauses, with abbreviation exceptions; a sentence
boundary never implies a speaker change. The existing source caption remains
the fast path. Community-1 is not run before returning ASR captions.

The new worker advertises live_refine only with two GPUs. It runs 12–30 second
acoustic windows on GPU 1 in a background thread, while ASR uses GPU 0. It uses
longer segments for speaker embeddings and conservative cross-window centroid
matches; uncertain voices remain unassigned. These thresholds are experimental
and have not been calibrated on Vietnamese/English live meetings. Label timing
is intentionally later than initial text and depends on speech/context/workload.

Refinement updates word-level speaker metadata and display spans while retaining
the original text and source IDs, so existing summary/chat citations still point
to their source turn. Manual edits/splits clear obsolete acoustic display spans.
Original recordings are not automatically reprocessed. Source IDs are separate
from inferred people; cross-source identity matching is not implemented.

Enable GPU T4 x2 in Kaggle. Stop the old worker cell, download the session
notebook from Setup and replace/run its worker cell only. No package reinstall
is needed if the previous environment passed preflight. The notebook download
refreshes code while retaining the active session URL/key. Old workers still
provide captions and sentence splitting but cannot run background refinement.
The relay must also be restarted with its existing instance marker to accept
the new capability; do not rotate a healthy tunnel solely for this code update.

GPU speech-quality and screen-latency validation remain required. Unit/integration
tests use simulated ASR/diarization responses; they do not establish label accuracy.


## Preserve accepted sentence splitting; optimize transport/speakers

The 2026-11-09 user test accepted sentence boundaries and reported ~4 s display
lag. No sentence-splitting rule was changed in this follow-up. The first actual
background speaker job took ~99 s and covered only the initial ~20 s of audio;
4/29 saved sentences received acoustic metadata. This is not successful diarization.

Changes: active per-worker pending index replaces repeated scans of historical
exports; authenticated long polling can return the job ZIP in the same response;
new workers reuse an HTTP session and exclude their currently running background
job. Legacy JSON discovery still works. This reduces avoidable transport work
but real end-to-end latency needs remeasurement.

Speaker model loading/warm-up now happens before live_refine is advertised.
Wait for 'Speaker refinement ready on GPU 1' for a clean test. Live can also
activate the capability during a session. Refinement advances in 12 s hops over
up to 30 s context, including catch-up windows at Stop. It no longer exits after
just the first slow result. Short turns sharing one acoustic label are combined
into a bounded speaker sample; thresholds remain provisional, not calibrated.
Timing of diarization/embedding and refined-through coverage is saved for review.

No weights were trained, no old recording was reprocessed, no sub-second latency
or speaker accuracy was established by the simulated tests. Update only the
worker cell in the existing Kaggle environment; dependency reinstall is unnecessary.


## Explicit source selection (2026-11-09)

The latest mixed-source test stored 29 tab captions plus 12 microphone captions.
This confirms the additional phrases originated on the microphone ASR path; it
does not establish whether each was real room speech or model hallucination.
The UI previously enabled both inputs by default. It now defaults to tab/system
audio and offers one explicit selector: tab, room mic, or combine both. Both is
never selected implicitly. Disabled source samples are also masked before saving
and recognition. No word blacklist, volume-only speech filter, sentence splitter
change, or historical transcript rewrite was applied. Neural VAD/hallucination
validation remains separate work; this fix prevents unintended source mixing.
Existing Kaggle workers remain compatible; only reload local server/page.


## Speaker safety correction (2026-11-09)

A real two-person test exposed a failure: several windows merged both voices
into one local cluster; later uncertain windows retained earlier wrong labels.
Per-word display spans also isolated words at slight timestamp gaps. This is
not considered successful live diarization. The accepted sentence splitter
and fast ASR path were not changed.

The server now requires per-track-purity-v1 evidence and revokes stale acoustic
metadata when updated support is uncertain. Single-label coverage is applied to
the whole sentence only with adequate word-duration coverage. Multiple labels
can split a sentence only with sustained multiword support on each side.
One-word changes keep the original sentence pending.

The revised worker verifies each acoustic track using subwindow embeddings,
rather than spreading a cluster label across all tracks. Identity support is
accumulated only from non-overlapping intervals; overlapping rolling windows
cannot repeatedly confirm the same sample. These thresholds remain experimental
and require a held-out speaker benchmark. Old workers' unverified tracks are
not applied, but their caption service remains compatible.

The specific failed recording was backed up in its data folder before clearing
automatic acoustic metadata. Its audio, source sentence IDs, text, timestamps,
and manual edits remain intact. No other recording was relabeled.
