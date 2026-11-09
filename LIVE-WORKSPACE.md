# Phiên họp liên tục

- Trang Live khôi phục phiên gần nhất và transcript/chat từ server. Chỉ New meeting chuyển sang một phiên trống; bản cũ vẫn ở Recordings.
- Pause & save chốt phần audio đã nhận. Resume tiếp tục cùng ID, nối audio và transcript, giữ lịch sử trợ lý. Tổng thời lượng hiện giới hạn 30 phút.
- Recordings mở bên trong workspace, không hủy phần đang thu. Current meeting quay lại transcript và trợ lý.
- Summarize so far và chat chạy ngay trong live, dựa trên các lượt đã chốt. Mỗi kết quả có mốc thời gian nguồn. Các tóm tắt trước vẫn nằm trong Earlier summaries.
- Trang được tải lại hoặc đóng sẽ ngắt quyền thu audio của phiên trình duyệt. Dữ liệu đã nhận được giữ; bấm Resume và chọn nguồn/cấp quyền lại để tiếp tục. Khoảng thời gian không thu không được bù audio.
- Nhận diện lại là tùy chọn và thay transcript; trước khi chạy có bản sao transcript cũ trong thư mục dữ liệu của bản ghi. Không cần nhận diện lại để xem/tóm tắt/hỏi đáp transcript live đã lưu.
- Live và Ollama dùng chung CPU; thời gian trả lời phụ thuộc tải máy. Hỏi đáp chỉ thấy các lượt đã chốt, không dùng các chữ tạm đang thay đổi. Nhãn nguồn audio chưa phải nhận diện từng người.

Kiểm thử: 53 unit/integration tests, gồm audio nối tiếp sau Resume, bảo toàn nội dung/chat và kết quả AI được lưu trong khi audio mới tiếp tục đến. Các kiểm thử đồng thời dùng model giả để kiểm tra luồng dữ liệu; không phải chứng nhận chất lượng ASR/Q&A hay độ trễ trên micro thật.
