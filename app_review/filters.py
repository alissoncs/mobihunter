"""Filtros e ordenação sobre registos (funções puras, sem Streamlit)."""

from __future__ import annotations

from typing import Any

from app_review.constants import (
    SORT_IMPORTED_ASC,
    SORT_IMPORTED_DESC,
    SORT_PRICE_ASC,
    SORT_PRICE_DESC,
)


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _price(rec: dict[str, Any]) -> float | None:
    p = rec.get("price")
    if p is None:
        return None
    try:
        return float(p)
    except (TypeError, ValueError):
        return None


def _imported(rec: dict[str, Any]) -> str:
    return str(rec.get("imported_at") or "")


def _text_blob(rec: dict[str, Any]) -> str:
    parts = [
        rec.get("title"),
        rec.get("address"),
        rec.get("neighborhood"),
        rec.get("city"),
        rec.get("description"),
    ]
    return _norm(" ".join(str(p) for p in parts if p))


def _tags_list(rec: dict[str, Any]) -> list[str]:
    t = rec.get("tags")
    if isinstance(t, list):
        return [str(x).strip() for x in t if str(x).strip()]
    if isinstance(t, str) and t.strip():
        return [t.strip()]
    return []


def _property_type(rec: dict[str, Any]) -> str:
    feat = rec.get("features")
    if isinstance(feat, dict):
        t = feat.get("type")
        return _norm(str(t)) if t else ""
    return ""


def _archived_int(rec: dict[str, Any]) -> int:
    v = rec.get("archived")
    if v is None:
        return 0
    try:
        return 1 if int(v) else 0
    except (TypeError, ValueError):
        return 0


def _source_inactive_int(rec: dict[str, Any]) -> int:
    v = rec.get("source_inactive")
    if v is None:
        return 0
    try:
        return 1 if int(v) else 0
    except (TypeError, ValueError):
        return 0


def apply_filters(
    records: list[dict[str, Any]],
    *,
    agency: str | None,
    price_min: float | None,
    price_max: float | None,
    text_query: str,
    tags_any: list[str] | None,
    property_type: str | None,
    only_like: bool = False,
    listing_status: str | None = None,
    neighborhood: str | None = None,
    city: str | None = None,
    show_dislikes: bool = False,
) -> list[dict[str, Any]]:
    """Filtra em memória.

    ``listing_status``: ``None`` ou ``\"all\"`` = todos; ``\"active\"`` = não arquivado e ativo na origem;
    ``\"archived\"``; ``\"removed\"`` = removido do site (source_inactive).

    Se ``show_dislikes`` for falso (padrão), exclui registos com ``review_status == \"dislike\"``.
    """
    tq = _norm(text_query)
    tag_set = {_norm(x) for x in (tags_any or []) if _norm(x)}
    pt = _norm(property_type) if property_type else ""
    ag = _norm(agency) if agency else ""
    ls = _norm(listing_status) if listing_status else ""
    nh = _norm(neighborhood) if neighborhood else ""
    ct = _norm(city) if city else ""

    out: list[dict[str, Any]] = []
    for r in records:
        if ag and _norm(str(r.get("agency") or "")) != ag:
            continue
        if nh:
            rn = _norm(str(r.get("neighborhood") or ""))
            if nh not in rn:
                continue
        if ct:
            rc = _norm(str(r.get("city") or ""))
            if ct not in rc:
                continue
        p = _price(r)
        if price_min is not None and (p is None or p < price_min):
            continue
        if price_max is not None and (p is None or p > price_max):
            continue
        if tq and tq not in _text_blob(r):
            continue
        if tag_set:
            rtags = {_norm(x) for x in _tags_list(r)}
            if not (rtags & tag_set):
                continue
        if pt and _property_type(r) != pt:
            continue
        if only_like and str(r.get("review_status") or "") != "like":
            continue
        if not show_dislikes and str(r.get("review_status") or "").strip() == "dislike":
            continue
        if ls and ls not in ("", "all"):
            a = _archived_int(r)
            si = _source_inactive_int(r)
            if ls == "active":
                if a != 0 or si != 0:
                    continue
            elif ls == "archived":
                if a == 0:
                    continue
            elif ls == "removed":
                if si == 0:
                    continue
        out.append(r)
    return out


def sort_records(records: list[dict[str, Any]], sort_key: str) -> list[dict[str, Any]]:
    """Devolve nova lista ordenada."""
    items = list(records)
    rid = lambda r: str(r.get("id", ""))

    def key_price_asc(r: dict[str, Any]) -> tuple:
        p = _price(r)
        if p is None:
            return (1, 0.0, rid(r))
        return (0, p, rid(r))

    def key_price_desc(r: dict[str, Any]) -> tuple:
        p = _price(r)
        if p is None:
            return (1, 0.0, rid(r))
        return (0, -p, rid(r))

    if sort_key == SORT_PRICE_ASC:
        items.sort(key=key_price_asc)
    elif sort_key == SORT_PRICE_DESC:
        items.sort(key=key_price_desc)
    elif sort_key == SORT_IMPORTED_ASC:
        items.sort(key=lambda r: (_imported(r), r.get("id", "")))
    elif sort_key == SORT_IMPORTED_DESC:
        items.sort(key=lambda r: (_imported(r), r.get("id", "")), reverse=True)
    else:
        items.sort(key=lambda r: (_price(r) is None, _price(r) or 0.0, r.get("id", "")))
    return items


def sort_records_active_first_price_asc(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preço crescente; imóveis não arquivados primeiro, arquivados no fim."""
    items = list(records)
    rid = lambda r: str(r.get("id", ""))

    def key_price_asc(r: dict[str, Any]) -> tuple:
        p = _price(r)
        if p is None:
            return (1, 0.0, rid(r))
        return (0, p, rid(r))

    items.sort(key=lambda r: (_archived_int(r), key_price_asc(r)))
    return items


def sort_records_active_first_price_desc(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preço decrescente; não arquivados primeiro, arquivados no fim (sem preço no fim do grupo)."""
    items = list(records)
    rid = lambda r: str(r.get("id", ""))

    def key_price_desc(r: dict[str, Any]) -> tuple:
        p = _price(r)
        if p is None:
            return (1, 0.0, rid(r))
        return (0, -float(p), rid(r))

    items.sort(key=lambda r: (_archived_int(r), key_price_desc(r)))
    return items


def sort_records_active_first_recent_desc(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mais recentes primeiro por `imported_at`; arquivados continuam no fim."""
    items = list(records)
    rid = lambda r: str(r.get("id", ""))
    # Ordenação estável: primeiro por data desc, depois força não arquivados no topo.
    items.sort(key=lambda r: (_imported(r), rid(r)), reverse=True)
    items.sort(key=lambda r: _archived_int(r))
    return items


def distinct_agencies(records: list[dict[str, Any]]) -> list[str]:
    s = {_norm(str(r.get("agency") or "")) for r in records}
    return sorted(x for x in s if x)


def distinct_property_types(records: list[dict[str, Any]]) -> list[str]:
    s: set[str] = set()
    for r in records:
        t = _property_type(r)
        if t:
            s.add(t)
    return sorted(s)


def all_tags_union(records: list[dict[str, Any]]) -> list[str]:
    s: set[str] = set()
    for r in records:
        for t in _tags_list(r):
            s.add(t)
    return sorted(s)
