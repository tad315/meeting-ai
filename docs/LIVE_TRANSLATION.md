# Dịch Live song ngữ (v2.9)

## Sử dụng
1. Khởi động lại VoxMeet để nạp API và giao diện mới.
2. Tải notebook v2.9 trong Setup. Dùng Kaggle GPU T4 x2 + Internet.
3. Chạy setup và worker. Model NLLB tải/nạp trước khi nhận việc; chờ dòng
   `Live translation ready: English <-> Vietnamese (NLLB).`
4. Trong Live, chọn English → Tiếng Việt hoặc Tiếng Việt → English.
   Chọn chiều trước khi bắt đầu cũng đặt ngôn ngữ ASR tương ứng.
5. Transcript gốc hiện độc lập; bản dịch xuất hiện theo từng câu đã chốt.
6. Dừng + lưu: dịch tiếp các câu còn chờ, lưu vào cùng Record. Trong Record bật
   Hiện bản dịch và Xuất song ngữ .txt. Bản dịch dở được đánh dấu, không mất lời gốc.
7. Khi mất worker, chọn Thử lại sau khi kết nối lại. Không cần thu lại audio.

## Mô hình và giới hạn
NLLB-200 distilled 600M, mã ngôn ngữ eng_Latn/vie_Latn, trên GPU 1.
https://huggingface.co/facebook/nllb-200-distilled-600M
Giấy phép CC-BY-NC-4.0; dùng cho đồ án phi thương mại, ghi nguồn khi phân phối.
ASR GPU 0 vẫn chạy. Dịch và chat/tóm tắt dùng chung hàng đợi GPU 1, vì vậy một
câu hỏi hoặc bản tóm tắt dài có thể làm bản dịch chờ. Không cam kết độ trễ 1 giây.
Bản đầu dịch câu đã chốt, không dịch lại từng từ tạm thời. Chưa tự nhận diện luân
phiên Anh/Việt trong cùng câu. Không phát giọng dịch.

## Bảo toàn dữ liệu
Mỗi câu dịch gắn ID và hash nội dung nguồn. Kết quả muộn sau sửa câu/đổi chiều
bị loại. Nguồn gốc vẫn là căn cứ chat/tóm tắt. Bộ dịch không đổi nhãn người nói.
Lưu snapshot song ngữ có revision/fingerprint; xuất đánh dấu phần chưa dịch.
Khởi động lại server không tự gửi tiếp audio/text: người dùng bật lại dịch nếu dở.

## Kiểm chứng
Có kiểm thử backend bằng model giả cho stale result, append, đổi chiều, lỗi worker,
lưu/export và chia câu dài; không thay thế thử nghiệm GPU thật và nghe/đọc đối chiếu.


## Cập nhật dịch trung tính theo ngữ cảnh
Luồng Live hiện dùng Qwen3-4B đã nạp cho chat, không dịch NLLB rồi sửa lại.
Mỗi nhóm nhận thêm tối đa 3 câu nguồn trước đó để hiểu ngữ cảnh. Chỉ dịch CURRENT,
không dịch lại CONTEXT. Xưng hô mặc định tôi/bạn hoặc lược đại từ; không đoán giới tính.
Áp dụng tương tự cho dịch Record. Notebook v2.9 đang chạy vẫn nhận tác vụ LLM này;
chỉ cần restart backend VoxMeet. Bản dịch cũ không tự sửa: bật lại dịch để xử lý lại.
Chưa đo tốc độ/chất lượng GPU của chính sách neutral-context-v1; Qwen có thể chậm hơn NLLB.


### 2026-11-11: sentence-fast-v3
Live now publishes one translated turn at a time, using plain output for short
turns rather than generating a JSON batch. Previous context is bounded to two
turns; long turns retain the validated structured path. The bridge caps polls
with an active assistant exclusion at one second to refresh stale thread state.
No notebook replacement is required; restart the local server to load changes.

Two isolated GPU probes (no saved transcript modified) before restarting the
bridge: VI->EN 1.25s end-to-end / 0.423s generation; EN->VI 16.33s /
0.775s generation. These are diagnostic samples, not a latency percentile or
proof of post-fix performance. Translation quality remains imperfect (the
second probe translated a household idiom unnaturally). No 1–2s SLA is claimed.


### v3.0: revisable translation and independent intake
- GPU live ASR updates now feed a volatile latest-snapshot map (one per source).
- A single translation pump chooses final work first, then the latest draft.
  Obsolete draft results are discarded by generation and exact source checks.
- Draft previews are polled by the UI and replace the translation on the same
  provisional turn. They are never used as Record/chat/summary source data.
- Committed sentences retain the existing final translation/export path.
- On two GPUs the notebook uses two independent HTTP intake loops: audio and
  assistant. GPU1 no longer waits for the main ASR dispatch call to return.
  Chat/summary still share the assistant GPU; running generation is not preempted.
- Upgrade: restart local VoxMeet, download v3.0 notebook from Setup, stop the
  existing worker cell and run the new worker cell in the same Kaggle runtime.
  No package reinstall is needed if setup is already complete.
- Validation covers revision races, deleted previews, storage isolation, lane
  filtering, and independent assistant dispatch. Full browser/GPU latency with
  v3.0 has not yet been measured; 1–2 seconds is a target, not a verified SLA.
