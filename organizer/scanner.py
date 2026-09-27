"""Indexing a folder of photos: dates, thumbnails and faces."""
from __future__ import annotations

import os
import threading
import traceback
from typing import Callable, List, Optional

from .clustering import recluster
from .config import GRID_DIR, GRID_SIZE, IMAGE_EXTS, PREVIEW_DIR, PREVIEW_SIZE
from .db import db, get_setting, set_setting
from .faces import EMBEDDING_MODEL, FaceEngine
from .metadata import read_photo, save_thumbnail


def find_images(folder: str) -> List[str]:
    paths = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not name.startswith(".") and os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                paths.append(os.path.join(root, name))
    return sorted(paths)


class Scanner:
    """Runs one scan at a time, in the background for the web app or inline for the CLI."""

    def __init__(self, on_progress: Optional[Callable[[dict], None]] = None) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._on_progress = on_progress
        self.status = {"state": "idle", "message": "", "total": 0, "processed": 0, "indexed": 0, "faces": 0, "errors": 0}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, folder: str) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("A scan is already running.")
            self.status.update(state="scanning", message="Starting…", processed=0, total=0)
            self._thread = threading.Thread(target=self.run, args=(folder,), daemon=True)
            self._thread.start()

    def run(self, folder: str) -> dict:
        try:
            return self._scan(folder)
        except Exception as exc:
            traceback.print_exc()
            self._set(state="error", message=str(exc))
            return self.status

    def _set(self, **changes) -> None:
        self.status.update(changes)
        if self._on_progress:
            self._on_progress(self.status)

    def _scan(self, folder: str) -> dict:
        folder = os.path.abspath(os.path.expanduser(folder))
        if not os.path.isdir(folder):
            raise ValueError(f"Folder not found: {folder}")

        self._set(state="scanning", message="Looking for photos…", total=0, processed=0, indexed=0, faces=0, errors=0)
        paths = find_images(folder)
        self._set(total=len(paths), message="Loading face models (the first run downloads about 280 MB)…")
        engine = FaceEngine()

        with db() as conn:
            set_setting(conn, "source_folder", folder)
            known = {r["path"]: r["mtime"] for r in conn.execute("SELECT path, mtime FROM photos")}
            if get_setting(conn, "embedding_model") != EMBEDDING_MODEL:
                # Fingerprints from a different model can't be compared: index everything again.
                known = {p: None for p in known}
                set_setting(conn, "embedding_model", EMBEDDING_MODEL)

            # Forget photos that were deleted from this folder since the last scan.
            prefix = folder.rstrip(os.sep) + os.sep
            present = set(paths)
            gone = [(p,) for p in known if p.startswith(prefix) and p not in present]
            conn.executemany("DELETE FROM photos WHERE path = ?", gone)

            for i, path in enumerate(paths, 1):
                mtime = os.path.getmtime(path)
                if known.get(path) != mtime:  # new or changed since the last scan
                    self._set(message=os.path.relpath(path, folder))
                    try:
                        found = self._index(conn, engine, path, mtime)
                        self._set(indexed=self.status["indexed"] + 1, faces=self.status["faces"] + found)
                    except Exception as exc:
                        print(f"Skipping {path}: {exc}")
                        self._set(errors=self.status["errors"] + 1)
                    if i % 25 == 0:
                        conn.commit()
                self._set(processed=i)
            conn.commit()

            self._set(state="clustering", message="Grouping faces into people…")
            result = recluster(conn)

        s = self.status
        self._set(
            state="done",
            message=(f"Done. {s['indexed']} new/changed photos, {s['faces']} faces found, "
                     f"{result['new_groups']} people groups, {result['matched_to_named']} faces matched to named people."
                     + (f" {s['errors']} files could not be read." if s["errors"] else "")),
        )
        return self.status

    @staticmethod
    def _index(conn, engine: FaceEngine, path: str, mtime: float) -> int:
        img, when, source = read_photo(path)
        faces = engine.analyze(img)
        # Re-indexing (e.g. after an edit) replaces the row and its faces but keeps ratings and flags.
        old = conn.execute("SELECT rating, flag FROM photos WHERE path = ?", (path,)).fetchone()
        conn.execute("DELETE FROM photos WHERE path = ?", (path,))
        photo_id = conn.execute(
            "INSERT INTO photos (path, taken_at, date_source, width, height, mtime, rating, flag)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (path, when.isoformat(sep=" "), source, img.width, img.height, mtime,
             old["rating"] if old else 0, old["flag"] if old else 0),
        ).lastrowid
        conn.executemany(
            "INSERT INTO faces (photo_id, x, y, w, h, score, embedding) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(photo_id, f.x, f.y, f.w, f.h, f.score, f.embedding.tobytes()) for f in faces],
        )
        save_thumbnail(img, GRID_DIR / f"{photo_id}.jpg", GRID_SIZE)
        save_thumbnail(img, PREVIEW_DIR / f"{photo_id}.jpg", PREVIEW_SIZE)
        return len(faces)
