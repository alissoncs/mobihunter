"""KPIs e dados agregados para a página de estatísticas."""

from __future__ import annotations

from typing import Any

from app_review.neighborhood_stats import aggregate_by_neighborhood, city_label


def _archived(r: dict[str, Any]) -> bool:
    try:
        return int(r.get("archived") or 0) != 0
    except (TypeError, ValueError):
        return False


def _source_inactive(r: dict[str, Any]) -> bool:
    try:
        return int(r.get("source_inactive") or 0) != 0
    except (TypeError, ValueError):
        return False


def _has_price(r: dict[str, Any]) -> bool:
    p = r.get("price")
    if p is None:
        return False
    try:
        return float(p) > 0
    except (TypeError, ValueError):
        return False


def records_for_market_charts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lista usada nos gráficos de bairro: não arquivados (visão de mercado atual)."""
    return [r for r in records if not _archived(r)]


def records_in_city(records: list[dict[str, Any]], city: str) -> list[dict[str, Any]]:
    """Filtra pelo rótulo de cidade (mesma regra que nas agregações)."""
    return [r for r in records if city_label(r) == city]


def collect_kpis(records: list[dict[str, Any]]) -> dict[str, int]:
    total = len(records)
    arquivados = sum(1 for r in records if _archived(r))
    ativos = total - arquivados
    removidos_site = sum(1 for r in records if _source_inactive(r))
    com_preco = sum(1 for r in records if _has_price(r))

    likes = 0
    dislikes = 0
    sem_review = 0
    for r in records:
        rs = str(r.get("review_status") or "").strip()
        if rs == "like":
            likes += 1
        elif rs == "dislike":
            dislikes += 1
        else:
            sem_review += 1

    agencias = len({str(r.get("agency") or "").strip() for r in records if str(r.get("agency") or "").strip()})

    return {
        "total": total,
        "ativos": ativos,
        "arquivados": arquivados,
        "removidos_site": removidos_site,
        "com_preco": com_preco,
        "likes": likes,
        "dislikes": dislikes,
        "sem_review": sem_review,
        "agencias_distintas": agencias,
    }


def chart_rows_neighborhoods(
    records: list[dict[str, Any]],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """
    Linhas agregadas por bairro (cidade + bairro), ordenadas por volume.
    ``limit`` None = todos os bairros.
    """
    rows = aggregate_by_neighborhood(records)
    rows.sort(key=lambda x: (-x["count"], x["city"].lower(), x["neighborhood"].lower()))
    if limit is not None:
        return rows[:limit]
    return rows


def _chart_row_label(row: dict[str, Any]) -> str:
    """Eixo Y na página de stats (já filtrada por uma cidade): só o bairro."""
    return str(row.get("neighborhood") or "(sem bairro)")


def build_chart_price_mean(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Série para gráfico: preço médio (R$) por bairro; maior valor no topo do gráfico."""
    pairs: list[tuple[str, float]] = []
    for r in rows:
        pm = r.get("price_mean")
        if pm is not None:
            pairs.append((_chart_row_label(r), round(float(pm), 2)))
    pairs.sort(key=lambda x: x[1], reverse=True)
    labels = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    return {"labels": labels, "values": values}


def build_chart_brl_m2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Série para gráfico: R$/m² por bairro; maior valor no topo do gráfico."""
    pairs: list[tuple[str, float]] = []
    for r in rows:
        v = r.get("brl_m2_median")
        if v is None:
            v = r.get("brl_m2_mean")
        if v is not None:
            pairs.append((_chart_row_label(r), round(float(v), 2)))
    pairs.sort(key=lambda x: x[1], reverse=True)
    labels = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    return {"labels": labels, "values": values}
