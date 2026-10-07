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

