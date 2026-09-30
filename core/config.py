"""Configuration loader and validator for AI Tool."""
import os
from pathlib import Path
from typing import List, Optional
import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from core.logger import logger


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class EnvSettings(BaseSettings):
    """Environment variables from .env."""
    TELEGRAM_BOT_TOKEN: str = Field(..., description="Telegram Bot Token")
    ALLOWED_USER_ID: Optional[int] = Field(
        default=None,
        description="Allowed Telegram User ID. If null, empty, or 0, bot is public."
    )
    CHROME_PATH: str = Field(
        default=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        description="Path to Google Chrome executable"
    )

    @field_validator("ALLOWED_USER_ID", mode="before")
    @classmethod
    def parse_allowed_user_id(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            v_clean = v.strip().lower()
            if v_clean in ("", "null", "none", "0"):
                return None
            try:
                return int(v_clean)
            except ValueError:
                return None
        if v == 0:
            return None
        return v

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


class AppConfig(BaseModel):
    debug: bool = False
    conversation_timeout_minutes: int = 30
    keep_session_files_days: int = 3
    max_saved_models: int = 10


class GeminiWebConfig(BaseModel):
    app_url: str = "https://gemini.google.com/app?hl=vi"
    video_url: str = "https://gemini.google.com/app?hl=vi"
    cdp_port: int = 9222
    profile_dir: str = "workspace/chrome-profile"
    image_timeout_seconds: int = 180
    video_timeout_seconds: int = 600
    poll_interval_seconds: int = 2

    @property
    def absolute_profile_dir(self) -> Path:
        p = Path(self.profile_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p


class ImagePromptConfig(BaseModel):
    default_prompt: str
    retry_template: str


class VideoPromptConfig(BaseModel):
    default_prompt: str
    retry_template: str


class TikTokConfig(BaseModel):
    caption_template: str
    hashtags: str
    caption_without_product: str


class YamlAppConfig(BaseModel):
    app: AppConfig
    gemini_web: GeminiWebConfig
    image: ImagePromptConfig
    video: VideoPromptConfig
    tiktok: TikTokConfig


class ChatSelectors(BaseModel):
    response_container: str = "model-response"
    new_chat_button: str = "a[href='/app'], button[aria-label*='Cuộc trò chuyện mới'], button[aria-label*='New chat']"
    upload_button: str = "button[aria-haspopup='menu'][aria-label*='tải lên' i], button[aria-haspopup='menu'][aria-label*='upload' i]"
    upload_menu_item: str = "[role='menu'] gem-button:has-text('Tệp') button, [role='menuitem']:has-text('tệp'), [role='menuitem']:has-text('Tải'), [role='menuitem']:has-text('Upload'), button:has-text('Tải tệp lên')"
    file_input: str = "input[type='file']"
    upload_thumbnail: str = "img[src^='blob:'], [class*='attachment'] img, [class*='preview'] img, button[aria-label*='Xóa' i], button[aria-label*='Remove' i]"
    prompt_box: str
    send_button: str
    response_image: str
    download_button: str


class VideoSelectors(BaseModel):
    response_container: str = "model-response"
    tool_menu_button: str = "button[aria-haspopup='menu'][aria-label*='tải lên' i], button[aria-haspopup='menu'][aria-label*='upload' i]"
    tool_item: str = "[role='menuitemcheckbox']:has-text('Tạo video'), [role='menuitemcheckbox']:has-text('Create video')"
    tool_active: str = "button[aria-label*='close Video' i], button[aria-label*='Bỏ chọn Video'], button[aria-label*='Deselect Video' i], [contenteditable='true'][data-placeholder*='Mô tả video']"
    upload_direct_button: str = "button[aria-label='Tải tệp lên'], button[aria-label='Upload file']"
    aspect_button: str = "button[aria-label*='Tỷ lệ khung hình'], button[aria-label*='Aspect ratio' i]"
    aspect_portrait_option: str = "[role='menuitemradio'][aria-label*='9:16']"
    upload_button: str = "button[aria-haspopup='menu'][aria-label*='tải lên' i], button[aria-haspopup='menu'][aria-label*='upload' i]"
    upload_menu_item: str = "[role='menu'] gem-button:has-text('Tệp') button, [role='menuitem']:has-text('tệp'), [role='menuitem']:has-text('Tải'), [role='menuitem']:has-text('Upload'), button:has-text('Tải tệp lên')"
    file_input: str = "input[type='file']"
    upload_thumbnail: str = "img[src^='blob:'], [class*='attachment'] img, [class*='preview'] img, button[aria-label*='Xóa' i], button[aria-label*='Remove' i]"
    prompt_box: str
    generate_button: str
    result_video: str
    download_button: str


class DetectSelectors(BaseModel):
    generating: str
    generating_texts: List[str] = []
    media_placeholder: str = "generated-image, single-image, generated-video"
    response_complete: str = "message-actions, thumb-up-button"
    refused_texts: List[str]
    quota_texts: List[str]
    login_url_prefix: str = "https://accounts.google.com"


class YamlSelectorsConfig(BaseModel):
    chat: ChatSelectors
    video: VideoSelectors
    detect: DetectSelectors


class ConfigManager:
    """Manages loading, validation and hot-reloading of config and selectors."""

    def __init__(
        self,
        config_path: Optional[Path | str] = None,
        selectors_path: Optional[Path | str] = None
    ):
        self.config_path = Path(config_path) if config_path else PROJECT_ROOT / "config.yaml"
        self.selectors_path = Path(selectors_path) if selectors_path else PROJECT_ROOT / "selectors.yaml"

        self._config_mtime: Optional[float] = None
        self._selectors_mtime: Optional[float] = None

        self._env: Optional[EnvSettings] = None
        self._app_config: Optional[YamlAppConfig] = None
        self._selectors: Optional[YamlSelectorsConfig] = None

        # Load initially
        self.load_all(strict=True)

    def load_env(self, strict: bool = True) -> EnvSettings:
        try:
            self._env = EnvSettings()
            return self._env
        except Exception as e:
            logger.error(f"Failed to load .env: {e}")
            if strict:
                raise
            return self._env

    def load_app_config(self, strict: bool = True) -> YamlAppConfig:
        if not self.config_path.exists():
            msg = f"Config file not found: {self.config_path}"
            logger.error(msg)
            if strict:
                raise FileNotFoundError(msg)
            return self._app_config

        try:
            mtime = self.config_path.stat().st_mtime
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            validated = YamlAppConfig.model_validate(data)
            self._app_config = validated
            self._config_mtime = mtime
            logger.info("Loaded config.yaml successfully.")
            return self._app_config
        except Exception as e:
            logger.error(f"Failed to load or validate {self.config_path}: {e}")
            if strict or self._app_config is None:
                raise
            return self._app_config

    def load_selectors(self, strict: bool = True) -> YamlSelectorsConfig:
        if not self.selectors_path.exists():
            msg = f"Selectors file not found: {self.selectors_path}"
            logger.error(msg)
            if strict:
                raise FileNotFoundError(msg)
            return self._selectors

        try:
            mtime = self.selectors_path.stat().st_mtime
            with open(self.selectors_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            validated = YamlSelectorsConfig.model_validate(data)
            self._selectors = validated
            self._selectors_mtime = mtime
            logger.info("Loaded selectors.yaml successfully.")
            return self._selectors
        except Exception as e:
            logger.error(f"Failed to load or validate {self.selectors_path}: {e}")
            if strict or self._selectors is None:
                raise
            return self._selectors

    def load_all(self, strict: bool = True):
        self.load_env(strict=strict)
        self.load_app_config(strict=strict)
        self.load_selectors(strict=strict)

    def reload_if_changed(self) -> bool:
        """Checks file modified times and reloads if files have changed on disk."""
        changed = False
        try:
            if self.config_path.exists():
                mtime = self.config_path.stat().st_mtime
                if self._config_mtime is None or mtime > self._config_mtime:
                    logger.info("config.yaml modified, reloading...")
                    self.load_app_config(strict=False)
                    changed = True

            if self.selectors_path.exists():
                mtime = self.selectors_path.stat().st_mtime
                if self._selectors_mtime is None or mtime > self._selectors_mtime:
                    logger.info("selectors.yaml modified, reloading...")
                    self.load_selectors(strict=False)
                    changed = True
        except Exception as e:
            logger.warning(f"Error checking config changes: {e}")
        return changed

    @property
    def env(self) -> EnvSettings:
        if self._env is None:
            self.load_env(strict=True)
        return self._env

    @property
    def app_config(self) -> YamlAppConfig:
        if self._app_config is None:
            self.load_app_config(strict=True)
        return self._app_config

    @property
    def selectors(self) -> YamlSelectorsConfig:
        if self._selectors is None:
            self.load_selectors(strict=True)
        return self._selectors


# Global config manager instance (will be initialized in main or loaded lazily)
config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    global config_manager
    if config_manager is None:
        config_manager = ConfigManager()
    return config_manager
