# VoxMeet: GPU transcript kết nối với Colab

## Sử dụng

1. Mở demo cổng 8009, chọn bản ghi đã chuẩn bị audio.
2. Trong **GPU transcript · Community-1**, chọn **Connect this recording to Colab**.
3. Tải notebook riêng của bản ghi bằng nút xuất hiện bên cạnh. Mở Colab → File → Upload notebook.
4. Bật T4 và quyền notebook truy cập secret HF_TOKEN đã được chấp thuận Community-1.
5. Chạy setup; nếu có cài thư viện thì restart session theo hướng dẫn. Chạy ô xử lý cho audio này.
6. Notebook tự lấy audio, chạy Whisper small.en + Community-1 baseline rồi trả JSON về demo.
7. Giữ trang bản ghi mở; demo tự nhận kết quả, sao lưu transcript cũ, giữ audio và xóa summary cũ. Nghe kiểm tra rồi tóm tắt lại.

Đây là xử lý tương tác: mỗi audio vẫn cần bấm chạy ô notebook. Không dùng Colab miễn phí làm máy chủ GPU chạy nền cho ứng dụng web. Xem https://research.google.com/colaboratory/faq.html#limitations-and-restrictions.

## Truyền dữ liệu

Kết nối chỉ được mở khi bấm Connect. Audio đi qua Cloudflare tới Google Colab, kết quả đi theo chiều ngược lại. Dịch vụ truyền ở cổng 8010 tách biệt với giao diện và API chính cổng 8009; yêu cầu khóa riêng cho mỗi phiên. Mỗi phiên chỉ cấp audio đã chọn, hết hạn sau một giờ và đóng khi kết quả được áp dụng. Notebook có khóa tạm thời: không chia sẻ notebook đó.

Giữ demo và máy tính hoạt động trong khi Colab xử lý. Nếu link hết hạn hoặc máy tắt, tạo phiên mới. Nếu đã chỉnh transcript sau khi xuất notebook, kết quả cũ sẽ bị từ chối để tránh ghi đè chỉnh sửa. Có thể dùng mục Manual transfer (fallback) nếu mạng không cho dùng tunnel.

Công cụ cloudflared được tải từ bản phát hành chính thức Cloudflare và nằm ở `data/gpu-tools` trên ổ D. `setup-gpu-link.ps1` cài công cụ, không tự mở kết nối. Tắt demo sẽ đóng các tiến trình truyền do demo tạo (tắt cưỡng bức tiến trình có thể không chạy cleanup).

## Chất lượng

Không train lại. Dùng baseline revision 3533c8cf8e369892e6b79ff1bf80f7b0286a54ee đã đánh giá. Nghỉ tối đa 2,5 giây trong cùng nhãn giọng được ghép vào cùng lượt (giới hạn lượt 30 giây); đổi người nói thật vẫn tách. Quy tắc này không sửa lỗi model gán nhầm nhãn.

Local CPU vẫn dùng model cũ. Không tự sửa những transcript đã lưu trước đó. Summary là bản nháp có trích dẫn, cần kiểm tra nội dung và danh tính trước khi sử dụng.

## Kiểm chứng

30 kiểm thử backend đã qua, gồm auth của kênh truyền, hết hạn, dữ liệu nhầm/cũ, tự áp dụng và bảo toàn audio. Cú pháp notebook và giao diện đã kiểm tra. Chưa xác nhận đường truyền Cloudflare thực hoặc suy luận GPU trên Colab trong phiên này; cần người dùng cho phép truyền audio và chạy notebook.


## Record translation (2026-11-08)

Open a saved recording with a transcript. Select → Tiếng Việt or → English,
then Dịch bản ghi. Uses the existing Kaggle LLM worker (Qwen3-4B); no extra
notebook installation or worker version is needed if its llm capability is online.
Only transcript text is sent. Original text, timestamps, speaker labels, summary,
and chat remain unchanged. The complete translation is saved separately and can
be hidden or exported as bilingual .txt. This is text translation, not dubbing.

Transcript edits/reprocessing invalidate the displayed/exported translation.
Errors retain the previous complete translation. Restart marks interrupted
translation tasks as errors so they can be retried. Translation accuracy requires
GPU testing with English and Vietnamese recordings; structural validation cannot
guarantee faithful meaning. Live translation is not enabled.


## Shared Live/Record sentence pipeline — 2026-11-09

The authoritative implementation is app/speech_turns.py. Live imports its
accepted sentence splitter; session notebooks embed that exact source, including
a SHA256 fingerprint in Record provenance. No separate copied sentence rule
exists in the worker. The old REST default remains legacy for compatibility;
the Record UI explicitly selects the new experimental shared-sentence-v1 mode.
Baseline Community-1/VBx remains selectable for comparison.

New Record path: Whisper word timestamps -> shared sentence/change-boundary
units -> Community-1 speaker embeddings with within-unit consistency checks ->
complete-link cosine clustering over the full recording -> shared timestamp/
speaker representation. Short or ambiguous samples cannot create a new person.
Short samples may match a well-supported identity. Unknown output is preserved.
A sentence is not assumed to contain only one voice: sustained acoustic changes
can create subunits; isolated word-label fluctuations cannot fragment it.

Live sends settled sentence word timestamps with its acoustic window. The same
core constructs units and the same embedding extractor is used; identity memory
is incremental in Live instead of global batch clustering. First captions still
do not wait for the speaker job. Overlapping context is not double-counted as
new identity evidence. Both paths remain experimental for speaker accuracy.

Notebook code update is required once for this feature. No dependency reinstall
is needed in the already-working Kaggle environment. Download refresh preserves
the session URL/key. Two GPUs are required for parallel Live refinement; the
Record path itself works on one GPU. Wait for Speaker refinement ready for Live.
Old workers can use the legacy mode but are explicitly rejected for the new
Record mode rather than silently using their old grouping logic.

Saved word/timestamp preservation was checked on both the original MP3 ASR
result and the user's recaptured audio result. This preview does not test the
new embeddings on GPU. Formal diarization accuracy and display latency still
require GPU validation. Old recordings/edits/summaries/chats are not migrated or
reprocessed automatically; normal reprocessing retains the existing backup path.
