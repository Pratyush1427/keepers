"""Writing the sorted library out as real folders."""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .db import get_setting

LAYOUTS = {
    "date_person": ("Date, then person", "2024/05 - May/12 Sun/Alice/2024-05-12_14-22-33_IMG_0042.jpg"),
    "person_date": ("Person, then date", "Alice/2024/05 - May/12 Sun/2024-05-12_14-22-33_IMG_0042.jpg"),
    "date": ("Date only", "2024/05 - May/12 Sun/2024-05-12_14-22-33_IMG_0042.jpg"),
}
MODES = ("copy", "link")
# Which photos to export: key -> (label, SQL condition on photos)
SELECTIONS = {
    "not_rejected": ("Everything except rejects", "flag != -1"),
    "picks": ("Picks only", "flag = 1"),
    "rated3": ("Rated 3 stars or more", "rating >= 3"),
    "rated1": ("Rated 1 star or more", "rating >= 1"),
    "all": ("Everything, including rejects", "1 = 1"),
}
# Photos without anyone you've named go here, instead of "Person 12"-style folders.
OTHERS = "Others"


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .") or "Unnamed"


def _place(src: str, dest: Path, mode: str) -> bool:
    """Copy or link src to dest. Returns False if an identical file is already there."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    candidate, n = dest, 1
    while candidate.exists() or candidate.is_symlink():
        if candidate.is_symlink() and os.path.realpath(candidate) == os.path.realpath(src):
            return False
        if not candidate.is_symlink() and candidate.stat().st_size == os.path.getsize(src):
            return False
        candidate = dest.with_name(f"{dest.stem}_{n}{dest.suffix}")
        n += 1
    if mode == "link":
        os.symlink(src, candidate)
    else:
        shutil.copy2(src, candidate)
    return True


def export_library(conn: sqlite3.Connection, out_dir: str, layout: str = "date_person", mode: str = "copy",
                   selection: str = "not_rejected") -> dict:
    if selection not in SELECTIONS:
        raise ValueError(f"Unknown selection: {selection}")
    if layout not in LAYOUTS:
        raise ValueError(f"Unknown layout: {layout}")
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}")

    out = Path(out_dir).expanduser().resolve()
    source = get_setting(conn, "source_folder")
    if source and (out == Path(source) or Path(source) in out.parents):
        raise ValueError("Choose an output folder outside the scanned folder, otherwise the next scan would pick up the copies.")

    people_in_photo = {}
    for r in conn.execute(
        "SELECT f.photo_id, p.name FROM faces f JOIN people p ON p.id = f.person_id WHERE p.named = 1"
    ):
        people_in_photo.setdefault(r["photo_id"], set()).add(r["name"])

    written = skipped = missing = 0
    condition = SELECTIONS[selection][1]
    for photo in conn.execute(f"SELECT id, path, taken_at FROM photos WHERE {condition} ORDER BY taken_at"):
        if not os.path.exists(photo["path"]):
            missing += 1
            continue
        dt = datetime.fromisoformat(photo["taken_at"])
        year, month, day = dt.strftime("%Y"), dt.strftime("%m - %B"), dt.strftime("%d %a")
        names = sorted(people_in_photo.get(photo["id"], ())) or [OTHERS]

        if layout == "date":
            folders = [out / year / month / day]
        elif layout == "date_person":
            folders = [out / year / month / day / _safe(n) for n in names]
        else:
            folders = [out / _safe(n) / year / month / day for n in names]

        # Prefix with the time taken so files sort chronologically in any file browser.
        filename = dt.strftime("%Y-%m-%d_%H-%M-%S_") + os.path.basename(photo["path"])
        for folder in folders:
            if _place(photo["path"], folder / filename, mode):
                written += 1
            else:
                skipped += 1

    return {"out_dir": str(out), "written": written, "already_there": skipped, "missing_originals": missing}
