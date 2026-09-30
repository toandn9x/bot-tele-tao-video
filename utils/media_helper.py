"""Media helper utilities for image normalization and verification."""
from pathlib import Path
from typing import Tuple
from PIL import Image
import pillow_heif
from core.logger import logger

# Register HEIF opener with Pillow
pillow_heif.register_heif_opener()

MAX_LONG_EDGE = 2048


def normalize_image(input_path: Path, output_path: Path, max_long_edge: int = MAX_LONG_EDGE) -> Path:
    """
    Normalizes an image:
    - Converts HEIC/WEBP/other formats to standard JPEG or PNG.
    - Preserves aspect ratio.
    - Resizes only if the longest edge exceeds `max_long_edge`.
    - Preserves RGB colorspace without automated color balancing.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(input_path) as img:
        # Convert RGBA/P to RGB if target is JPEG, or preserve RGBA for PNG
        target_ext = output_path.suffix.lower()
        if target_ext in [".jpg", ".jpeg"]:
            if img.mode in ("RGBA", "LA", "P"):
                # Fill transparent areas with white background
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
        else:
            # PNG or other
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")

        # Downscale if needed
        w, h = img.size
        long_edge = max(w, h)
        if long_edge > max_long_edge:
            scale = max_long_edge / float(long_edge)
            new_w = int(w * scale)
            new_h = int(h * scale)
            logger.info(f"Downscaling image from {w}x{h} to {new_w}x{new_h}")
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Save output
        if target_ext in [".jpg", ".jpeg"]:
            img.save(output_path, "JPEG", quality=95, optimize=True)
        else:
            img.save(output_path, "PNG", optimize=True)

    logger.info(f"Image normalized and saved to {output_path}")
    return output_path


def make_preview_jpeg(image_path: Path, max_long_edge: int = 1600) -> Path:
    """
    Writes '<name>_preview.jpg' next to image_path for sending to Telegram (much smaller than the
    full-size PNG, so uploads do not time out). Falls back to the original file on any error.
    """
    image_path = Path(image_path)
    preview_path = image_path.with_name(f"{image_path.stem}_preview.jpg")
    try:
        return normalize_image(image_path, preview_path, max_long_edge=max_long_edge)
    except Exception as e:
        logger.warning(f"Could not create preview for {image_path}: {e}")
        return image_path


def get_image_dimensions(image_path: Path) -> Tuple[int, int]:
    """Returns (width, height) of an image."""
    with Image.open(image_path) as img:
        return img.size
