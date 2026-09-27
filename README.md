# Photo Organizer

A local photo library for photographers. It sorts photos by the date they were taken, recognises the
people in them, and lets you cull (rate, pick, reject) and export sorted folders. Everything runs on your
own machine: no cloud, no account, and your original files are never modified.

## Features

- **Timeline library.** Photos are grouped by month and day, shown uncropped in justified rows, with an adjustable thumbnail size.
- **Face recognition.** Faces are found and grouped into people automatically. Name one face in a group photo and that person is named across the whole library; new imports are matched to people you've already named.
- **Culling.** Star ratings, pick/reject flags and Lightroom-style shortcuts (`1`–`5`, `P`, `X`, `U`). They work in the grid, including on several selected photos at once, and in the full-screen view.
- **Full-screen view.** Filmstrip, 100% zoom with drag-to-pan, RGB histogram, camera settings (lens, focal length, aperture, shutter, ISO), and *Show in Finder* / *Open in editor* on macOS.
- **People management.** Search, drag one person onto another to merge, and select several faces to move or remove them.
- **Export.** Copies or symlinks your photos into folders such as
  `2025/12 - December/06 Sat/Alice/2025-12-06_08-22-55_DSC00197.jpg`. Only people you've named get a folder,
  and you can export picks only, 3★ and up, or everything except rejects.
- **Dark and light themes.**

## Requirements

- Python 3.9 or newer
- macOS, Linux or Windows (*Show in Finder* / *Open in editor* are macOS only)
- About 300 MB of disk space for the face models, which are downloaded automatically on the first import

## Setup

```bash
git clone <this repo>
cd photo-organizer
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5050.

1. **Import**: enter a folder of photos. Importing the same folder again only processes new or edited photos.
2. **People**: name the groups the app found. *Name from a group photo* is the fastest way to do this.
3. **Library**: browse, filter and cull.
4. **Export**: write the sorted folders.

Press `?` in the app to see all keyboard shortcuts.

### Command line

```bash
python cli.py scan ~/Pictures/Trip
python cli.py export ~/Pictures/Sorted --layout date_person --select picks
```

Layouts: `date_person`, `person_date`, `date`. Selections: `not_rejected` (default), `picks`, `rated3`, `rated1`, `all`.

## Privacy

The app only listens on `127.0.0.1`, so other devices can't reach it. Everything it learns is stored in `data/`:
the database (face fingerprints, names, file paths, ratings), thumbnails and models. That folder is excluded
by `.gitignore`, so don't commit it.

To keep separate libraries, point the app at a different data folder:

```bash
PHOTO_ORGANIZER_DATA=~/PhotoLibraries/work python app.py
```

## How it works

| Step | Method | Code |
|---|---|---|
| Date taken | EXIF `DateTimeOriginal`, then a date in the file name (`IMG_20240512_…`), then the file's modified time | `organizer/metadata.py` |
| Face detection | [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) via OpenCV | `organizer/faces.py` |
| Face embedding | [ArcFace](https://github.com/deepinsight/insightface) `w600k_r50` via onnxruntime: a 512-number fingerprint per face, aligned from the full-resolution photo | `organizer/faces.py` |
| Grouping | Average-linkage agglomerative clustering (cosine similarity) | `organizer/clustering.py` |
| Recognition | Nearest neighbour against faces of people you've named | `organizer/clustering.py` |

Names and manual fixes are never overwritten; only unnamed groups are rebuilt when you import or regroup.
Thresholds live in `organizer/config.py`. Raise `CLUSTER_SIMILARITY` if different people end up in the same
group, and lower it if one person is split across several groups.

## Model licences

The face models are downloaded at runtime and aren't part of this repository.
The ArcFace model from InsightFace's `buffalo_l` pack is licensed for **non-commercial research use only**.
YuNet is MIT-licensed.
