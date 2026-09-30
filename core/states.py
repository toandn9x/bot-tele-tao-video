"""Conversation states and enums for the Telegram Bot."""
from enum import Enum, auto


class BotState(Enum):
    """Conversation states for ConversationHandler."""
    IDLE = auto()
    WAITING_MODEL_IMG = auto()
    WAITING_CLOTH_IMG = auto()
    PROCESSING_IMAGE = auto()
    REVIEW_IMAGE = auto()
    WAITING_IMG_FEEDBACK = auto()
    PROCESSING_VIDEO = auto()
    REVIEW_VIDEO = auto()
    WAITING_VID_FEEDBACK = auto()
    WAITING_PRODUCT = auto()
    ERROR = auto()


class JobType(Enum):
    """Type of generation job for retry handling."""
    IMAGE_INITIAL = "image_initial"
    IMAGE_REFINE = "image_refine"
    VIDEO = "video"


class DetectStatus(Enum):
    """Detection status for Gemini Web page."""
    GENERATING = "generating"
    SUCCESS = "success"
    TEXT_RESPONSE = "text_response"
    SAFETY_REFUSED = "safety_refused"
    QUOTA_EXCEEDED = "quota_exceeded"
    LOGIN_REQUIRED = "login_required"
    TIMEOUT = "timeout"
    ERROR = "error"


# In-memory transient state tracking (not persisted across restarts)
_active_user_jobs: set[int] = set()
_user_upload_locks: dict[int, "asyncio.Lock"] = {}


def is_user_job_active(user_id: int) -> bool:
    """Checks whether the user has a long-running generation task currently running."""
    return user_id in _active_user_jobs


def set_user_job_active(user_id: int, active: bool) -> None:
    """Sets or clears the running job indicator for a user."""
    if active:
        _active_user_jobs.add(user_id)
    else:
        _active_user_jobs.discard(user_id)


def get_user_upload_lock(user_id: int):
    """Returns a per-user asyncio Lock to serialize single photo uploads."""
    import asyncio
    if user_id not in _user_upload_locks:
        _user_upload_locks[user_id] = asyncio.Lock()
    return _user_upload_locks[user_id]
