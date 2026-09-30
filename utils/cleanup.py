"""Cleanup utilities for purging old session files and debug screenshots."""
import time
from pathlib import Path
from core.logger import logger


def cleanup_old_sessions(base_sessions_dir: Path, keep_days: int = 3) -> int:
    """Removes session directories older than `keep_days`."""
    base_dir = Path(base_sessions_dir)
    if not base_dir.exists():
        return 0

    now = time.time()
    cutoff_seconds = keep_days * 86400
    cleaned_count = 0

    for item in base_dir.iterdir():
        if item.is_dir():
            try:
                # Check modification time of session folder
                mtime = item.stat().st_mtime
                if (now - mtime) > cutoff_seconds:
                    logger.info(f"Removing expired session folder: {item}")
                    for sub in item.glob("**/*"):
                        if sub.is_file():
                            sub.unlink(missing_ok=True)
                    # Remove subdirectories bottom up
                    for sub_dir in sorted(item.glob("**/*"), reverse=True):
                        if sub_dir.is_dir():
                            sub_dir.rmdir()
                    item.rmdir()
                    cleaned_count += 1
            except Exception as e:
                logger.warning(f"Failed to clean up session {item}: {e}")

    logger.info(f"Cleanup finished. Removed {cleaned_count} expired session(s).")
    return cleaned_count


def cleanup_old_debug_files(debug_dir: Path, keep_days: int = 7) -> int:
    """Removes debug files (screenshots, HTML) older than `keep_days`."""
    d_dir = Path(debug_dir)
    if not d_dir.exists():
        return 0

    now = time.time()
    cutoff_seconds = keep_days * 86400
    cleaned_count = 0

    for item in d_dir.iterdir():
        if item.is_file():
            try:
                if (now - item.stat().st_mtime) > cutoff_seconds:
                    item.unlink(missing_ok=True)
                    cleaned_count += 1
            except Exception as e:
                logger.warning(f"Failed to delete debug file {item}: {e}")

    return cleaned_count
