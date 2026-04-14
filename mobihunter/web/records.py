"""Helpers para registos na UI."""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup


def listing_code_from_record(rec: dict[str, Any]) -> int | None:
    if rec.get("listing_code") is not None:
        try:
            return int(rec["listing_code"])
        except (TypeError, ValueError):
            pass
    feat = rec.get("features")
    if isinstance(feat, dict) and feat.get("code") is not None:
        try:
            return int(feat["code"])
        except (TypeError, ValueError):
            pass
    return None


def thumb_url(rec: dict[str, Any]) -> str | None:
    """URL da miniatura: `thumbnail_url` ou primeira entrada de `photos`."""
    u = rec.get("thumbnail_url")
    if isinstance(u, str) and u.strip():
        return u.strip()
    photos = rec.get("photos")
    if isinstance(photos, list):
        for p in photos:
            if isinstance(p, str) and p.strip():
                return p.strip()
    return None


def fmt_money(v: Any) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    s = f"{x:,.0f}"
    return "R$ " + s.replace(",", ".")


def area_m2(rec: dict[str, Any]) -> str:
    feat = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    for k in ("area_private", "area_total", "area"):
        v = feat.get(k)
        if v is None:
            continue
        try:
            return f"{float(v):g} m²"
        except (TypeError, ValueError):
            continue
    return "—"


def row_status_label(rec: dict[str, Any]) -> str:
    parts: list[str] = []
    try:
        if int(rec.get("archived") or 0):
            parts.append("Arquivado")
    except (TypeError, ValueError):
        pass
    try:
        if int(rec.get("source_inactive") or 0):
            parts.append("Removido do site")
    except (TypeError, ValueError):
        pass
    if not parts:
        return "Ativo"
    return " · ".join(parts)


def agency_label(rec: dict[str, Any]) -> str:
    a = rec.get("agency")
    if a is None or str(a).strip() == "":
        return "—"
    return str(a).strip()


def price_previous_display(rec: dict[str, Any]) -> str:
    prev = rec.get("_price_previous")
    if prev is None:
        return "—"
    return fmt_money(prev)


def description_plain(rec: dict[str, Any]) -> str:
    """Texto da descrição sem marcação HTML (para mostrar na tabela)."""
    raw = rec.get("description")
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    text = BeautifulSoup(s, "html.parser").get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(line for line in lines if line)
