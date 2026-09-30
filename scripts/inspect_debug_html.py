import re
from pathlib import Path

html_path = Path("workspace/debug/video_download_failed_20260929_170603.html")
content = html_path.read_text(encoding="utf-8", errors="ignore")

idx_btn = content.find('aria-label="Tải video xuống"')
start = max(0, idx_btn - 800)
end = min(len(content), idx_btn + 400)
print("=== HTML around download button ===")
print(content[start:end])
