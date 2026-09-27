"""Web interface: python app.py, then open http://127.0.0.1:5050"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
from datetime import datetime
from urllib.parse import urlencode

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

from organizer.clustering import recluster
from organizer.config import BROWSER_EXTS, FACE_DIR, GRID_DIR, GRID_SIZE, PREVIEW_DIR, PREVIEW_SIZE
from organizer.db import db, get_setting, init_db, person_label
from organizer.export import LAYOUTS, SELECTIONS, export_library
from organizer.metadata import camera_info, load_upright, save_face_thumbnail, save_thumbnail
from organizer.scanner import Scanner

app = Flask(__name__)
scanner = Scanner()
init_db()

# Library filters: ?person=<id>&flag=<key>&rating=<min stars>&sort=old
FLAG_FILTERS = {
    "pick": ("Picks", "ph.flag = 1"),
    "unflagged": ("Unflagged", "ph.flag = 0"),
    "reject": ("Rejected", "ph.flag = -1"),
    "hide_rejected": ("Hide rejected", "ph.flag != -1"),
}
DATE_SOURCES = {"exif": "camera", "filename": "file name", "file": "file modified date"}


def error(message: str, code: int = 400):
    return jsonify(error=message), code


def people_list(conn):
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.named,
               COUNT(DISTINCT f.photo_id) AS photos,
               (SELECT f2.id FROM faces f2 WHERE f2.person_id = p.id ORDER BY f2.score DESC LIMIT 1) AS cover
        FROM people p JOIN faces f ON f.person_id = p.id
        GROUP BY p.id
        ORDER BY p.named DESC, photos DESC
        """
    ).fetchall()
    samples = {}
    for f in conn.execute("SELECT person_id, id FROM faces WHERE person_id IS NOT NULL ORDER BY score DESC"):
        faces = samples.setdefault(f["person_id"], [])
        if len(faces) < 4:
            faces.append(f["id"])
    return [dict(r, label=person_label(r), samples=samples.get(r["id"], [])) for r in rows]


def current_filters() -> dict:
    args, filters = request.args, {}
    person = args.get("person", type=int)
    if person:
        filters["person"] = person
    if args.get("flag") in FLAG_FILTERS:
        filters["flag"] = args["flag"]
    rating = args.get("rating", 0, type=int)
    if 1 <= rating <= 5:
        filters["rating"] = rating
    if args.get("sort") == "old":
        filters["sort"] = "old"
    return filters


def filtered_photos(conn, filters: dict, columns: str = "ph.id"):
    where, params = [], []
    if "person" in filters:
        where.append("EXISTS (SELECT 1 FROM faces x WHERE x.photo_id = ph.id AND x.person_id = ?)")
        params.append(filters["person"])
    if "flag" in filters:
        where.append(FLAG_FILTERS[filters["flag"]][1])
    if "rating" in filters:
        where.append("ph.rating >= ?")
        params.append(filters["rating"])
    order = "ASC" if filters.get("sort") == "old" else "DESC"
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return conn.execute(
        f"SELECT {columns} FROM photos ph {clause} ORDER BY ph.taken_at {order}, ph.id {order}", params
    ).fetchall()


@app.context_processor
def template_helpers():
    def qs(filters: dict, **changes) -> str:
        """Query string for the given filters, with some changed (None removes one)."""
        merged = {k: v for k, v in {**filters, **changes}.items() if v not in (None, "", 0)}
        return ("?" + urlencode(merged)) if merged else ""

    return {"qs": qs, "flag_filters": FLAG_FILTERS}


def group_by_date(rows):
    """[{key, title, year, count, days: [{title, count, photos: [...]}]}] in the order given."""
    months = []
    for r in rows:
        dt = datetime.fromisoformat(r["taken_at"])
        month_key, day_key = dt.strftime("%Y-%m"), dt.date()
        if not months or months[-1]["key"] != month_key:
            months.append({"key": month_key, "title": dt.strftime("%B %Y"), "year": dt.year, "days": [], "count": 0})
        month = months[-1]
        if not month["days"] or month["days"][-1]["key"] != day_key:
            month["days"].append({"key": day_key, "title": f"{dt:%A}, {dt.day} {dt:%B}", "photos": []})
        month["days"][-1]["photos"].append({
            "id": r["id"],
            "time": dt.strftime("%H:%M"),
            "name": os.path.basename(r["path"]),
            "aspect": round(r["width"] / r["height"], 4) if r["width"] and r["height"] else 1.5,
            "rating": r["rating"],
            "flag": r["flag"],
        })
        month["count"] += 1
    return months


# ---------------------------------------------------------------- pages

@app.route("/")
def home():
    with db() as conn:
        has_photos = conn.execute("SELECT 1 FROM photos LIMIT 1").fetchone()
    return redirect(url_for("library" if has_photos else "import_page"))


@app.route("/timeline")
def timeline():  # old address
    return redirect(url_for("library", **request.args))


@app.route("/library")
def library():
    filters = current_filters()
    with db() as conn:
        rows = filtered_photos(conn, filters, "ph.id, ph.path, ph.taken_at, ph.width, ph.height, ph.rating, ph.flag")
        people = people_list(conn)
        totals = conn.execute(
            "SELECT COUNT(*) AS photos, COALESCE(SUM(flag = 1), 0) AS picks, COALESCE(SUM(flag = -1), 0) AS rejects,"
            " COALESCE(SUM(rating > 0), 0) AS rated FROM photos"
        ).fetchone()
    person = next((p for p in people if p["id"] == filters.get("person")), None)
    months = group_by_date(rows)
    years = []
    for m in months:
        if not years or years[-1][0] != m["year"]:
            years.append((m["year"], m["key"]))
    return render_template("library.html", months=months, years=years, people=people, person=person,
                           filters=filters, shown=len(rows), totals=totals)


@app.route("/import")
def import_page():
    with db() as conn:
        stats = conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM photos) AS photos,
                   (SELECT COUNT(*) FROM faces) AS faces,
                   (SELECT COUNT(*) FROM people WHERE named = 1) AS named,
                   (SELECT COUNT(*) FROM people WHERE named = 0) AS unnamed,
                   (SELECT MIN(taken_at) FROM photos) AS first,
                   (SELECT MAX(taken_at) FROM photos) AS last
            """
        ).fetchone()
        folder = get_setting(conn, "source_folder", "")
    return render_template("import.html", stats=stats, folder=folder)


@app.route("/export")
def export_page():
    with db() as conn:
        counts = {key: conn.execute(f"SELECT COUNT(*) FROM photos WHERE {cond}").fetchone()[0]
                  for key, (_, cond) in SELECTIONS.items()}
        named = conn.execute("SELECT name FROM people WHERE named = 1 ORDER BY name").fetchall()
        last = get_setting(conn, "export_folder", "")
    return render_template("export.html", layouts=LAYOUTS, selections=SELECTIONS, counts=counts,
                           named=[r["name"] for r in named], last_folder=last)


@app.route("/people")
def people():
    with db() as conn:
        everyone = people_list(conn)
        unknown = conn.execute("SELECT COUNT(*) FROM faces WHERE person_id IS NULL").fetchone()[0]
        unknown_samples = [r["id"] for r in conn.execute(
            "SELECT id FROM faces WHERE person_id IS NULL ORDER BY score DESC LIMIT 6")]
    named = sorted((p for p in everyone if p["named"]), key=lambda p: p["name"].lower())
    unnamed = [p for p in everyone if not p["named"]]
    return render_template("people.html", named=named, unnamed=unnamed, unknown=unknown,
                           unknown_samples=unknown_samples)


@app.route("/person/<int:pid>")
def person(pid):
    with db() as conn:
        row = conn.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()
        if not row:
            abort(404)
        faces = conn.execute(
            """SELECT f.id, f.photo_id, ph.taken_at FROM faces f JOIN photos ph ON ph.id = f.photo_id
               WHERE f.person_id = ? ORDER BY ph.taken_at DESC""",
            (pid,),
        ).fetchall()
        others = [p for p in people_list(conn) if p["id"] != pid]
    stats = {
        "photos": len({f["photo_id"] for f in faces}),
        "first": datetime.fromisoformat(faces[-1]["taken_at"]) if faces else None,
        "last": datetime.fromisoformat(faces[0]["taken_at"]) if faces else None,
    }
    return render_template("person.html", person=dict(row, label=person_label(row)), faces=faces, others=others,
                           stats=stats)


@app.route("/people/unknown")
def unknown_faces():
    with db() as conn:
        faces = conn.execute(
            """SELECT f.id, f.photo_id, ph.taken_at FROM faces f JOIN photos ph ON ph.id = f.photo_id
               WHERE f.person_id IS NULL ORDER BY ph.taken_at DESC LIMIT 1000"""
        ).fetchall()
        others = people_list(conn)
    return render_template("person.html", person=None, faces=faces, others=others, stats=None)


@app.route("/photo/<int:pid>")
def photo(pid):
    filters = current_filters()
    with db() as conn:
        ph = conn.execute("SELECT * FROM photos WHERE id = ?", (pid,)).fetchone()
        if not ph:
            abort(404)
        ids = [r["id"] for r in filtered_photos(conn, filters)]
        if pid not in ids:  # opened from outside the current filter: browse everything
            filters = {}
            ids = [r["id"] for r in filtered_photos(conn, filters)]
        faces = conn.execute(
            """SELECT f.id, f.x, f.y, f.w, f.h, f.person_id, f.locked, p.name, p.named,
                      (SELECT COUNT(*) FROM faces g WHERE g.person_id = f.person_id) AS group_size
               FROM faces f LEFT JOIN people p ON p.id = f.person_id WHERE f.photo_id = ? ORDER BY f.x""",
            (pid,),
        ).fetchall()
        people = people_list(conn)
    index = ids.index(pid)
    faces = [
        dict(f, label=(f["name"] or f"Person {f['person_id']}") if f["person_id"] else "Unknown") for f in faces
    ]
    exists = os.path.exists(ph["path"])
    return render_template(
        "photo.html",
        photo=ph,
        name=os.path.basename(ph["path"]),
        taken=datetime.fromisoformat(ph["taken_at"]),
        date_source=DATE_SOURCES.get(ph["date_source"], ph["date_source"]),
        camera=camera_info(ph["path"]) if exists else [],
        file_size=os.path.getsize(ph["path"]) if exists else None,
        faces=faces,
        names=[p["name"] for p in people if p["named"]],
        filters=filters,
        position=index + 1,
        count=len(ids),
        prev_id=ids[index - 1] if index > 0 else None,
        next_id=ids[index + 1] if index + 1 < len(ids) else None,
        strip=ids[max(0, index - 20): index + 21],
        can_reveal=sys.platform == "darwin",
    )


# ---------------------------------------------------------------- images

def _photo_path(pid):
    with db() as conn:
        row = conn.execute("SELECT path FROM photos WHERE id = ?", (pid,)).fetchone()
    if not row or not os.path.exists(row["path"]):
        abort(404)
    return row["path"]


def _cached(pid, folder, size):
    dest = folder / f"{pid}.jpg"
    if not dest.exists():
        save_thumbnail(load_upright(_photo_path(pid), size), dest, size)
    return send_file(dest, max_age=7 * 86400)


@app.route("/thumb/<int:pid>")
def thumb(pid):
    return _cached(pid, GRID_DIR, GRID_SIZE)


@app.route("/preview/<int:pid>")
def preview(pid):
    return _cached(pid, PREVIEW_DIR, PREVIEW_SIZE)


@app.route("/face/<int:fid>")
def face_thumb(fid):
    dest = FACE_DIR / f"{fid}.jpg"
    if not dest.exists():
        with db() as conn:
            f = conn.execute("SELECT photo_id, x, y, w, h FROM faces WHERE id = ?", (fid,)).fetchone()
        if not f:
            abort(404)
        save_face_thumbnail(load_upright(_photo_path(f["photo_id"])), (f["x"], f["y"], f["w"], f["h"]), dest)
    return send_file(dest, max_age=86400)


@app.route("/image/<int:pid>")
def image(pid):
    """The original, for 100% zoom."""
    path = _photo_path(pid)
    if os.path.splitext(path)[1].lower() in BROWSER_EXTS:
        return send_file(path)
    buf = io.BytesIO()  # HEIC/TIFF etc. -> JPEG for the browser
    load_upright(path).save(buf, "JPEG", quality=92)
    buf.seek(0)
    return send_file(buf, mimetype="image/jpeg")


def warm_cache():
    """Make any missing grid thumbnails and previews in the background, so browsing is instant."""
    with db() as conn:
        rows = conn.execute("SELECT id, path FROM photos ORDER BY taken_at DESC").fetchall()
    for folder, size in ((GRID_DIR, GRID_SIZE), (PREVIEW_DIR, PREVIEW_SIZE)):
        missing = [r for r in rows if not (folder / f"{r['id']}.jpg").exists() and os.path.exists(r["path"])]
        if missing:
            print(f"Preparing {len(missing)} {folder.name} images in the background…")
        for r in missing:
            try:
                save_thumbnail(load_upright(r["path"], size), folder / f"{r['id']}.jpg", size)
            except Exception as exc:
                print(f"Could not prepare {r['path']}: {exc}")


# ---------------------------------------------------------------- API

@app.post("/api/scan")
def api_scan():
    folder = (request.get_json(force=True).get("folder") or "").strip()
    if not folder:
        return error("Enter the folder to scan.")
    if not os.path.isdir(os.path.expanduser(folder)):
        return error(f"Folder not found: {folder}")
    try:
        scanner.start(folder)
    except RuntimeError as exc:
        return error(str(exc), 409)
    return jsonify(scanner.status)


@app.get("/api/status")
def api_status():
    return jsonify(scanner.status)


@app.post("/api/recluster")
def api_recluster():
    if scanner.running:
        return error("Wait for the scan to finish.", 409)
    with db() as conn:
        return jsonify(recluster(conn))


@app.post("/api/photo/<int:pid>/meta")
def api_photo_meta(pid):
    """Set the star rating (0-5) and/or flag (1 pick, 0 none, -1 reject)."""
    data = request.get_json(force=True)
    changes = {}
    if "rating" in data:
        if data["rating"] not in range(6):
            return error("Rating must be 0-5.")
        changes["rating"] = data["rating"]
    if "flag" in data:
        if data["flag"] not in (-1, 0, 1):
            return error("Flag must be -1, 0 or 1.")
        changes["flag"] = data["flag"]
    with db() as conn:
        if changes:
            conn.execute(f"UPDATE photos SET {', '.join(k + ' = ?' for k in changes)} WHERE id = ?",
                         (*changes.values(), pid))
        row = conn.execute("SELECT rating, flag FROM photos WHERE id = ?", (pid,)).fetchone()
    if not row:
        abort(404)
    return jsonify(rating=row["rating"], flag=row["flag"])


@app.post("/api/photos/meta")
def api_photos_meta():
    """Rate or flag several photos at once: {ids: [...], rating?: 0-5, flag?: -1|0|1}."""
    data = request.get_json(force=True)
    ids = [i for i in data.get("ids") or [] if isinstance(i, int)]
    changes = {}
    if "rating" in data:
        if data["rating"] not in range(6):
            return error("Rating must be 0-5.")
        changes["rating"] = data["rating"]
    if "flag" in data:
        if data["flag"] not in (-1, 0, 1):
            return error("Flag must be -1, 0 or 1.")
        changes["flag"] = data["flag"]
    if not ids or not changes:
        return error("Nothing to change.")
    with db() as conn:
        conn.executemany(f"UPDATE photos SET {', '.join(k + ' = ?' for k in changes)} WHERE id = ?",
                         [(*changes.values(), i) for i in ids])
        rows = conn.execute(f"SELECT id, rating, flag FROM photos WHERE id IN ({','.join('?' * len(ids))})", ids)
        return jsonify(photos={r["id"]: {"rating": r["rating"], "flag": r["flag"]} for r in rows})


@app.post("/api/faces/assign")
def api_faces_assign():
    """Move several faces at once: {ids: [...], person_id: id | null, new_name?: str}.

    A name that doesn't exist yet creates that person. null means "not this person"."""
    data = request.get_json(force=True)
    ids = [i for i in data.get("ids") or [] if isinstance(i, int)]
    new_name = (data.get("new_name") or "").strip()
    person_id = data.get("person_id")
    if not ids:
        return error("Select some faces first.")
    with db() as conn:
        if new_name:
            row = conn.execute("SELECT id FROM people WHERE named = 1 AND lower(name) = lower(?)", (new_name,)).fetchone()
            person_id = row["id"] if row else conn.execute(
                "INSERT INTO people (name, named) VALUES (?, 1)", (new_name,)
            ).lastrowid
        elif person_id is not None and not conn.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone():
            return error("That person no longer exists.")
        conn.executemany("UPDATE faces SET person_id = ?, locked = 1 WHERE id = ?", [(person_id, i) for i in ids])
        # Drop automatic groups that are now empty.
        conn.execute("DELETE FROM people WHERE named = 0 AND id NOT IN (SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL)")
    return jsonify(moved=len(ids), person_id=person_id)


@app.post("/api/photo/<int:pid>/reveal")
def api_reveal(pid):
    """Show the original in Finder, or open it in the default app (e.g. Photoshop / Preview)."""
    if sys.platform != "darwin":
        return error("Only available on macOS.")
    path = _photo_path(pid)
    action = request.get_json(force=True).get("action")
    subprocess.run(["open", path] if action == "open" else ["open", "-R", path], check=False)
    return jsonify(ok=True)


@app.post("/api/person/<int:pid>/rename")
def api_rename(pid):
    name = (request.get_json(force=True).get("name") or "").strip()
    if not name:
        return error("Enter a name.")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM people WHERE id = ?", (pid,)).fetchone():
            abort(404)
        same = conn.execute(
            "SELECT id FROM people WHERE named = 1 AND lower(name) = lower(?) AND id != ?", (name, pid)
        ).fetchone()
        if same:  # an existing name: this group is the same person, so merge
            _merge(conn, pid, same["id"])
            return jsonify(redirect=f"/person/{same['id']}", merged=True)
        conn.execute("UPDATE people SET name = ?, named = 1 WHERE id = ?", (name, pid))
    return jsonify(ok=True)


def _merge(conn, source_id, target_id):
    src = conn.execute("SELECT * FROM people WHERE id = ?", (source_id,)).fetchone()
    dst = conn.execute("SELECT * FROM people WHERE id = ?", (target_id,)).fetchone()
    if not src or not dst:
        abort(404)
    conn.execute("UPDATE faces SET person_id = ? WHERE person_id = ?", (target_id, source_id))
    if not dst["named"] and src["named"]:
        conn.execute("UPDATE people SET name = ?, named = 1 WHERE id = ?", (src["name"], target_id))
    conn.execute("DELETE FROM people WHERE id = ?", (source_id,))


@app.post("/api/person/<int:pid>/merge")
def api_merge(pid):
    target = request.get_json(force=True).get("into")
    if not isinstance(target, int) or target == pid:
        return error("Choose another person to merge into.")
    with db() as conn:
        _merge(conn, pid, target)
    return jsonify(redirect=f"/person/{target}")


@app.post("/api/face/<int:fid>/assign")
def api_assign(fid):
    data = request.get_json(force=True)
    new_name = (data.get("new_name") or "").strip()
    person_id = data.get("person_id")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM faces WHERE id = ?", (fid,)).fetchone():
            abort(404)
        if new_name:
            row = conn.execute("SELECT id FROM people WHERE named = 1 AND lower(name) = lower(?)", (new_name,)).fetchone()
            person_id = row["id"] if row else conn.execute(
                "INSERT INTO people (name, named) VALUES (?, 1)", (new_name,)
            ).lastrowid
        elif person_id is not None and not conn.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone():
            return error("That person no longer exists.")
        # locked = the model will not move this face again
        conn.execute("UPDATE faces SET person_id = ?, locked = 1 WHERE id = ?", (person_id, fid))
    return jsonify(ok=True, person_id=person_id)


@app.route("/tag")
def tag_group_photo():
    """Open the photo that shows the most people who don't have a name yet."""
    with db() as conn:
        row = conn.execute(
            """SELECT f.photo_id FROM faces f JOIN people p ON p.id = f.person_id
               WHERE p.named = 0 GROUP BY f.photo_id
               ORDER BY COUNT(DISTINCT f.person_id) DESC, SUM(f.w * f.h) DESC LIMIT 1"""
        ).fetchone()
    if not row:
        return redirect(url_for("people"))
    return redirect(url_for("photo", pid=row["photo_id"]))


@app.post("/api/face/<int:fid>/name")
def api_name_face(fid):
    """Name the person in one face, as typed on a photo.

    If the face is in an automatic group, the whole group gets the name (merging
    with an existing person of that name). If it already belongs to someone else,
    only this face moves. Then leftover faces are matched against everyone named.
    """
    name = (request.get_json(force=True).get("name") or "").strip()
    if not name:
        return error("Enter a name.")
    with db() as conn:
        face = conn.execute(
            """SELECT f.person_id, p.named, (SELECT COUNT(*) FROM faces g WHERE g.person_id = f.person_id) AS group_size
               FROM faces f LEFT JOIN people p ON p.id = f.person_id WHERE f.id = ?""",
            (fid,),
        ).fetchone()
        if not face:
            abort(404)
        existing = conn.execute("SELECT id FROM people WHERE named = 1 AND lower(name) = lower(?)", (name,)).fetchone()

        if face["person_id"] and not face["named"]:
            if existing:
                _merge(conn, face["person_id"], existing["id"])
            else:
                conn.execute("UPDATE people SET name = ?, named = 1 WHERE id = ?", (name, face["person_id"]))
            named_faces = face["group_size"]
        elif existing and existing["id"] == face["person_id"]:
            return jsonify(named_faces=0, matched_to_named=0)
        else:
            person_id = existing["id"] if existing else conn.execute(
                "INSERT INTO people (name, named) VALUES (?, 1)", (name,)
            ).lastrowid
            conn.execute("UPDATE faces SET person_id = ?, locked = 1 WHERE id = ?", (person_id, fid))
            named_faces = 1
        result = recluster(conn)
    return jsonify(named_faces=named_faces, matched_to_named=result["matched_to_named"])


@app.post("/api/export")
def api_export():
    data = request.get_json(force=True)
    out_dir = (data.get("out_dir") or "").strip()
    if not out_dir:
        return error("Enter an output folder.")
    try:
        with db() as conn:
            result = export_library(conn, out_dir, data.get("layout", "date_person"), data.get("mode", "copy"),
                                    data.get("selection", "not_rejected"))
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('export_folder', ?)", (out_dir,))
        return jsonify(result)
    except (ValueError, OSError) as exc:
        return error(str(exc))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    threading.Thread(target=warm_cache, daemon=True).start()
    print(f"Photo Organizer running at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True)
