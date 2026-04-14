"""Agregações por cidade/bairro para relatórios (funções puras)."""

from __future__ import annotations

from collections import Counter
from typing import Any


def _price(rec: dict[str, Any]) -> float | None:
    p = rec.get("price")
    if p is None:
        return None
    try:
        x = float(p)
        if x <= 0:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _area_m2(rec: dict[str, Any]) -> float | None:
    feat = rec.get("features")
    if not isinstance(feat, dict):
        return None
    for key in ("area_private", "area_total", "area"):
        v = feat.get(key)
        if v is None:
            continue
        try:
            a = float(v)
            if a > 0:
                return a
        except (TypeError, ValueError):
            continue
    return None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    m = n // 2
    if n % 2:
        return s[m]
    return (s[m - 1] + s[m]) / 2.0


def _city_label(rec: dict[str, Any]) -> str:
    c = rec.get("city")
    t = (str(c).strip() if c is not None else "") or "(sem cidade)"
    return t


def _hood_label(rec: dict[str, Any]) -> str:
    n = rec.get("neighborhood")
    t = (str(n).strip() if n is not None else "") or "(sem bairro)"
    return t


def city_label(rec: dict[str, Any]) -> str:
    """Rótulo de cidade normalizado (igual ao usado nas agregações)."""
    return _city_label(rec)


def distinct_cities_sorted(records: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for r in records:
        seen.add(_city_label(r))
    return sorted(seen, key=str.lower)


def most_common_city_label(records: list[dict[str, Any]]) -> str | None:
    """Cidade com mais registos; desempate por ordem lexicográfica (case-insensitive)."""
    if not records:
        return None
    cnt = Counter(_city_label(r) for r in records)
    best = max(cnt.values())
    candidates = [c for c, n in cnt.items() if n == best]
    return min(candidates, key=str.casefold)


def aggregate_by_neighborhood(
    records: list[dict[str, Any]],
    *,
    city: str | None = None,
) -> list[dict[str, Any]]:
    """
    Agrega por (cidade, bairro). Se `city` for dado, filtra essa cidade
    (comparação exacta ao rótulo normalizado usado em `_city_label`).
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in records:
        cl = _city_label(r)
        if city is not None and cl != city:
            continue
        hl = _hood_label(r)
        key = (cl, hl)
        groups.setdefault(key, []).append(r)

    rows: list[dict[str, Any]] = []
    for (cl, hl), items in groups.items():
        prices = [p for r in items if (p := _price(r)) is not None]
        areas = [a for r in items if (a := _area_m2(r)) is not None]
        ppm = []
        for r in items:
            pr = _price(r)
            ar = _area_m2(r)
            if pr is not None and ar is not None and ar > 0:
                ppm.append(pr / ar)

        price_mean = sum(prices) / len(prices) if prices else None
        brl_m2_mean = sum(ppm) / len(ppm) if ppm else None

        rows.append(
            {
                "city": cl,
                "neighborhood": hl,
                "count": len(items),
                "with_price": len(prices),
                "price_min": min(prices) if prices else None,
                "price_mean": price_mean,
                "price_median": _median(prices),
                "price_max": max(prices) if prices else None,
                "m2_median": _median(areas),
                "brl_m2_mean": brl_m2_mean,
                "brl_m2_median": _median(ppm),
            }
        )

    rows.sort(key=lambda x: (-x["count"], x["city"].lower(), x["neighborhood"].lower()))
    return rows
