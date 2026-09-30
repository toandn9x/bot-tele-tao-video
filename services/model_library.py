"""Per-user library of saved model (người mẫu) photos: workspace/models/<user_id>/."""
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core.config import PROJECT_ROOT, get_config_manager
from core.logger import logger


class ModelLibrary:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or (PROJECT_ROOT / "workspace" / "models")

    def _user_dir(self, user_id: int) -> Path:
        user_dir = self.base_dir / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def _index_path(self, user_id: int) -> Path:
        return self._user_dir(user_id) / "models.json"

    def _read_index(self, user_id: int) -> List[dict]:
        path = self._index_path(user_id)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to read model library index {path}: {e}")
            return []

    def _write_index(self, user_id: int, items: List[dict]) -> None:
        path = self._index_path(user_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def list_models(self, user_id: int) -> List[dict]:
        """Saved models of a user (oldest first); entries whose image file is gone are skipped."""
        user_dir = self._user_dir(user_id)
        return [m for m in self._read_index(user_id) if (user_dir / m["file"]).exists()]

    def get_model(self, user_id: int, model_id: str) -> Optional[dict]:
        return next((m for m in self.list_models(user_id) if m["id"] == model_id), None)

    def model_path(self, user_id: int, entry: dict) -> Path:
        return self._user_dir(user_id) / entry["file"]

    def save_model(self, user_id: int, src: Path, name: Optional[str] = None) -> dict:
        """Copies src into the library. Raises ValueError when the per-user limit is reached."""
        items = self.list_models(user_id)
        max_models = get_config_manager().app_config.app.max_saved_models
        if len(items) >= max_models:
            raise ValueError(f"Đã đạt tối đa {max_models} người mẫu. Hãy xóa bớt trước khi lưu mới.")

        model_id = uuid.uuid4().hex[:8]
        file_name = f"{model_id}.jpg"
        shutil.copy2(src, self._user_dir(user_id) / file_name)

        next_no = max((m.get("no", 0) for m in items), default=0) + 1
        entry = {
            "id": model_id,
            "no": next_no,
            "name": (name or "").strip()[:40] or f"Mẫu {next_no}",
            "file": file_name,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "tg_file_id": None,
        }
        items.append(entry)
        self._write_index(user_id, items)
        logger.info(f"Saved model '{entry['name']}' ({model_id}) for user {user_id}")
        return entry

    def delete_model(self, user_id: int, model_id: str) -> bool:
        items = self._read_index(user_id)
        entry = next((m for m in items if m["id"] == model_id), None)
        if not entry:
            return False
        (self._user_dir(user_id) / entry["file"]).unlink(missing_ok=True)
        self._write_index(user_id, [m for m in items if m["id"] != model_id])
        logger.info(f"Deleted model '{entry['name']}' ({model_id}) for user {user_id}")
        return True

    def set_tg_file_id(self, user_id: int, model_id: str, file_id: Optional[str]) -> None:
        """Caches the Telegram file_id of a sent preview so later previews are not re-uploaded."""
        items = self._read_index(user_id)
        for m in items:
            if m["id"] == model_id:
                m["tg_file_id"] = file_id
        self._write_index(user_id, items)


_model_library: Optional[ModelLibrary] = None


def get_model_library() -> ModelLibrary:
    global _model_library
    if _model_library is None:
        _model_library = ModelLibrary()
    return _model_library
