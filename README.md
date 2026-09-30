# AI Virtual Try-On & Video AI (Gemini Web) → TikTok Affiliate

Telegram Bot tự động hóa quy trình tạo nội dung thời trang bằng gói Gemini Pro (qua trình duyệt Chrome thật):

1. Nhận **ảnh người mẫu** + **ảnh trang phục** (hoặc chọn **người mẫu đã lưu**)
2. Thử đồ trên `gemini.google.com`, góp ý chỉnh sửa nhiều vòng trong cùng cuộc chat
3. Tạo video trình diễn thời trang **dọc 9:16** bằng công cụ **Tạo video (Veo)** của Gemini
4. Đóng gói video gốc + caption sao chép 1 chạm + checklist đăng bài TikTok Shop kèm sản phẩm affiliate

> Bot **không tự đăng TikTok**. Bạn nhận gói nội dung trên Telegram rồi tự đăng trên app TikTok và gắn sản phẩm.

---

## 1. Cấu trúc dự án

```
ai-tool/
├── .env.example                  # Mẫu biến môi trường (copy thành .env)
├── config.yaml                   # Prompt ảnh/video, caption, timeout, giới hạn (tự nạp lại, không cần restart)
├── selectors.yaml                # Selector giao diện Gemini (tự nạp lại, không cần restart)
├── requirements.txt
├── main.py                       # Entry point: khởi chạy bot Telegram
├── plan.md                       # Tài liệu thiết kế
│
├── core/
│   ├── config.py                 # Nạp + validate config/selectors, hot-reload
│   ├── logger.py
│   └── states.py                 # Trạng thái hội thoại, cờ job đang chạy (in-memory)
│
├── handlers/
│   ├── menu_handler.py           # /start /new /cancel /status /help
│   ├── upload_handler.py         # Nhận ảnh lẻ / album, chuẩn hóa ảnh
│   ├── model_library_handler.py  # Lưu / chọn / xóa người mẫu đã lưu
│   ├── image_flow_handler.py     # Tạo ảnh thử đồ + góp ý
│   ├── video_flow_handler.py     # Tạo video + góp ý
│   └── publish_handler.py        # Gói đăng TikTok (video gốc, caption, checklist)
│
├── services/
│   ├── browser.py                # Chrome thật qua CDP, nhận diện trạng thái trang, tải file, debug
│   ├── gemini_web_image.py       # Tự động hóa thử đồ trên Gemini
│   ├── gemini_web_video.py       # Tự động hóa công cụ "Tạo video" (9:16) + tải MP4
│   ├── model_library.py          # Thư viện người mẫu theo từng user Telegram
│   └── tiktok_package.py         # Caption + lưu thành phẩm
│
├── scripts/
│   ├── open_chrome_profile.py    # Mở Chrome profile của bot để đăng nhập Google thủ công
│   ├── spike_gemini_web.py       # Chạy thử tự động hóa Gemini không qua Telegram
│   └── inspect_debug_html.py     # Hỗ trợ đọc file HTML debug
│
├── utils/
│   ├── media_helper.py           # Chuẩn hóa ảnh, tạo ảnh preview JPEG
│   ├── telegram_io.py            # Gửi ảnh/video với timeout dài + fallback khi mạng chậm
│   └── cleanup.py                # Dọn session/debug cũ
│
└── workspace/                    # Dữ liệu runtime — được gitignore hoàn toàn
    ├── chrome-profile/           # Profile Chrome chứa phiên đăng nhập Google (TUYỆT ĐỐI không chia sẻ)
    ├── sessions/<session_id>/    # File trung gian từng phiên
    ├── output/<YYYY-MM-DD>/      # Ảnh + video đã duyệt (thành phẩm)
    ├── models/<telegram_user_id>/# Người mẫu đã lưu
    ├── debug/                    # Ảnh chụp + HTML khi lỗi
    └── logs/bot.log
```

---

## 2. Cài đặt

### 2.1 Môi trường Python (3.11+)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> Không cần `playwright install chromium`: bot điều khiển **Google Chrome thật** đã cài trên máy qua CDP.

### 2.2 File `.env`

Copy `.env.example` thành `.env` rồi điền:

```ini
TELEGRAM_BOT_TOKEN=token_từ_BotFather
ALLOWED_USER_ID=null        # null/để trống = PUBLIC; điền ID Telegram (@userinfobot) = chỉ 1 người dùng
CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
```

> `.env` chứa token bot — không commit, không gửi cho ai (đã có trong `.gitignore`).

### 2.3 Đăng nhập Google cho bot (làm 1 lần)

```powershell
.\.venv\Scripts\python.exe scripts\open_chrome_profile.py
```

- Đăng nhập **tài khoản có gói Gemini Pro** (Google AI Pro). Tài khoản thường tạo video rất hạn chế và nhanh hết lượt.
- Nếu profile có nhiều tài khoản, Gemini dùng tài khoản mặc định → nên **chỉ giữ tài khoản Pro** trong profile bot
  (kiểm tra bằng `/status` hoặc avatar góc trên Gemini: phải thấy chế độ *Pro*).
- **Đăng nhập xong phải đóng hẳn cửa sổ Chrome này** rồi mới chạy bot (bot tự mở lại Chrome kèm cổng CDP).

---

## 3. Chạy bot

```powershell
.\.venv\Scripts\python.exe main.py
```

- **Sửa code Python (`.py`) → phải khởi động lại bot** (Ctrl+C rồi chạy lại). Chỉ `config.yaml` và `selectors.yaml` được tự nạp lại trước mỗi job.
- Cửa sổ Chrome của bot: để nó nằm sau các cửa sổ khác, **không thu nhỏ quá bé**. Bot tự phóng to cửa sổ nếu bị nhỏ hơn 1200×800
  (cửa sổ quá nhỏ làm ô nhập của Gemini che mất kết quả).
- Kiểm tra nhanh không qua Telegram:
  `.\.venv\Scripts\python.exe scripts\spike_gemini_web.py --model path\model.jpg --cloth path\outfit.jpg --video`

---

## 4. Sử dụng trên Telegram

Lệnh: `/start` · `/new` · `/status` (Chrome + đăng nhập Google) · `/help` · `/cancel`

1. `/new` → gửi **ảnh người mẫu** (nên gửi dạng *File* để giữ nét), rồi gửi **ảnh trang phục** — hoặc gửi 1 album 2 ảnh (mẫu trước, đồ sau).
   - **💾 Lưu người mẫu này**: lưu vào thư viện (chú thích ảnh = tên, không có thì "Mẫu 1, 2…").
   - Lần sau bấm **📚 Chọn người mẫu đã lưu** → chọn → chỉ cần gửi ảnh trang phục. 🗑 để xóa (có xác nhận).
2. Nhận ảnh thử đồ → **✅ Duyệt ảnh → Tạo video** hoặc **✏️ Góp ý chỉnh ảnh** (Gemini sửa tiếp trên cùng cuộc chat).
3. Bot chọn công cụ **Tạo video**, chuyển tỷ lệ **Dọc (9:16)**, tải video về → **✅ Duyệt video** hoặc **✏️ Góp ý chỉnh video**.
4. Nhập tên/link sản phẩm (hoặc **Bỏ qua**) → nhận video gốc (document), caption, checklist đăng TikTok
   (nhớ bật nhãn *Nội dung do AI tạo* và gắn sản phẩm từ Showcase).

---

## 5. Cấu hình (`config.yaml`)

| Mục | Ý nghĩa |
|---|---|
| `image.default_prompt` / `image.retry_template` | Prompt thử đồ; mẫu góp ý (giữ biến `{user_feedback}`) |
| `video.default_prompt` / `video.retry_template` | Prompt video; mẫu góp ý (giữ `{base_prompt}`, `{feedback_list}`) |
| `tiktok.*` | Mẫu caption (giữ `{product_name}`, `{hashtags}`) |
| `gemini_web.*_timeout_seconds` | Thời gian chờ tạo ảnh/video |
| `app.max_saved_models` | Số người mẫu tối đa mỗi user (mặc định 10) |
| `app.keep_session_files_days` | Xóa `workspace/sessions` cũ hơn N ngày khi bot khởi động |

Lưu ý khi sửa prompt:
- Các dòng dưới `default_prompt: >` phải **thụt lề đều** (4 dấu cách), nếu không YAML lỗi và bot không khởi động được.
- Muốn dùng dấu `{` `}` trong nội dung thì viết `{{` `}}`.
- Prompt video nên **ngắn gọn, tập trung vào trang phục và chuyển động nhẹ**; prompt dài, nhấn mạnh cơ thể/danh tính người thật
  dễ bị Gemini từ chối ("I can't generate that video…").

---

## 6. Dữ liệu & dọn dẹp

| Thư mục | Tự dọn? |
|---|---|
| `workspace/sessions/` | ✅ Khi bot khởi động: xóa phiên cũ hơn `keep_session_files_days` (3 ngày) |
| `workspace/debug/` | ✅ Khi bot khởi động: xóa file cũ hơn 7 ngày |
| `workspace/output/` | ❌ Thành phẩm — tự xóa bằng tay khi cần |
| `workspace/models/` | ❌ Chỉ xóa khi bấm 🗑 trên Telegram |
| `workspace/logs/bot.log` | ❌ Không xoay vòng |
| `workspace/chrome-profile/` | ❌ **Đừng xóa** (mất đăng nhập Google) |

---

## 7. Xử lý lỗi thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| Sửa code nhưng lỗi cũ vẫn lặp lại | Bot chưa khởi động lại → Ctrl+C rồi chạy lại `main.py` |
| `🔐 Phiên Google đã hết hạn` | Chạy `scripts\open_chrome_profile.py`, đăng nhập lại, đóng Chrome |
| `Profile Chrome đang được mở bởi một cửa sổ Chrome khác` | Đóng cửa sổ Chrome đăng nhập thủ công rồi thử lại |
| `Gemini từ chối yêu cầu: "I can't generate that video…"` | Chính sách của Gemini với ảnh/prompt đó → rút gọn prompt video hoặc đổi ảnh |
| `"I'm having a hard time fulfilling your request"` | Thường do tài khoản hết lượt tạo video (hoặc profile đang dùng tài khoản không phải Pro) → kiểm tra `/status`, đổi về tài khoản Pro, thử lại sau |
| Bấm nút báo "Phiên làm việc đã kết thúc" | Phiên hết hạn (30 phút) hoặc là nút của phiên cũ → `/new` |
| Ảnh/video đến trễ kèm ghi chú "mạng gửi file chậm" | Mạng upload chậm; file gốc vẫn nằm trong `workspace/sessions` / `workspace/output` |
| Lỗi khác | Xem `workspace/logs/bot.log` và ảnh/HTML trong `workspace/debug/` |

### Khi Google đổi giao diện Gemini
1. Bot tự lưu ảnh chụp + HTML vào `workspace/debug/` khi thao tác lỗi (tên file cho biết bước lỗi: `upload_failed`, `video_tool_failed`, …).
2. Sửa selector tương ứng trong `selectors.yaml` — **không cần restart bot**.

---

## 8. Chạy 24/7 trên Windows

1. *Settings → System → Power & Sleep*: **When plugged in, sleep = Never**.
2. Task Scheduler → Basic Task:
   - Program: `D:\Link tink\ai-tool\.venv\Scripts\python.exe`, Arguments: `main.py`
   - **Start in**: `D:\Link tink\ai-tool`
3. Dùng `/status` trên Telegram để kiểm tra từ xa.

## 9. Deploy lên server Linux (không có giao diện)

Bot cần Chrome đã đăng nhập Google, nên có 3 hướng:

| Phương án | Server tối thiểu | Ghi chú |
|---|---|---|
| **Gemini API** thay cho tự động hóa web | 1 vCPU / 1 GB | Ổn định nhất, không cần trình duyệt; **trả phí theo lượt** (không dùng gói Pro). Cần thêm service API |
| Bot trên server + Chrome ở máy nhà (Tailscale/SSH tunnel tới CDP) | 1 vCPU / 512 MB | Giữ gói Pro và IP nhà; máy nhà vẫn phải bật |
| Chrome + Xvfb + noVNC ngay trên server | 2 vCPU / 2–4 GB | Phải đăng nhập lại qua noVNC (không copy profile Windows được); IP datacenter dễ bị Google bắt xác minh |

---

## 10. Bảo mật

- Không commit/chia sẻ: `.env` (token bot), `workspace/` (đặc biệt `workspace/chrome-profile/` — chứa phiên đăng nhập Google), log, ảnh người mẫu.
- Các mục trên đã có trong `.gitignore`; kiểm tra lại bằng `git status` trước mỗi lần commit.
- Bot đang ở chế độ PUBLIC nếu `ALLOWED_USER_ID=null`: ai tìm thấy bot cũng dùng được tài khoản Gemini của bạn.
