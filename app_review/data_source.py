"""Carregar e gravar dados: apenas SQLite (`data/imoveis.db`)."""

from __future__ import annotations

from copy import deepcopy
import sqlite3
from pathlib import Path
from typing import Any

from scripts.importers.common import REVIEW_KEYS
from scripts.importers.sqlite_store import (
    DEFAULT_DB_PATH,
    connect_db,
    fetch_all_records,
    set_archived_only,
    set_review_status_only,
    update_review_fields,
)


def _mtime_ns(path: Path) -> int:
    if not path.exists():
        return 0
    return path.stat().st_mtime_ns


def data_snapshot_key() -> tuple[str, str, int]:
    """
    Identifica ficheiro e versão para invalidação de cache.
    (fonte, caminho_resolvido, mtime_ns)
    """
    dbp = DEFAULT_DB_PATH
    p = dbp.resolve()
    if not dbp.exists():
        return "sqlite", str(p), 0
    return "sqlite", str(p), _mtime_ns(p)


def _format_db_error(exc: BaseException) -> str:
    """Mensagem curta para utilizador (UI / logs)."""
    msg = str(exc).strip()
    low = msg.lower()
    if "malformed" in low or "corrupt" in low:
        return "O ficheiro SQLite parece corrompido (imagem inválida no disco)."
    if "not a database" in low or "file is not a database" in low:
        return "Este ficheiro não é uma base SQLite válida (ou está vazio/corrompido)."
    if "unable to open" in low or "could not open" in low:
        return "Não foi possível abrir o ficheiro (permissões ou caminho inválido)."
    if "readonly" in low or "read-only" in low:
        return "A base está só de leitura ou sem permissão de escrita."
    if "locked" in low:
        return "A base está bloqueada por outro processo; tente mais tarde."
    if "no such table" in low:
        return "A base existe mas falta a tabela de imóveis (schema incompleto)."
    if msg:
        return f"Erro ao ler a base: {msg}"
    return "Erro desconhecido ao aceder à base SQLite."


def try_load_records(
    db_path: Path | None = None,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """
    Carrega todos os imóveis.

    Devolve ``(registos, 'sqlite', erro)``.
    ``erro`` é ``None`` se correu bem ou se não há ficheiro (lista vazia).
    """
    dbp = (db_path or DEFAULT_DB_PATH).resolve()
    if not dbp.exists():
        return [], "sqlite", None

    conn: sqlite3.Connection | None = None
    try:
        conn = connect_db(dbp)
        return fetch_all_records(conn), "sqlite", None
    except (sqlite3.Error, OSError) as ex:
        return [], "sqlite", _format_db_error(ex)
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def load_records_uncached() -> tuple[list[dict[str, Any]], str]:
    """Carrega lista completa de imóveis a partir do SQLite (compatibilidade)."""
    records, src, err = try_load_records()
    if err:
        return [], src
    return records, src


def save_review_updates(
    full_list: list[dict[str, Any]],
    record_id: str,
    review: dict[str, Any],
) -> None:
    """Grava campos de revisão (tags, category, rating, notes, comments, review_status) no SQLite."""
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

    ar_raw = merged.get("archived")
    try:
        ar_i = 0 if ar_raw is None else (1 if int(ar_raw) else 0)
    except (TypeError, ValueError):
        ar_i = 0

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
            archived=ar_i,
        )
        conn.commit()
    finally:
        conn.close()


def set_review_status_for_id(record_id: str, status: str | None) -> None:
    """Atualiza só `review_status` (like / dislike / limpar)."""
    st = status.strip() if isinstance(status, str) else None
    if st == "":
        st = None
    if st is not None and st not in ("like", "dislike"):
        raise ValueError("review_status inválido")
    conn = connect_db()
    try:
        set_review_status_only(conn, record_id, st)
        conn.commit()
    finally:
        conn.close()


def set_archived_for_id(record_id: str, archived: bool) -> None:
    conn = connect_db()
    try:
        set_archived_only(conn, record_id, 1 if archived else 0)
        conn.commit()
    finally:
        conn.close()


def parse_tags_csv(text: str) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]
