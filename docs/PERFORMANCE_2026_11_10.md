# Hiệu năng và ngữ cảnh — 10/11/2026

Mốc đối chiếu bản ghi 1700 giây: tổng 355.3 giây; worker 337.3 giây,
ASR 240.794 giây, diarization 79.581 giây, verification 12.659 giây.

- Record từ 60 giây dùng batch ASR 4, giảm 2/1 khi CUDA OOM. Live không đổi decoder.
- Bộ chấm acoustic run được tính một lần mỗi chuỗi thay vì lặp cho từng cửa sổ.
- Chat giữ nhiều điểm liên quan trước khi bổ sung câu liền kề; mở rộng tham chiếu tiếng Việt.
- Cache bộ nhớ tối đa 96 mục, 10 phút, tách theo ID cuộc họp/provider/nội dung chính xác.
  Chat thay transcript/history sẽ không dùng kết quả cũ. Summary chỉ dùng lại đoạn đã kiểm tra định dạng và ID.
- Tóm tắt tiếng Việt không áp dụng chỉ dẫn tranh biện vào cuộc họp thông thường.

Đã replay 3429 cửa sổ của bản 28 phút: kết quả kiểm tra nhãn giống trước.
Thời gian riêng hàm replay local 0.0505 -> 0.0103 giây, KHÔNG phải tăng tốc toàn pipeline.
Chưa có benchmark GPU đầu-cuối v2.8 hoặc đo WER/DER; không cam kết tăng độ chính xác.

Áp dụng: restart server VoxMeet để nhận logic assistant; tải worker v2.8 từ Setup,
đổi cell worker trong cùng runtime Kaggle nếu thư viện đã sẵn sàng. Không cần xóa cache model.
Không tự khởi động lại server, worker hoặc nhận dạng đè bản ghi.
