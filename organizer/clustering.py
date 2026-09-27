"""Turning face embeddings into people.

1. Faces the user has already filed under a *named* person act as training
   examples. Every unassigned face is compared to them (1-nearest-neighbour on
   cosine similarity) and filed under the closest person if it is similar enough.
2. The faces that are left are grouped with average-linkage agglomerative
   clustering: two groups only merge when their faces are similar *on average*,
   so different people can't get chained together through in-between faces.
   Each group becomes an unnamed "Person N" that the user can name in the web
   app, which in turn makes it a training example for step 1 on the next run.
"""
from __future__ import annotations

import sqlite3

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from .config import CLUSTER_SIMILARITY, MATCH_THRESHOLD, MIN_GROUP_SIZE


def _embeddings(rows) -> np.ndarray:
    return np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])


def recluster(conn: sqlite3.Connection) -> dict:
    # Automatic groups are rebuilt from scratch; named people and manual fixes are kept.
    conn.execute("UPDATE faces SET locked = 0 WHERE person_id IN (SELECT id FROM people WHERE named = 0)")
    conn.execute("DELETE FROM people WHERE named = 0")  # their faces fall back to person_id NULL

    known = conn.execute("SELECT id, person_id, embedding FROM faces WHERE person_id IS NOT NULL").fetchall()
    todo = conn.execute("SELECT id, embedding FROM faces WHERE person_id IS NULL AND locked = 0").fetchall()

    matched = []
    if known and todo:
        known_emb = _embeddings(known)
        known_person = np.array([r["person_id"] for r in known])
        todo_emb = _embeddings(todo)
        remaining = []
        for start in range(0, len(todo), 1024):
            sims = todo_emb[start:start + 1024] @ known_emb.T
            best = sims.argmax(axis=1)
            for offset, idx in enumerate(best):
                row = todo[start + offset]
                if sims[offset, idx] >= MATCH_THRESHOLD:
                    matched.append((int(known_person[idx]), row["id"]))
                else:
                    remaining.append(row)
        conn.executemany("UPDATE faces SET person_id = ? WHERE id = ?", matched)
        todo = remaining

    groups = []
    if len(todo) >= 2:
        labels = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average", distance_threshold=1 - CLUSTER_SIMILARITY
        ).fit_predict(_embeddings(todo))
        by_label = {}
        for row, label in zip(todo, labels):
            by_label.setdefault(label, []).append(row["id"])
        groups = sorted((g for g in by_label.values() if len(g) >= MIN_GROUP_SIZE), key=len, reverse=True)
        for face_ids in groups:
            person_id = conn.execute("INSERT INTO people (named) VALUES (0)").lastrowid
            conn.executemany("UPDATE faces SET person_id = ? WHERE id = ?", [(person_id, f) for f in face_ids])

    conn.commit()
    unknown = len(todo) - sum(len(g) for g in groups)
    return {"matched_to_named": len(matched), "new_groups": len(groups), "unknown_faces": unknown}
