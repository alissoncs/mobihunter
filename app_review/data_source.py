"""Carregar e gravar dados: SQLite (preferido se tiver registos) ou JSON."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.importers.common import (
    DEFAULT_DATA_PATH,
    REVIEW_KEYS,
    load_properties,
    save_properties,
)
from scripts.importers.sqlite_store import (
    DEFAULT_DB_PATH,
    connect_db,
    fetch_all_records,
    row_count,
    update_review_fields,
)


def _mtime_ns(path: Path) -> int:
    if not path.exists():
        return 0
    return path.stat().st_mtime_ns


def data_snapshot_key() -> tuple[str, str, int]:
    """
    Identifica ficheiro e versão para @st.cache_data.
    (fonte, caminho_resolvido, mtime_ns)
    """
    dbp = DEFAULT_DB_PATH
    jp = DEFAULT_DATA_PATH
    if dbp.exists():
        conn = connect_db(dbp)
        try:
            if row_count(conn) > 0:
                p = dbp.resolve()
                return "sqlite", str(p), _mtime_ns(p)
        finally:
            conn.close()
    if jp.exists():
        p = jp.resolve()
        return "json", str(p), _mtime_ns(p)
    p = jp.resolve()
    return "empty", str(p), 0


def load_records_uncached() -> tuple[list[dict[str, Any]], str]:
    """Carrega lista completa de imóveis."""
    dbp = DEFAULT_DB_PATH
    if dbp.exists():
        conn = connect_db(dbp)
        try:
            if row_count(conn) > 0:
                return fetch_all_records(conn), "sqlite"
        finally:
            conn.close()
    return load_properties(), "json"


def save_review_updates(
    source: str,
    full_list: list[dict[str, Any]],
    record_id: str,
    review: dict[str, Any],
) -> None:
    """Grava campos de revisão (tags, category, rating, notes, comments, review_status)."""
    rec = next((r for r in full_list if r.get("id") == record_id), None)
    if rec is None:
        raise ValueError(f"Registo não encontrado: {record_id}")

    merged: dict[str, Any] = {}
    for k in REVIEW_KEYS:
        if k in review:
            merged[k] = review[k]
        else:
            merged[k] = deepcopy(rec.get(k))

    tags = merged.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if tags is not None and not isinstance(tags, list):
        tags = None

    rs_raw = merged.get("review_status")
    if isinstance(rs_raw, str):
        rs_raw = rs_raw.strip() or None
    rs: str | None = rs_raw if rs_raw in (None, "like", "dislike") else None

    if source == "sqlite":
        conn = connect_db()
        try:
            update_review_fields(
                conn,
                record_id,
                tags=tags,
                category=merged.get("category"),
                rating=float(merged["rating"]) if merged.get("rating") is not None else None,
                notes=merged.get("notes"),
                comments=merged.get("comments"),
                review_status=rs,
            )
            conn.commit()
        finally:
            conn.close()
        return

    for i, r in enumerate(full_list):
        if r.get("id") != record_id:
            continue
        out = deepcopy(r)
        for k in REVIEW_KEYS:
            out[k] = deepcopy(merged[k])
        full_list[i] = out
        break
    save_properties(full_list)


def parse_tags_csv(text: str) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]
