"""Carregar/gravar o JSON central, deduplicação por URL e merge preservando revisão humana."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Campos geridos pelo utilizador na app de revisão — não sobrescrever na reimportação.
REVIEW_KEYS: frozenset[str] = frozenset(
    {"tags", "category", "rating", "notes", "comments", "review_status", "archived"}
)

# Raiz do repositório: .../mobihunter/scripts/importers/common.py -> parents[2]
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DEFAULT_DATA_PATH: Path = PROJECT_ROOT / "data" / "imoveis.json"


def normalize_source_url(url: str) -> str:
    """Normaliza URL para deduplicação (remove fragmento, ordena query, barra final)."""
    raw = url.strip()
    if not raw:
        raise ValueError("URL vazia")
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    scheme = (p.scheme or "https").lower()
    netloc = (p.netloc or "").lower()
    query = parse_qsl(p.query, keep_blank_values=True)
    query.sort()
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    normalized = urlunparse(
        (scheme, netloc, path, p.params, urlencode(query), "")
    )
    return normalized


def stable_id_from_url(source_url: str) -> str:
    """ID estável derivado da URL canónica (hex SHA-256 truncado)."""
    normalized = normalize_source_url(source_url)
    h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return h[:32]


def stable_id_from_agency_listing_code(agency: str, listing_code: int) -> str:
    """
    ID estável por imobiliária + código do anúncio na origem (evita duplicados
    quando a URL canónica muda, ex.: slug diferente no mesmo código Foxter).
    """
    key = f"{agency.strip().lower()}:{int(listing_code)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def load_properties(path: Path | None = None) -> list[dict[str, Any]]:
    """Lê o ficheiro JSON; se não existir ou estiver vazio, devolve []."""
    p = path or DEFAULT_DATA_PATH
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"Esperado array JSON em {p}")
    return data


def save_properties(
    records: list[dict[str, Any]], path: Path | None = None, *, indent: int = 2
) -> None:
    p = path or DEFAULT_DATA_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(records, ensure_ascii=False, indent=indent) + "\n",
        encoding="utf-8",
    )
    tmp.replace(p)


def _ensure_identity(record: dict[str, Any], agency: str) -> dict[str, Any]:
    out = deepcopy(record)
    su = out.get("source_url")
    if not su or not isinstance(su, str):
        raise ValueError("Cada registo deve ter 'source_url' (str).")
    out["source_url"] = normalize_source_url(su)
    out["id"] = stable_id_from_url(out["source_url"])
    out["agency"] = agency
    if "imported_at" not in out:
        out["imported_at"] = utc_now_iso()
    return out


def merge_import_with_existing(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """
    Sobrepõe dados do anúncio vindos do importador e mantém revisão humana já guardada.
    """
    merged = deepcopy(incoming)
    for key in REVIEW_KEYS:
        if key in existing:
            merged[key] = deepcopy(existing[key])
    return merged


def index_by_source_url(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        u = r.get("source_url")
        if isinstance(u, str) and u:
            out[normalize_source_url(u)] = r
    return out


def upsert_properties(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    agency: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Insere ou atualiza por source_url. incoming: registos já com campos de anúncio.
    Estatísticas: added, updated.
    """
    by_url = index_by_source_url(existing)
    stats = {"added": 0, "updated": 0}

    for raw in incoming:
        rec = _ensure_identity(raw, agency)
        key = rec["source_url"]
        if key in by_url:
            prev = by_url[key]
            merged = merge_import_with_existing(prev, rec)
            merged["imported_at"] = utc_now_iso()
            by_url[key] = merged
            stats["updated"] += 1
        else:
            rec["imported_at"] = utc_now_iso()
            by_url[key] = rec
            stats["added"] += 1

    new_list = list(by_url.values())
    # Ordenação estável por id para diffs previsíveis
    new_list.sort(key=lambda x: x.get("id", ""))
    return new_list, stats
