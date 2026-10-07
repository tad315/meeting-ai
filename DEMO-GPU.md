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
