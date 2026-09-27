"""Face detection (YuNet, via OpenCV) and face embeddings (ArcFace, via onnxruntime).

An embedding is a 512-number fingerprint of a face: photos of the same person
produce embeddings that point in nearly the same direction, so comparing two
faces is just a cosine similarity.

The ArcFace model (InsightFace buffalo_l / w600k_r50) is licensed for
non-commercial use only, which is fine for a personal photo library.
"""
from __future__ import annotations

import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

from .config import DETECT_MAX_SIDE, FACE_SCORE_THRESHOLD, MIN_FACE_SIZE, MODELS_DIR

EMBEDDING_MODEL = "arcface-w600k_r50"  # stored in settings; changing it re-indexes every photo

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
DETECTOR_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
                "face_detection_yunet/face_detection_yunet_2023mar.onnx")
RECOGNIZER_FILE = "w600k_r50.onnx"
RECOGNIZER_ZIP_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"

# Where ArcFace expects the eyes, nose tip and mouth corners in its 112x112 input.
ARCFACE_TEMPLATE = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float32,
)
TEMPLATE_SPAN = 41.0  # px between the highest and lowest landmark in the template


@dataclass
class Face:
    x: float  # box as fractions of image width/height
    y: float
    w: float
    h: float
    score: float
    embedding: np.ndarray  # (512,) float32, unit length


def ensure_models() -> Tuple[Path, Path]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    detector = MODELS_DIR / DETECTOR_FILE
    if not detector.exists():
        print(f"Downloading {DETECTOR_FILE} ...")
        urllib.request.urlretrieve(DETECTOR_URL, detector.with_suffix(".part"))
        detector.with_suffix(".part").rename(detector)

    recognizer = MODELS_DIR / RECOGNIZER_FILE
    if not recognizer.exists():
        print("Downloading the ArcFace model (about 280 MB, first run only) ...")
        archive = MODELS_DIR / "buffalo_l.zip.part"
        urllib.request.urlretrieve(RECOGNIZER_ZIP_URL, archive)
        with zipfile.ZipFile(archive) as zf:
            member = next(n for n in zf.namelist() if n.endswith(RECOGNIZER_FILE))
            recognizer.with_suffix(".part").write_bytes(zf.read(member))
        recognizer.with_suffix(".part").rename(recognizer)
        archive.unlink()
    return detector, recognizer


class FaceEngine:
    def __init__(self) -> None:
        detector_path, recognizer_path = ensure_models()
        self.detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320), FACE_SCORE_THRESHOLD, 0.3, 5000)
        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(recognizer_path), options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def analyze(self, img: Image.Image) -> List[Face]:
        rgb = np.asarray(img)
        full_h, full_w = rgb.shape[:2]

        # Detect on a smaller copy (fast), but align faces from the full-resolution photo (sharp).
        scale = min(1.0, DETECT_MAX_SIDE / max(full_h, full_w))
        small = rgb if scale == 1.0 else cv2.resize(
            rgb, (round(full_w * scale), round(full_h * scale)), interpolation=cv2.INTER_AREA)
        h, w = small.shape[:2]
        self.detector.setInputSize((w, h))
        _, detections = self.detector.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
        if detections is None:
            return []

        kept, crops = [], []
        for det in detections:
            if min(det[2], det[3]) < MIN_FACE_SIZE:
                continue
            landmarks = det[4:14].reshape(5, 2) / scale
            crop = self._align(rgb, landmarks)
            if crop is None:
                continue
            kept.append(det)
            crops.append(crop)
        if not crops:
            return []

        embeddings = self._embed(crops)
        faces = []
        for det, emb in zip(kept, embeddings):
            fx, fy = max(float(det[0]), 0.0), max(float(det[1]), 0.0)
            fw, fh = min(float(det[2]), w - fx), min(float(det[3]), h - fy)
            faces.append(Face(fx / w, fy / h, fw / w, fh / h, float(det[-1]), emb))
        return faces

    @staticmethod
    def _align(rgb: np.ndarray, landmarks: np.ndarray):
        """Warp a face so its eyes, nose and mouth sit where ArcFace expects them (112x112)."""
        h, w = rgb.shape[:2]
        lo, hi = landmarks.min(axis=0), landmarks.max(axis=0)
        span = max(float((hi - lo).max()), 1.0)
        # Cut out the neighbourhood and shrink it with area averaging first; warping a
        # large face straight down to 112px would alias.
        pad = span * 1.6
        left, top = int(max(lo[0] - pad, 0)), int(max(lo[1] - pad, 0))
        right, bottom = int(min(hi[0] + pad, w)), int(min(hi[1] + pad, h))
        region = rgb[top:bottom, left:right]
        if region.size == 0:
            return None
        points = landmarks - np.array([left, top], dtype=np.float32)
        factor = min(1.0, 2 * TEMPLATE_SPAN / span)
        if factor < 1.0:
            region = cv2.resize(region, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
            points = points * factor
        matrix, _ = cv2.estimateAffinePartial2D(points.astype(np.float32), ARCFACE_TEMPLATE, method=cv2.LMEDS)
        if matrix is None:
            return None
        return cv2.warpAffine(region, matrix, (112, 112), flags=cv2.INTER_LINEAR, borderValue=0)

    def _embed(self, crops: List[np.ndarray]) -> np.ndarray:
        # Embed each face and its mirror image and add them: more robust to pose and lighting.
        batch = np.stack(crops + [c[:, ::-1] for c in crops]).astype(np.float32)
        batch = ((batch - 127.5) / 127.5).transpose(0, 3, 1, 2)
        out = self.session.run(None, {self.input_name: np.ascontiguousarray(batch)})[0]
        emb = out[: len(crops)] + out[len(crops):]
        return (emb / np.linalg.norm(emb, axis=1, keepdims=True)).astype(np.float32)
