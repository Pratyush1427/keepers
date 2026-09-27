import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PHOTO_ORGANIZER_DATA", BASE_DIR / "data"))
MODELS_DIR = DATA_DIR / "models"
THUMBS_DIR = DATA_DIR / "thumbs"
GRID_DIR = THUMBS_DIR / "grid"          # library thumbnails
PREVIEW_DIR = THUMBS_DIR / "preview"    # full-screen view (originals are only loaded for 100% zoom)
FACE_DIR = THUMBS_DIR / "faces"
GRID_SIZE = 720
PREVIEW_SIZE = 2048
DB_PATH = DATA_DIR / "photos.db"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
# Formats browsers can display directly; others are converted to JPEG when viewed.
BROWSER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Face detection
DETECT_MAX_SIDE = 1920        # photos are downscaled to this for detection (faces are cropped from the original)
FACE_SCORE_THRESHOLD = 0.85   # YuNet confidence needed to keep a face
MIN_FACE_SIZE = 24            # px (in the downscaled image); smaller faces are ignored

# Face grouping (cosine similarity between ArcFace embeddings, 1.0 = identical)
MATCH_THRESHOLD = 0.40        # similarity needed to file a face under an already-named person
CLUSTER_SIMILARITY = 0.40     # average similarity needed for two groups of unnamed faces to merge
MIN_GROUP_SIZE = 2            # faces needed to form a group
