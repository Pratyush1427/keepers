"""Loading photos and working out when they were taken."""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageOps

try:  # iPhone HEIC support
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

EXIF_IFD_POINTER = 0x8769
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_DATETIME = 0x0132

# e.g. IMG_20240512_142233.jpg, PXL_20240512_142233123.jpg, IMG-20240512-WA0001.jpg, 2024-05-12 14.22.33.png
FILENAME_DATE_RE = re.compile(
    r"(19\d{2}|20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[-_ T.]?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d))?"
)


def _parse_exif_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    value = str(value).strip().strip("\x00")
    for fmt, length in (("%Y:%m:%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M:%S", 19), ("%Y:%m:%d %H:%M", 16)):
        try:
            dt = datetime.strptime(value[:length], fmt)
        except ValueError:
            continue
        if dt.year >= 1900:  # some cameras write 0000:00:00
            return dt
    return None


def _date_from_filename(name: str) -> Optional[datetime]:
    match = FILENAME_DATE_RE.search(name)
    if not match:
        return None
    try:
        return datetime(*(int(part) if part else 0 for part in match.groups()))
    except ValueError:
        return None


def taken_at(img: Image.Image, path: str) -> Tuple[datetime, str]:
    """Return (datetime, source) where source is 'exif', 'filename' or 'file'."""
    try:
        exif = img.getexif()
        sub = exif.get_ifd(EXIF_IFD_POINTER)
        for value in (sub.get(TAG_DATETIME_ORIGINAL), sub.get(TAG_DATETIME_DIGITIZED), exif.get(TAG_DATETIME)):
            dt = _parse_exif_datetime(value)
            if dt:
                return dt, "exif"
    except Exception:
        pass
    dt = _date_from_filename(os.path.basename(path))
    if dt:
        return dt, "filename"
    return datetime.fromtimestamp(os.path.getmtime(path)).replace(microsecond=0), "file"


def read_photo(path: str) -> Tuple[Image.Image, datetime, str]:
    """Load a photo upright (EXIF rotation applied) as RGB, plus when it was taken."""
    with Image.open(path) as raw:
        when, source = taken_at(raw, path)
        img = ImageOps.exif_transpose(raw).convert("RGB")
    return img, when, source


def load_upright(path: str, max_side: Optional[int] = None) -> Image.Image:
    """Load a photo upright. With max_side, JPEGs are decoded at reduced size (much faster)."""
    with Image.open(path) as raw:
        if max_side and raw.format == "JPEG":
            raw.draft("RGB", (max_side, max_side))
        return ImageOps.exif_transpose(raw).convert("RGB")


def save_thumbnail(img: Image.Image, dest: Path, max_side: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    thumb = img.copy()
    thumb.thumbnail((max_side, max_side), Image.LANCZOS)
    # Write to a temp file first: the web app may be generating the same file in another thread.
    tmp = dest.with_name(f".{dest.name}.{threading.get_ident()}.tmp")
    thumb.save(tmp, "JPEG", quality=88)
    os.replace(tmp, dest)


def _ratio(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def camera_info(path: str) -> List[Tuple[str, str]]:
    """Camera settings from EXIF, as (label, value) rows for display."""
    try:
        with Image.open(path) as raw:
            exif = raw.getexif()
            sub = exif.get_ifd(EXIF_IFD_POINTER)
    except Exception:
        return []
    rows = []
    make, model = (str(exif.get(t) or "").strip().strip("\x00") for t in (0x010F, 0x0110))
    camera = model if make and model.lower().startswith(make.split()[0].lower()) else f"{make} {model}".strip()
    if camera:
        rows.append(("Camera", camera))
    lens = str(sub.get(0xA434) or "").strip().strip("\x00")
    if lens:
        rows.append(("Lens", lens))
    focal, focal35 = _ratio(sub.get(0x920A)), sub.get(0xA405)
    if focal:
        rows.append(("Focal length", f"{focal:g} mm" + (f" ({focal35} mm eq.)" if focal35 and focal35 != round(focal) else "")))
    aperture = _ratio(sub.get(0x829D))
    if aperture:
        rows.append(("Aperture", f"f/{aperture:g}"))
    exposure = _ratio(sub.get(0x829A))
    if exposure:
        rows.append(("Shutter", f"1/{round(1 / exposure)} s" if exposure < 1 else f"{exposure:g} s"))
    iso = sub.get(0x8827)
    if iso:
        rows.append(("ISO", str(iso[0] if isinstance(iso, tuple) else iso)))
    bias = _ratio(sub.get(0x9204))
    if bias:
        rows.append(("Exposure comp.", f"{bias:+.1f} EV"))
    flash = sub.get(0x9209)
    if flash is not None:
        rows.append(("Flash", "Fired" if int(flash) & 1 else "Off"))
    return rows


def save_face_thumbnail(img: Image.Image, box: Tuple[float, float, float, float], dest: Path, size: int = 200) -> None:
    """Crop a square around a face box given as fractions of the image size."""
    x, y, w, h = box
    cx, cy = (x + w / 2) * img.width, (y + h / 2) * img.height
    half = max(w * img.width, h * img.height) * 0.8
    crop = img.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{threading.get_ident()}.tmp")
    crop.resize((size, size), Image.LANCZOS).save(tmp, "JPEG", quality=88)
    os.replace(tmp, dest)
