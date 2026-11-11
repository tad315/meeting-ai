# HƯỚNG DẪN CÀI ĐẶT VÀ KHỞI CHẠY VOXMEET


**Ngày cập nhật:** 10/11/2026  
**Phạm vi:** bản demo web Windows + worker Kaggle GPU, mã worker `live-assistant-v2.3`.  
**Thư mục chạy trên máy hiện tại:** `E:\VoxMeet\meeting-ai`.

## 1. Chạy lại nhanh trên máy đã cài

Cắm ổ E, kiểm tra ổ vẫn mang ký tự `E:`, mở terminal **PowerShell** trong VS Code và chạy:

```powershell
cd E:\VoxMeet\meeting-ai
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Port 8009
```

Mở **http://127.0.0.1:8009/**. Giữ terminal này chạy trong lúc sử dụng.

- Trang tổng quan: `http://127.0.0.1:8009/`
- Bản ghi: `http://127.0.0.1:8009/recordings`
- Họp trực tiếp: `http://127.0.0.1:8009/live`
- Cài đặt: `http://127.0.0.1:8009/recordings?setup=1`

Không cần kích hoạt `.venv` trước: `run.ps1` gọi trực tiếp Python trong môi trường đó. Luôn ghi `-Port 8009`; giá trị mặc định trong script hiện là **8008**.

Sau khi web chạy, thực hiện mục 5 để kết nối Kaggle. Không cần cài lại thư viện mỗi lần mở dự án.

## 2. Thành phần và nơi xử lý

| Thành phần | Vai trò | Nơi chạy |
| --- | --- | --- |
| Python, FastAPI, Uvicorn | API, lưu dữ liệu, điều phối tác vụ và phục vụ giao diện | Máy Windows |
| HTML, CSS, JavaScript | Giao diện web | Trình duyệt |
| FFmpeg | Đọc MP3 và các định dạng đầu vào, chuyển sang WAV mono 16 kHz | Máy Windows |
| Cloudflared + relay | Kết nối worker Kaggle với ứng dụng local | Máy Windows |
| faster-whisper / Whisper turbo đa ngôn ngữ | Chuyển lời nói Việt/Anh thành văn bản | Kaggle GPU |
| pyannote Community-1 / VBx | Phân đoạn và phân cụm người nói | Kaggle GPU |
| Bộ xử lý audio của VoxMeet | Đối chiếu embedding, ghép nhãn với mốc từ/câu | Kaggle và backend local |
| Qwen3-4B | Tóm tắt, hỏi đáp và dịch văn bản | Kaggle GPU |
| SQLite + thư mục `data` | Lưu bản ghi, transcript, nhãn, kết quả trợ lý và bản sao | Máy Windows |

Luồng chính: **trình duyệt → backend local → worker Kaggle → backend local → trình duyệt**.

Chế độ Kaggle không yêu cầu GPU trên máy cá nhân. Vẫn có xử lý local cho audio đầu vào, lưu dữ liệu và ghép kết quả; không phải mọi phép tính đều chuyển lên GPU.

Để chạy demo web này, không cần build ứng dụng desktop, chạy `npm install`, cài CMake hoặc tải lại dataset huấn luyện AMI. Worker hiện tại chạy **suy luận**, không tiếp tục train checkpoint cũ. Ollama và Deepgram là lựa chọn khác, không bắt buộc khi dùng Kaggle.

## 3. Chuẩn bị trên máy mới

### 3.1. Phần mềm và tài khoản

- Windows 10/11 64-bit, PowerShell.
- Python 64-bit. Script setup của dự án hướng dẫn Python **3.12**; máy hiện tại đang dùng **3.14.6**. Không cần đổi Python của máy hiện tại chỉ để chạy lại.
- VS Code để mở mã nguồn và terminal; có thể dùng PowerShell riêng nếu không dùng VS Code.
- Chrome hoặc Edge cho thu mic và chia sẻ âm thanh cuộc họp.
- FFmpeg chạy được trên Windows.
- Tài khoản Kaggle có quyền bật GPU và Internet cho notebook.
- Tài khoản Hugging Face có quyền đọc model Community-1 và token tương ứng.
- Kết nối Internet giữa máy local, Kaggle, Hugging Face và Cloudflare.

Python local và Python trên Kaggle là hai môi trường độc lập. Không sao chép thư viện từ máy sang Kaggle hoặc ngược lại.

### 3.2. Đặt đúng mã nguồn

Thư mục tối thiểu cần có:

```text
E:\VoxMeet\meeting-ai\
├── app\
├── web\step-one\
├── colab\community_worker.ipynb
├── tests\
├── requirements.txt
├── requirements-local.txt
├── setup.ps1
├── setup-gpu-link.ps1
├── run.ps1
└── .env.example
```

Tên thư mục `colab` là tên còn giữ trong mã nguồn; notebook này được dùng cho Kaggle. Không vì tên đó mà chuyển sang chạy trên Colab.

`E:\VoxMeet\AI_Speech_to_text` là workspace khác; entrypoint của bản demo đang sử dụng nằm trong **`E:\VoxMeet\meeting-ai`**. Khi chuyển sang máy mới, cần sao chép đúng thư mục demo này.

Không dùng lại `.venv` được chép từ máy khác. Tạo môi trường mới; `.venv` có thể chứa đường dẫn gắn với máy/Python cũ.

### 3.3. Tạo môi trường và cài backend

Các lệnh dưới đây dành cho **máy mới**, chạy trong PowerShell:

```powershell
cd E:\VoxMeet\meeting-ai
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
.\.venv\Scripts\python.exe -m pip install "numpy>=2,<3"
```

`setup.ps1` cài `requirements.txt` và tạo `.env` từ `.env.example` nếu chưa có. Bước NumPy được ghi riêng vì backend Live đang import NumPy nhưng `requirements.txt` hiện chưa liệt kê nó trực tiếp. Không bỏ qua bước này trên môi trường mới.

Kiểm tra import, không khởi động server:

```powershell
.\.venv\Scripts\python.exe -c "import numpy, fastapi, uvicorn; import app.step_one; print('Backend import: OK')"
```

Nếu lệnh `py -3.12` không tồn tại, cài Python 3.12 64-bit cùng Python Launcher trước. Không tạo thêm môi trường đè lên môi trường đang hoạt động của máy hiện tại.

### 3.4. Cài FFmpeg

Kiểm tra trước:

```powershell
ffmpeg -version
```

Nếu đã hiện thông tin phiên bản, có thể dùng FFmpeg trong PATH. Nếu chưa có, lấy bản Windows từ các liên kết tại [trang tải FFmpeg](https://ffmpeg.org/download.html), giải nén vào thư mục trên ổ E, ví dụ:

```text
E:\VoxMeet\tools\ffmpeg\bin\ffmpeg.exe
```

Trong `.env`, điền đường dẫn **thực tế**:

```dotenv
FFMPEG_PATH=E:/VoxMeet/tools/ffmpeg/bin/ffmpeg.exe
```

Đây là đường dẫn ví dụ, không phải file mà setup tự tạo. Nếu dùng FFmpeg trong PATH thì có thể để `FFMPEG_PATH` trống.

Kiểm tra cách backend tìm FFmpeg:

```powershell
.\.venv\Scripts\python.exe -c "from app.providers import ffmpeg_path; print(ffmpeg_path())"
```

Nếu để trống, backend thử PATH rồi mới thử binary ở thư mục `frontend/src-tauri/binaries` bên cạnh dự án. Không nên phụ thuộc đường dẫn binary cũ khi cài sang máy mới.

### 3.5. Chuẩn bị cầu nối Kaggle

Chạy một lần để tải công cụ Cloudflare vào `data\gpu-tools`:

```powershell
cd E:\VoxMeet\meeting-ai
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-gpu-link.ps1
```

Script có thể in thông báo cũ nhắc Colab. Sau khi công cụ được tải, thao tác kết nối thực hiện trong **Setup của VoxMeet → worker Kaggle**.

Với chế độ Kaggle, không cần điền khóa Deepgram/Ollama trong `.env`. Các biến đó chỉ dùng khi chủ động chọn nhà cung cấp tương ứng.

## 4. Khởi động và kiểm tra backend

```powershell
cd E:\VoxMeet\meeting-ai
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Port 8009
```

Dấu hiệu thành công: Uvicorn báo chạy tại `http://127.0.0.1:8009`, không có lỗi bind cổng.

Mở một terminal PowerShell khác để kiểm tra:

```powershell
Invoke-RestMethod http://127.0.0.1:8009/api/health
```

`ffmpeg_available` cần là `True` để nhập MP3 và chuẩn hóa audio. Các mục local/Ollama chưa sẵn sàng không cản trở chế độ Kaggle. Health thành công chỉ xác nhận backend, chưa xác nhận worker hay độ chính xác model.

Ứng dụng mặc định chỉ lắng nghe `127.0.0.1`; không tự đổi sang `0.0.0.0` để chạy demo trên máy này.

## 5. Kết nối Kaggle GPU

### 5.1. Chuẩn bị Hugging Face

1. Đăng nhập Hugging Face.
2. Mở [model Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) và chấp nhận điều kiện truy cập bằng chính tài khoản sẽ cấp token.
3. Tạo token có quyền đọc model tại [Hugging Face Tokens](https://huggingface.co/settings/tokens).
4. Lưu token trong **Kaggle Secrets**, tên chính xác **`HF_TOKEN`**, và cấp quyền cho notebook sử dụng secret này.

Không dán token vào đoạn chat, output công khai hoặc commit vào mã nguồn.

### 5.2. Tải notebook từ đúng phiên VoxMeet

1. Giữ backend local đang chạy.
2. Vào **Cài đặt / Setup → Connect Kaggle worker**. Nếu đã có phiên hợp lệ, dùng phiên hiện có; chỉ tạo phiên thay thế khi cần.
3. Bấm tải notebook phiên hiện tại. Có thể dùng [đường dẫn tải local](http://127.0.0.1:8009/api/community-worker/notebook) sau khi đã cấu hình phiên.
4. Trên Kaggle, tạo/mở notebook **Private** và nhập file vừa tải.

Phải tải từ VoxMeet: file đó chứa địa chỉ và khóa kết nối của phiên. File mẫu `colab/community_worker.ipynb` trong mã nguồn còn placeholder nên không dùng trực tiếp như một notebook đã kết nối.

Không công khai notebook phiên vì nó có thông tin xác thực tạm thời của cầu nối.

### 5.3. Chọn môi trường Kaggle và chạy

1. Bật **Internet**.
2. Chọn **GPU T4 ×2** nếu tài khoản còn quota. Bản Live hiện quảng bá khả năng tinh chỉnh nhãn khi có hai GPU.
3. Thêm và bật secret `HF_TOKEN` cho notebook.
4. Chạy **cell cài thư viện**.
5. Nếu cell yêu cầu restart: chọn **Run → Restart & clear cell outputs**.
6. Sau restart, chạy **chỉ cell worker**, không Run All.

Cell cài đặt hiện pin `numpy==2.2.6`, `pyannote.audio==4.0.7`, `faster-whisper==1.2.1` và giữ các gói Torch/CUDA có sẵn khi tạo constraints. Không tự thay Torch GPU bằng bản CPU.

Các dòng cần thấy ở bản này:

```text
Checking NumPy, pyannote and GPU before accepting jobs...
Preflight OK: 2.2.6 | Tesla T4
VoxMeet v2.5: preload Vietnamese ASR and chat before accepting jobs
Preparing Vietnamese live ASR on GPU 0 (downloads only if not cached)...
Preparing Qwen3-4B chat on GPU 1. Please wait before starting the meeting...
Chat ready: Qwen3-4B is loaded and warmed on GPU 1.
Worker running. ASR ready; chat ready. Stop this cell to disconnect.
```

Live chỉ dùng nhãn nguồn mic/tab. Không cần đợi thông báo model phân nhãn Live; phân nhãn người nói được thực hiện trong Record. Với hai GPU, trợ lý có thể chạy nền để luồng nhận ASR tiếp tục.

Giữ cell worker chạy trong lúc dùng Record, Live hoặc trợ lý Kaggle. Chờ chuẩn bị model ở đầu phiên, không phải gửi một câu hỏi để kích hoạt tải. Giao diện chỉ báo sẵn sàng sau khi chuẩn bị xong. Tải model lần đầu có thể mất thời gian. Quota và thời lượng phiên phụ thuộc tài khoản Kaggle.

### 5.4. Kiểm tra kết nối từ Windows

```powershell
Invoke-RestMethod http://127.0.0.1:8009/api/community-worker
```

- `configured=True`: đã có cấu hình phiên.
- `connected=True`: worker có heartbeat gần đây, kể cả lúc đang chuẩn bị model.
- `startup_state=warming`: đang tải/nạp model, chưa nhận tác vụ; `ready`: đã khởi động xong.
- `assistant_ready=True`: model chat đã được nạp và chạy thử (chế độ hai GPU).
- Worker cũ không báo trạng thái nạp model sẽ có `assistant_state=unknown`; không coi là đã nạp sẵn.
- `features` có `live_asr` và `llm`: worker hỗ trợ nhận chữ Live và trợ lý. Live không yêu cầu `live_refine`.

**Connected không chứng minh đúng bản code.** Luôn kiểm tra dòng `VoxMeet v2.5` trong output Kaggle; notebook vừa tải không tự cập nhật cell đang chạy trong notebook cũ.

## 6. Thử chức năng Record

1. Mở **Bản ghi / Recordings**, tải một audio rõ, ngắn, có hai người nói.
2. Chọn **ngôn ngữ audio** đúng: English cho bài hoàn toàn tiếng Anh, Tiếng Việt cho bài tiếng Việt, hoặc Tự nhận diện khi chưa biết. Ngôn ngữ giao diện và ngôn ngữ tóm tắt là lựa chọn riêng.
3. Chọn **Community-1 · Worker Kaggle**.
4. Chọn **Chia câu Live + Community-1 / VBx**.
5. Để số người nói tự động trước; chỉ cố định khi biết chắc số người thực sự trong audio.
6. Bấm tạo transcript, đợi worker trả kết quả tự động.
7. Nghe đối chiếu các mốc đổi người, thử sửa câu/nhãn, đổi tên và xuất transcript.

Đầu vào hiện giới hạn **100 MB và 30 phút**; hỗ trợ WAV, MP3, M4A, MP4, FLAC, OGG, WebM. FFmpeg chuẩn hóa audio trước khi gửi worker.

Không chọn bắt buộc `vi` cho một bài hoàn toàn tiếng Anh. Trong lần thử thực tế của dự án, cấu hình này đã gây nội dung tiếng Việt bất thường và mất một số đoạn tiếng Anh.

### Tóm tắt, hỏi đáp và dịch

- Chọn nhà cung cấp **Kaggle GPU** để dùng trợ lý trên worker.
- Chọn ngôn ngữ đầu ra tóm tắt riêng với ngôn ngữ audio.
- Dùng khung chat cho các câu hỏi liên tiếp về bản ghi.
- Chat phân biệt câu hỏi dữ kiện, nguyên nhân, suy luận và kiểm tra một kết luận. Suy luận phải dựa trên nội dung đã có; câu hỏi về kết luận mạnh có thêm bước kiểm tra dẫn chứng có đủ hỗ trợ kết luận hay không. Không suy tính cách/động cơ từ một lỗi kỹ năng.
- Một câu trả lời có ID nguồn hợp lệ vẫn có thể suy luận sai. Cần nghe đối chiếu các kết luận quan trọng; kiểm tra nguồn không thay thế đánh giá ý nghĩa.
- Dịch Record được lưu riêng; kiểm tra bản gốc và bản dịch khi sử dụng.
- Với worker v2.5 và hai GPU, Qwen3-4B được tải/nạp và chạy thử ngay khi khởi động. Chờ `Chat ready` trước khi bắt đầu; câu hỏi dùng lại model đã nạp. Cache mới có thể cần tải khoảng 8 GB trọng số trên Kaggle. Một GPU vẫn dùng chế độ tuần tự/nạp khi cần và hiển thị cảnh báo riêng.
- Sửa transcript có thể làm kết quả trợ lý trước đó hết phù hợp hoặc bị xóa/đánh dấu cũ theo luồng ứng dụng; xuất nội dung cần giữ trước khi nhận dạng lại.

## 7. Thử chức năng Live

1. Đợi worker báo đang chạy rồi mở **Họp trực tiếp / Live meeting**.
2. Chọn nguồn âm thanh đúng với bài thử:
   - Video/Meet trong trình duyệt: chọn **audio tab cuộc họp**.
   - Nói trực tiếp trong phòng: chọn **mic trong phòng**.
   - Chỉ bật cả hai khi thực sự cần ghi cả hai nguồn; tiếng loa lọt vào mic có thể gây trùng hoặc nhận dạng không mong muốn.
3. Chọn ngôn ngữ lời nói và chế độ **Kaggle GPU**.
4. Cấp quyền mic/chia sẻ màn hình hoặc tab. Khi chia sẻ tab, bật **chia sẻ âm thanh tab** nếu trình duyệt cung cấp.
5. Bắt đầu họp; theo dõi chữ tạm và nhãn cập nhật sau.
6. Dừng/tạm dừng và lưu trước khi đóng trang hoặc tắt server.
7. Mở bản đã lưu trong Recordings để kiểm tra audio và transcript.

Máy có thể ghi được tín hiệu nhưng nguồn chọn sai vẫn khiến model nghe sai nội dung. Mặc định nên thử từng nguồn riêng trước khi phối hợp.

Live dùng chữ tạm có thể sửa trong lúc cập nhật và chỉ hiển thị nguồn mic/tab. Bản v2.3 không gửi tác vụ phân nhãn trong Live, không chờ phân nhãn khi dừng. Tóm tắt/chat dùng transcript đã chốt và hiển thị mốc thời gian của snapshot. Khi cần phân biệt từng người, mở audio đã lưu trong Record và chạy Community-1. Độ trễ vẫn phụ thuộc chốt lời, mạng và GPU; chưa cam kết 0,5–1 giây.

## 8. Cập nhật code và notebook

### Chỉ cập nhật giao diện

Tải lại trang bằng `Ctrl+F5` sau khi cập nhật file. Nếu đổi backend, dừng server rồi khởi động lại.

### Cập nhật thuật toán hoặc worker

1. Dừng phiên Live đang thu và đợi tác vụ hiện tại xong.
2. Dừng backend bằng `Ctrl+C`, khởi động lại `run.ps1 -Port 8009`.
3. Dừng cell worker cũ trên Kaggle.
4. Tải notebook mới từ phiên hiện tại trên VoxMeet.
5. Thay **toàn bộ cell worker** bằng code mới hoặc nhập notebook mới.
6. Không cần cài lại thư viện nếu bản cập nhật không đổi dependencies.
7. Kiểm tra dòng phiên bản trong log rồi mới thử audio.

Nếu cầu nối/URL vẫn hợp lệ, không cần bấm tạo phiên kết nối thay thế. Nếu đã đóng Cloudflare hoặc địa chỉ công khai cũ chết, vào Setup để kết nối lại và dùng notebook chứa địa chỉ phiên mới.

Không chỉ sửa chuỗi in phiên bản trong cell cũ: thuật toán chung được nhúng trong `COMMON_TURN_SOURCE`, nên cần thay đầy đủ cell đã sinh từ server.

## 9. Dừng dự án và mở lại

1. Dừng và lưu Live; chờ tác vụ cần giữ hoàn tất.
2. Nhấn `Ctrl+C` tại terminal chạy Uvicorn.
3. Dừng cell worker Kaggle khi không dùng để tránh tiêu tốn phiên GPU.

Relay cổng **8010** và Cloudflare có thể còn chạy sau khi dừng server 8009 để giữ kết nối giữa các lần mở web. Nếu muốn tắt toàn bộ dịch vụ dự án, đoạn dưới chỉ chọn tiến trình thuộc đúng đường dẫn VoxMeet:

```powershell
$voxTargets = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq 'python.exe' -and
     $_.CommandLine -like '*E:\VoxMeet\meeting-ai*' -and
     $_.CommandLine -match 'app\.(gpu_bridge|step_one)') -or
    ($_.Name -eq 'cloudflared.exe' -and
     $_.ExecutablePath -eq 'E:\VoxMeet\meeting-ai\data\gpu-tools\cloudflared.exe')
}
$voxTargets | Select-Object ProcessId, Name, CommandLine
# Sau khi kiểm tra danh sách trên đúng là dịch vụ VoxMeet:
$voxTargets | ForEach-Object {
    Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
}
```

Nếu cài ở thư mục khác, sửa điều kiện đường dẫn tương ứng. Không dùng lệnh tắt tất cả `python.exe` hoặc tất cả cổng trên Windows.

Kiểm tra cổng dự án:

```powershell
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 8009, 8010 } |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

Mở lại dự án bằng lệnh ở mục 1. Nếu đã tắt cầu nối, kiểm tra lại kết nối Kaggle theo mục 5.

## 10. Lỗi thường gặp

| Triệu chứng | Kiểm tra và xử lý |
| --- | --- |
| `WinError 10048`, không bind được 8009 | Đã có tiến trình nghe cổng. Kiểm tra PID bằng `Get-NetTCPConnection`; kiểm tra tiến trình rồi dừng đúng server cũ. Không chạy nhiều terminal cùng mở 8009. |
| Không thấy thư mục/venv sau khi cắm ổ | Kiểm tra ký tự ổ E và thư mục `E:\VoxMeet\meeting-ai`; môi trường `.venv` có thể cần tạo lại nếu Python nền đã bị gỡ hoặc đổi máy. |
| `No module named numpy` | Chạy `.\.venv\Scripts\python.exe -m pip install "numpy>=2,<3"` trong thư mục dự án rồi kiểm tra import. |
| Thiếu FFmpeg, MP3 không giải mã | Kiểm tra `ffmpeg -version`, `FFMPEG_PATH` và file thật; khởi động lại backend sau khi sửa `.env`. |
| `GPU khả dụng: False` / `Enable GPU first` | Bật GPU trong Kaggle, chạy lại cell kiểm tra. GPU của Kaggle tách biệt GPU trên máy Windows. |
| NumPy import lỗi sau cài gói | Restart kernel Kaggle theo chỉ dẫn, sau đó chạy **chỉ worker**. Nếu vẫn lỗi, giữ traceback để kiểm tra; không sửa ngẫu nhiên hàng loạt package. |
| `pip check` báo xung đột với gradio/moviepy/... | Cảnh báo có thể thuộc gói khác có sẵn trong Kaggle. Kiểm tra cài đặt thành công và preflight thực sự qua; không mặc định mọi cảnh báo đều vô hại. |
| HF 401/403 / gated model | Kiểm tra `HF_TOKEN`, quyền secret và tài khoản đã chấp nhận điều kiện Community-1. Không gửi token khi báo lỗi. |
| `libcublas.so.12` không tải được | Worker hiện có bước chuẩn bị CUDA libraries riêng cho faster-whisper. Dùng cell mới và gửi traceback nếu bước đó thất bại; không thay Torch bằng bản CPU. |
| Worker online nhưng thuật toán vẫn cũ | Kiểm tra dòng `Speaker engine` trong Kaggle. Dừng cell và thay đầy đủ code từ notebook vừa tải. |
| `Connection interrupted; retrying...` | Kiểm tra backend, relay 8010, Internet và đường hầm Cloudflare. Cell tự thử lại; nếu URL đã đổi cần dùng notebook kết nối mới. |
| Có chữ quảng cáo/tiếng Việt trong bài tiếng Anh | Kiểm tra ngôn ngữ audio có đang bị ép `vi`; chọn English và nhận dạng lại. Vẫn cần nghe đối chiếu vì model có thể nhận sai. |
| Chat/tóm tắt báo không tới Ollama | Chọn nhà cung cấp Kaggle GPU nếu không cài Ollama; worker phải còn hoạt động. |
| Nhãn `SPEAKER_00`, `01` đổi số giữa hai lần chạy | ID nhóm là tùy ý. So sánh cùng giọng có được giữ nhất quán không, không chỉ so số ID. |
| Các câu ngắn vẫn gán sai | Lưu mốc thời gian, lời đúng, người nói tham chiếu và log phiên bản. Phân biệt chỉnh tay với kết quả thuật toán để đánh giá đúng. |

## 11. Dữ liệu, sao lưu và di chuyển

- Database: `E:\VoxMeet\meeting-ai\data\meetings.sqlite`.
- Audio, transcript GPU gốc, bản sao trước chỉnh sửa và dữ liệu phiên: trong `data\`.
- Model local tùy chọn: `data\models\`.
- Công cụ Cloudflare: `data\gpu-tools\cloudflared.exe`.
- Cấu hình riêng: `.env`.

Trước khi sao lưu, dừng tác vụ và server để có bản sao database nhất quán. Sao lưu **toàn bộ `data`**, mã nguồn và `.env` nếu cần giữ cấu hình; cất bản có secret ở nơi riêng tư. Chỉ xuất TXT không đủ khôi phục audio, lịch sử và cấu hình bản ghi.

Khi chuyển máy, tạo lại `.venv`. Một số dữ liệu audio lưu đường dẫn tuyệt đối, nên ưu tiên giữ cấu trúc `E:\VoxMeet\meeting-ai`; nếu đổi đường dẫn cần kiểm tra lại khả năng mở audio cũ. Không xóa bản gốc trước khi kiểm tra bản sao dùng được.

Không cần tải dataset train từ Drive/Kaggle về máy chỉ để sử dụng demo. Nếu thực hiện huấn luyện riêng, backup checkpoint/dataset theo quy trình khác; không nhầm chúng với dữ liệu cuộc họp trong `data`.

## 12. Checklist nghiệm thu sau cài

- [ ] Backend chạy tại đúng cổng 8009.
- [ ] `/api/health` trả thành công và nhận được FFmpeg.
- [ ] Import backend không lỗi thiếu thư viện.
- [ ] Kaggle bật Internet, GPU; preflight qua.
- [ ] Kaggle thấy đúng dòng `VoxMeet v2.5`.
- [ ] Live chỉ hiển thị nguồn mic/tab; phân nhãn người nói thực hiện trong Record.
- [ ] VoxMeet báo worker connected.
- [ ] Một audio English ngắn được nhận dạng bằng English và trả kết quả tự động.
- [ ] Một audio tiếng Việt được nhận dạng bằng cấu hình phù hợp.
- [ ] Live thu đúng nguồn, có chữ cập nhật, lưu và nghe lại được audio.
- [ ] Tóm tắt/chat dùng được khi chọn Kaggle; ngôn ngữ đầu ra đúng lựa chọn.
- [ ] Đổi tên, sửa nhãn, xuất và mở lại bản ghi hoạt động.

Kiểm thử kỹ thuật của dự án:

```powershell
cd E:\VoxMeet\meeting-ai
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Bộ test hiện có thể cần các thư viện nhánh local (ví dụ faster-whisper/sherpa-onnx) mà cấu hình Kaggle tối thiểu không cài. Chỉ cài thêm khi chạy kiểm thử/nhánh đó; dùng `requirements-local.txt` nếu cần, không bắt buộc tải model local chỉ để mở web.

Tại thời điểm viết, **137 kiểm thử code đã qua trên môi trường phát triển hiện tại**. Điều này không có nghĩa nhận diện đúng 100% hoặc Live đạt độ trễ 0,5–1 giây. Bản v2.5 có thêm 22 kiểm tra tập trung về khởi động, tái sử dụng model, hàng đợi, song song và chính sách ASR đã qua; bước nạp trước trên phiên GPU mới vẫn cần xác nhận thực tế. Bản này vẫn cần đo GPU và kiểm thử nhiều audio Việt/Anh, câu ngắn, nói chồng và tiếng cười. Các nhãn đã chỉnh tay không được tính là model tự nhận đúng.

## 13. Tài liệu tham khảo

- [Python trên Windows](https://docs.python.org/3.12/using/windows.html).
- [Tải FFmpeg](https://ffmpeg.org/download.html).
- [Kaggle Notebooks](https://www.kaggle.com/docs/notebooks).
- [Community-1: quyền truy cập và cách sử dụng](https://huggingface.co/pyannote/speaker-diarization-community-1).

Hướng dẫn này được đối chiếu với `run.ps1`, `setup.ps1`, `requirements.txt`, `setup-gpu-link.ps1`, API hiện tại và notebook worker trong mã nguồn. README cũ mô tả mốc đầu có thể còn đường dẫn ổ D và tính năng đã lỗi thời; ưu tiên tài liệu này cho bản demo hiện tại.
