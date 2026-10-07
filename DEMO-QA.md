# Chat về nội dung bản ghi

Mở bản ghi có transcript, tìm **Chat about this recording**. Nhập câu hỏi tiếng Anh, Enter để gửi; Shift+Enter để xuống dòng. Có thể soạn tin nhắn kế tiếp trong lúc chờ, rồi gửi khi câu trả lời trước hoàn tất.

Ví dụ:
- `How long is the nap?`
- `Is that in the morning?`
- `What about weekends?`

Lịch sử chat được lưu theo từng bản ghi và giữ lại khi tải trang. Model chỉ nhận tối đa 2 câu hỏi gần nhất khi cần hiểu câu hỏi nối tiếp; câu trả lời AI cũ không được dùng làm bằng chứng. Câu hỏi độc lập được đối chiếu lại với transcript. Đổi chủ đề không xóa lịch sử. Chỉnh sửa hoặc thay transcript sẽ xóa chat cũ để tránh dùng nguồn lỗi thời.

Mỗi lần trả lời có một nội dung chính, thường 1–2 câu; có thể liệt kê trong câu trả lời khi người dùng hỏi danh sách. Mục **Sources** mặc định đóng, có nguyên văn transcript và nút nghe. Lịch sử trò chuyện chỉ hỗ trợ hiểu câu hỏi tiếp nối, không thay thế bằng chứng từ transcript.

Ollama chạy local, CPU có thể mất vài phút. API tùy chọn gửi cả ngữ cảnh chat gần đây và transcript được chọn tới nhà cung cấp. Câu trả lời vẫn có thể sai hoặc suy luận nhầm người; kiểm tra Sources trước khi dùng.

Nếu lỗi, tin nhắn vừa gửi còn hiển thị và có **Retry last message**. Câu hỏi đang xử lý được đánh dấu gián đoạn nếu server khởi động lại; có thể gửi lại. Với bản ghi dài, tìm kiếm từ khóa có thể bỏ sót nguồn và giao diện sẽ ghi rõ chỉ dùng một phần transcript.

Tối ưu Q&A: chọn tối đa 8 lượt liên quan kèm lượt liền kề cho câu hỏi cụ thể; câu hỏi tổng quan dùng ngữ cảnh rộng hơn. Model Ollama được giữ trong RAM 5 phút sau câu hỏi để giảm thời gian nạp lại. Dẫn chứng dùng mã ngắn trong prompt, rồi ánh xạ về ID gốc và nguyên văn ở server. Nếu model trả lời lệch dạng câu hỏi có/không, ứng dụng có thể hiển thị Transcript excerpt để người dùng kiểm tra thay vì suy diễn. Việc kiểm tra nguồn không bảo đảm mọi suy luận đều đúng.
