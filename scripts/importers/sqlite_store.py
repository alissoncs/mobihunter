"""Persistência em SQLite: imóveis, revisão humana e rastreio de alteração de preço."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from scripts.importers.common import (
    PROJECT_ROOT,
    normalize_source_url,
    stable_id_from_agency_listing_code,
    stable_id_from_url,
    utc_now_iso,
)
from scripts.importers.db import queries_imoveis as Q

_DB_DIR = Path(__file__).resolve().parent / "db"
SCHEMA_PATH = _DB_DIR / "schema.sql"

DEFAULT_DB_PATH: Path = PROJECT_ROOT / "data" / "imoveis.db"

# Diferença mínima (R$) para considerar que o preço mudou
PRICE_EPS = 0.01


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Apenas tabelas base (não views): evita confundir com objetos homónimos."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _imoveis_has_columns(conn: sqlite3.Connection) -> bool:
    """True se `imoveis` existe e PRAGMA table_info devolve colunas (tabela utilizável)."""
    if not _table_exists(conn, "imoveis"):
        return False
    cur = conn.execute("PRAGMA table_info(imoveis)")
    return len(cur.fetchall()) > 0


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    p = path or DEFAULT_DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Só migrar se a tabela base existir e tiver colunas; senão criar schema (ficheiro vazio
    # ou BD inconsistente onde sqlite_master não bate certo com a tabela real).
    if not _imoveis_has_columns(conn):
        init_schema(conn)
    else:
        _migrate_listing_code(conn)
        _migrate_review_status(conn)
        _migrate_archived_columns(conn)
        conn.commit()
    return conn


def _migrate_listing_code(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(imoveis)")
    cols = {r[1] for r in cur.fetchall()}
    if not cols:
        return
    if "listing_code" not in cols:
        conn.execute("ALTER TABLE imoveis ADD COLUMN listing_code INTEGER")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_imoveis_agency_listing_code
        ON imoveis(agency, listing_code)
        WHERE listing_code IS NOT NULL
        """
    )


def _migrate_review_status(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(imoveis)")
    cols = {r[1] for r in cur.fetchall()}
    if not cols:
        return
    if "review_status" not in cols:
        conn.execute("ALTER TABLE imoveis ADD COLUMN review_status TEXT")


def _migrate_archived_columns(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "imoveis"):
        return
    cur = conn.execute("PRAGMA table_info(imoveis)")
    cols = {r[1] for r in cur.fetchall()}
    if not cols:
        return
    if "archived" not in cols:
        conn.execute(
            "ALTER TABLE imoveis ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
        )
    if "source_inactive" not in cols:
        conn.execute(
            "ALTER TABLE imoveis ADD COLUMN source_inactive INTEGER NOT NULL DEFAULT 0"
        )


def init_schema(conn: sqlite3.Connection) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    _migrate_listing_code(conn)
    _migrate_review_status(conn)
    _migrate_archived_columns(conn)
    conn.commit()


def _price_differs(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return abs(float(a) - float(b)) > PRICE_EPS


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _merge_features_json(
    existing_json: str | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """
    Junta `features` já guardados com os vindos da importação.
    Chaves presentes no import substituem as antigas; chaves só locais mantêm-se.
    """
    try:
        prev = json.loads(existing_json or "{}")
    except json.JSONDecodeError:
        prev = {}
    if not isinstance(prev, dict):
        prev = {}
    return {**prev, **incoming}


def _extract_listing_code(raw: dict[str, Any]) -> int | None:
    v = raw.get("listing_code")
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    feat = raw.get("features")
    if isinstance(feat, dict) and feat.get("code") is not None:
        try:
            return int(feat["code"])
        except (TypeError, ValueError):
            pass
    return None


def _resolve_existing_row(
    conn: sqlite3.Connection,
    *,
    agency: str,
    source_url: str,
    listing_code: int | None,
    pid: str,
) -> sqlite3.Row | None:
    """
    Encontra linha existente: (agency+listing_code) > id > source_url.
    Alinha `id` ao pid canónico (agency+código) quando necessário, ou remove duplicado.
    """
    ex: sqlite3.Row | None = None
    if listing_code is not None:
        ex = conn.execute(
            Q.SELECT_BY_AGENCY_AND_LISTING_CODE, (agency, listing_code)
        ).fetchone()
    if ex is None:
        ex = conn.execute(Q.SELECT_BY_ID, (pid,)).fetchone()
    if ex is None:
        ex = conn.execute(Q.SELECT_BY_SOURCE_URL, (source_url,)).fetchone()
    # Legado: id por URL antigo; slug mudou mas o código do anúncio é o mesmo
    if ex is None and listing_code is not None:
        needle = f"/imovel/{listing_code}"
        cur = conn.execute(
            "SELECT * FROM imoveis WHERE agency = ? AND instr(source_url, ?) > 0",
            (agency, needle),
        )
        matches = cur.fetchall()
        if len(matches) == 1:
            ex = matches[0]

    if ex is None:
        return None

    if listing_code is not None and ex["id"] != pid:
        other = conn.execute(Q.SELECT_BY_ID, (pid,)).fetchone()
        if other is not None and other["id"] != ex["id"]:
            conn.execute(Q.DELETE_IMOVEL_BY_ID, (ex["id"],))
            ex = other
        elif other is None:
            conn.execute(Q.UPDATE_IMOVEL_PRIMARY_KEY, (pid, ex["id"]))
            moved = conn.execute(Q.SELECT_BY_ID, (pid,)).fetchone()
            ex = moved if moved is not None else ex
    return ex


def record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    """Converte uma linha SQLite para o mesmo formato usado em `imoveis.json`."""
    photos = json.loads(row["photos_json"] or "[]")
    features = json.loads(row["features_json"] or "{}")
    tags: list[str] | None = None
    tj = row["tags_json"]
    if tj:
        parsed = json.loads(tj)
        if isinstance(parsed, list):
            tags = [str(x) for x in parsed]
        elif parsed is not None:
            tags = [str(parsed)]

    rec: dict[str, Any] = {
        "id": row["id"],
        "source_url": row["source_url"],
        "agency": row["agency"],
        "imported_at": row["imported_at"],
        "title": row["title"],
        "description": row["description"],
        "currency": row["currency"],
        "price": row["price_current"],
        "thumbnail_url": row["thumbnail_url"],
        "photos": photos if isinstance(photos, list) else [],
        "address": row["address"],
        "city": row["city"],
        "neighborhood": row["neighborhood"],
        "state": row["state"],
        "features": features if isinstance(features, dict) else {},
    }
    if "listing_code" in row.keys() and row["listing_code"] is not None:
        rec["listing_code"] = int(row["listing_code"])
    if tags is not None:
        rec["tags"] = tags
    if row["category"] is not None:
        rec["category"] = row["category"]
    if row["rating"] is not None:
        rec["rating"] = row["rating"]
    if row["notes"] is not None:
        rec["notes"] = row["notes"]
    if row["comments"] is not None:
        rec["comments"] = row["comments"]
    if "review_status" in row.keys() and row["review_status"] is not None:
        rec["review_status"] = str(row["review_status"])
    # Metadados extra da BD (úteis na UI)
    if row["price_previous"] is not None:
        rec["_price_previous"] = row["price_previous"]
    if row["price_changed_at"] is not None:
        rec["_price_changed_at"] = row["price_changed_at"]
    if row["price_change_count"] is not None:
        rec["_price_change_count"] = int(row["price_change_count"] or 0)
    if "archived" in row.keys() and row["archived"] is not None:
        rec["archived"] = int(row["archived"])
    else:
        rec["archived"] = 0
    if "source_inactive" in row.keys() and row["source_inactive"] is not None:
        rec["source_inactive"] = int(row["source_inactive"])
    else:
        rec["source_inactive"] = 0
    return rec


def fetch_all_records(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Lista todos os imóveis no formato de registo da app (ordenado por preço)."""
    cur = conn.execute(Q.SELECT_ALL_ORDERED)
    return [record_from_row(r) for r in cur.fetchall()]


def update_review_fields(
    conn: sqlite3.Connection,
    property_id: str,
    *,
    tags: list[str] | None,
    category: str | None,
    rating: float | None,
    notes: str | None,
    comments: str | None,
    review_status: str | None = None,
    archived: int | None = None,
) -> None:
    """Atualiza apenas campos de revisão humana."""
    tags_j = _json_dumps(tags) if tags is not None else None
    ar = 0 if archived is None else (1 if int(archived) else 0)
    conn.execute(
        Q.UPDATE_REVIEW_FIELDS,
        (tags_j, category, rating, notes, comments, review_status, ar, property_id),
    )


def set_review_status_only(
    conn: sqlite3.Connection,
    property_id: str,
    status: str | None,
) -> None:
    conn.execute(Q.UPDATE_REVIEW_STATUS_ONLY, (status, property_id))


def set_archived_only(
    conn: sqlite3.Connection,
    property_id: str,
    archived: int,
) -> None:
    conn.execute(Q.UPDATE_ARCHIVED_ONLY, (1 if archived else 0, property_id))


def _log_upsert_line(
    action: str,
    property_id: str,
    listing_code: int | None,
    *,
    price_changed: bool = False,
) -> None:
    extra = f" code={listing_code}" if listing_code is not None else ""
    pc = " price_changed" if price_changed else ""
    print(f"[db] {action} id={property_id}{extra}{pc}", file=sys.stderr, flush=True)


def upsert_import_records(
    conn: sqlite3.Connection,
    records: list[dict[str, Any]],
    *,
    agency: str,
    log_each_save: bool = False,
    commit_each: bool = False,
) -> dict[str, int]:
    """
    Insere ou atualiza anúncios vindos do importador.

    Em **atualização** de linha existente, mantém-se inalterados os campos de revisão
    humana já na base: ``tags_json``, ``category``, ``rating``, ``notes``, ``comments``,
    ``review_status`` (like / dislike / vazio), ``archived`` e ``source_inactive``.
    O anúncio é atualizado (preço, fotos, texto, morada, ``features_json``, etc.).

    ``features_json``: faz-se merge do JSON existente com o vindo do importador
    (chaves novas na origem substituem; chaves só locais preservam-se).

    ``log_each_save``: uma linha em stderr por INSERT/UPDATE.
    ``commit_each``: COMMIT após cada registo (recomendado no importador longo;
    evita perder tudo se o processo morrer antes do fim).
    """
    stats = {
        "inserted": 0,
        "updated": 0,
        "price_changes": 0,
    }
    now = utc_now_iso()

    try:
        for raw in records:
            su = raw.get("source_url")
            if not su or not isinstance(su, str):
                raise ValueError("Registo sem source_url.")
            source_url = normalize_source_url(su)
            listing_code = _extract_listing_code(raw)
            if listing_code is not None:
                pid = stable_id_from_agency_listing_code(agency, listing_code)
            else:
                pid = stable_id_from_url(source_url)

            photos = raw.get("photos") if isinstance(raw.get("photos"), list) else []
            features = raw.get("features") if isinstance(raw.get("features"), dict) else {}
            listing_promo = None
            if "old_price" in features:
                v = features.get("old_price")
                listing_promo = float(v) if v is not None else None

            new_price = raw.get("price")
            if new_price is not None:
                new_price = float(new_price)

            ex = _resolve_existing_row(
                conn,
                agency=agency,
                source_url=source_url,
                listing_code=listing_code,
                pid=pid,
            )

            title = raw.get("title")
            description = raw.get("description")
            currency = raw.get("currency")
            thumb = raw.get("thumbnail_url")
            address = raw.get("address")
            city = raw.get("city")
            neighborhood = raw.get("neighborhood")
            state = raw.get("state")

            if ex is None:
                pc = new_price
                pprev = None
                pchg_at = None
                pcc = 0
                conn.execute(
                    Q.INSERT_IMOVEL,
                    (
                        pid,
                        source_url,
                        agency,
                        listing_code,
                        now,
                        title,
                        description,
                        currency,
                        pc,
                        pprev,
                        listing_promo,
                        pchg_at,
                        pcc,
                        thumb,
                        _json_dumps(photos),
                        address,
                        city,
                        neighborhood,
                        state,
                        _json_dumps(features),
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        0,
                        0,
                    ),
                )
                stats["inserted"] += 1
                if log_each_save:
                    _log_upsert_line("insert", pid, listing_code)
                if commit_each:
                    conn.commit()
                continue

            row_id = ex["id"]
            tags_j = ex["tags_json"]
            category = ex["category"]
            rating = ex["rating"]
            notes = ex["notes"]
            comments = ex["comments"]
            review_rs = (
                ex["review_status"]
                if "review_status" in ex.keys()
                else None
            )
            ex_archived = (
                int(ex["archived"])
                if "archived" in ex.keys() and ex["archived"] is not None
                else 0
            )
            ex_source_inactive = (
                int(ex["source_inactive"])
                if "source_inactive" in ex.keys() and ex["source_inactive"] is not None
                else 0
            )

            old_pc = ex["price_current"]
            old_pcc = int(ex["price_change_count"] or 0)

            final_pc = old_pc
            final_pprev = ex["price_previous"]
            final_pchg_at = ex["price_changed_at"]
            final_pcc = old_pcc

            price_changed_row = False
            if new_price is not None:
                if old_pc is None:
                    final_pc = new_price
                elif old_pc is not None and _price_differs(float(old_pc), new_price):
                    final_pprev = float(old_pc)
                    final_pc = new_price
                    final_pchg_at = now
                    final_pcc = old_pcc + 1
                    stats["price_changes"] += 1
                    price_changed_row = True
                else:
                    final_pc = new_price

            merged_features = _merge_features_json(ex["features_json"], features)

            conn.execute(
                Q.UPDATE_IMOVEL_AFTER_IMPORT,
                (
                    source_url,
                    agency,
                    listing_code,
                    now,
                    title,
                    description,
                    currency,
                    final_pc,
                    final_pprev,
                    listing_promo,
                    final_pchg_at,
                    final_pcc,
                    thumb,
                    _json_dumps(photos),
                    address,
                    city,
                    neighborhood,
                    state,
                    _json_dumps(merged_features),
                    tags_j,
                    category,
                    rating,
                    notes,
                    comments,
                    review_rs,
                    ex_archived,
                    ex_source_inactive,
                    row_id,
                ),
            )
            stats["updated"] += 1
            if log_each_save:
                _log_upsert_line(
                    "update",
                    str(row_id),
                    listing_code,
                    price_changed=price_changed_row,
                )
            if commit_each:
                conn.commit()

        if not commit_each:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return stats


def row_count(conn: sqlite3.Connection) -> int:
    r = conn.execute(Q.COUNT_IMOVEIS).fetchone()
    return int(r["c"]) if r else 0
