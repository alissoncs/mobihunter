"""Listagem simples de imóveis (SQLite) com filtros de preço e código."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app_review.data_source import (
    set_archived_for_id,
    set_review_status_for_id,
    try_load_records,
)
from app_review.filters import (
    apply_filters,
    sort_records_active_first_price_asc,
    sort_records_active_first_price_desc,
    sort_records_active_first_recent_desc,
)
from scripts.importers.sqlite_store import DEFAULT_DB_PATH
from mobihunter.web.records import (
    agency_label,
    area_m2,
    fmt_money,
    imported_at_human,
    listing_code_from_record,
    price_previous_display,
    thumb_url,
)
from app_review.neighborhood_stats import (
    distinct_cities_sorted,
    most_common_city_label,
)
from mobihunter.web.stats_service import (
    build_chart_brl_m2,
    build_chart_price_mean,
    chart_rows_neighborhoods,
    collect_kpis,
    records_for_market_charts,
    records_in_city,
)

_BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

app = FastAPI(title="Mobihunter", description="Listagem de imóveis")

DEFAULT_PAGE_SIZE = 50
MIN_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

# Estatísticas: cidade pré-selecionada quando `?city=` não é passado (se existir na base).
DEFAULT_STATS_CITY = "Porto Alegre"


def _distinct_neighborhoods(records: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for r in records:
        n = r.get("neighborhood")
        if n is None:
            continue
        s = str(n).strip()
        if s:
            seen.add(s)
    out = sorted(seen, key=str.casefold)
    return out[:500]


def _distinct_cities(records: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for r in records:
        c = r.get("city")
        if c is None:
            continue
        s = str(c).strip()
        if s:
            seen.add(s)
    out = sorted(seen, key=str.casefold)
    return out[:500]


def _imoveis_query_dict(
    *,
    price_min: str,
    price_max: str,
    code: str,
    city: str,
    listing_status: str,
    neighborhood: str,
    only_like: bool,
    show_dislikes: bool,
    recent_first: bool,
    per_page: int,
    page: int,
    sort: str,
) -> dict[str, str]:
    d: dict[str, str] = {}
    if price_min.strip():
        d["price_min"] = price_min.strip()
    if price_max.strip():
        d["price_max"] = price_max.strip()
    if code.strip():
        d["code"] = code.strip()
    if city.strip():
        d["city"] = city.strip()
    ls = listing_status.strip() or "all"
    if ls != "all":
        d["listing_status"] = ls
    if neighborhood.strip():
        d["neighborhood"] = neighborhood.strip()
    if only_like:
        d["only_like"] = "1"
    if show_dislikes:
        d["show_dislikes"] = "1"
    if recent_first:
        d["recent_first"] = "1"
    if sort.strip() == "price_desc":
        d["sort"] = "price_desc"
    d["per_page"] = str(per_page)
    d["page"] = str(page)
    return d


def _imoveis_query_string(
    *,
    price_min: str,
    price_max: str,
    code: str,
    city: str,
    listing_status: str,
    neighborhood: str,
    only_like: bool,
    show_dislikes: bool,
    recent_first: bool,
    per_page: int,
    page: int,
    sort: str,
) -> str:
    return urlencode(
        _imoveis_query_dict(
            price_min=price_min,
            price_max=price_max,
            code=code,
            city=city,
            listing_status=listing_status,
            neighborhood=neighborhood,
            only_like=only_like,
            show_dislikes=show_dislikes,
            recent_first=recent_first,
            per_page=per_page,
            page=page,
            sort=sort,
        )
    )


def _parse_float(q: str | None) -> float | None:
    if q is None:
        return None
    s = str(q).strip()
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _parse_int(q: str | None) -> int | None:
    if q is None:
        return None
    s = str(q).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_bool(q: str | None) -> bool:
    if q is None:
        return False
    return str(q).strip().lower() in ("1", "on", "true", "yes")


def _norm_listing_status(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("", "all"):
        return None
    if s in ("active", "archived", "removed"):
        return s
    return None


class ReviewStatusBody(BaseModel):
    status: str | None = Field(default=None, description="like, dislike ou null para limpar")


class ArchivedBody(BaseModel):
    archived: bool = True


@app.get("/", response_class=HTMLResponse)
async def list_imoveis(
    request: Request,
    price_min: str | None = Query(None, description="Preço mínimo (R$)"),
    price_max: str | None = Query(None, description="Preço máximo (R$)"),
    code: str | None = Query(None, description="Código do anúncio"),
    city: str | None = Query(None, description="Filtrar por cidade (contém)"),
    only_like: str | None = Query(None, description="Só imóveis com like"),
    show_dislikes: str | None = Query(
        None,
        description="Incluir imóveis com dislike (por defeito ficam ocultos)",
    ),
    recent_first: str | None = Query(
        None,
        description="Mais recentes primeiro por data de importação",
    ),
    listing_status: str | None = Query(
        None,
        description="all | active | archived | removed",
    ),
    neighborhood: str | None = Query(None, description="Filtrar por bairro (contém)"),
    page: int = Query(1, ge=1, description="Página"),
    per_page: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=MIN_PAGE_SIZE,
        le=MAX_PAGE_SIZE,
        description="Itens por página",
    ),
    sort: str | None = Query(
        "price_asc",
        description="price_asc | price_desc (arquivados no fim em ambos)",
    ),
) -> HTMLResponse:
    pm = _parse_float(price_min)
    px = _parse_float(price_max)
    code_i = _parse_int(code)
    only_l = _parse_bool(only_like)
    show_d = _parse_bool(show_dislikes)
    recent_f = _parse_bool(recent_first)
    ls = _norm_listing_status(listing_status)
    hood_q = (neighborhood or "").strip() or None
    city_q = (city or "").strip() or None
    sort_mode = (sort or "price_asc").strip().lower()
    if sort_mode not in ("price_asc", "price_desc"):
        sort_mode = "price_asc"

    records, _src, load_err = try_load_records()
    if load_err is not None:
        return templates.TemplateResponse(
            request,
            "db_invalid.html",
            {
                "request": request,
                "message": load_err,
                "db_path": str(DEFAULT_DB_PATH.resolve()),
                "nav_active": "",
            },
            status_code=503,
        )

    filtered = apply_filters(
        records,
        agency=None,
        price_min=pm,
        price_max=px,
        text_query="",
        tags_any=None,
        property_type=None,
        only_like=only_l,
        listing_status=ls,
        neighborhood=hood_q,
        city=city_q,
        show_dislikes=show_d,
    )
    if recent_f:
        filtered = sort_records_active_first_recent_desc(filtered)
    elif sort_mode == "price_desc":
        filtered = sort_records_active_first_price_desc(filtered)
    else:
        filtered = sort_records_active_first_price_asc(filtered)

    if code_i is not None:
        filtered = [
            r for r in filtered if listing_code_from_record(r) == code_i
        ]

    total_filtered = len(filtered)
    pp = int(per_page)
    total_pages = max(1, (total_filtered + pp - 1) // pp) if total_filtered else 1
    cur_page = min(max(1, page), total_pages)
    start = (cur_page - 1) * pp
    page_rows = filtered[start : start + pp]

    ls_form = (listing_status or "").strip() or "all"
    hood_val = neighborhood or ""
    row_start = start + 1 if total_filtered else 0
    row_end = start + len(page_rows)

    q_common = dict(
        price_min=price_min or "",
        price_max=price_max or "",
        code=code or "",
        city=city or "",
        listing_status=ls_form,
        neighborhood=hood_val,
        only_like=only_l,
        show_dislikes=show_d,
        recent_first=recent_f,
        per_page=pp,
        sort=sort_mode,
    )

    return templates.TemplateResponse(
        request,
        "imoveis.html",
        {
            "request": request,
            "rows": page_rows,
            "count": total_filtered,
            "total_db": len(records),
            "price_min": price_min or "",
            "price_max": price_max or "",
            "code": code or "",
            "city": city or "",
            "only_like": only_l,
            "show_dislikes": show_d,
            "recent_first": recent_f,
            "listing_status": ls_form,
            "neighborhood": hood_val,
            "sort": sort_mode,
            "page": cur_page,
            "per_page": pp,
            "total_pages": total_pages,
            "row_start": row_start,
            "row_end": row_end,
            "qs_prev": _imoveis_query_string(**q_common, page=max(1, cur_page - 1)),
            "qs_next": _imoveis_query_string(**q_common, page=min(total_pages, cur_page + 1)),
            "qs_first": _imoveis_query_string(**q_common, page=1),
            "qs_last": _imoveis_query_string(**q_common, page=total_pages),
            "neighborhood_options": _distinct_neighborhoods(records),
            "city_options": _distinct_cities(records),
            "fmt_money": fmt_money,
            "listing_code_from_record": listing_code_from_record,
            "thumb_url": thumb_url,
            "area_m2": area_m2,
            "agency_label": agency_label,
            "price_previous_display": price_previous_display,
            "imported_at_human": imported_at_human,
            "nav_active": "imoveis",
        },
    )


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(
    request: Request,
    city: str | None = Query(
        None,
        description="Cidade: restringe KPIs e gráficos. Omitido usa Porto Alegre se existir na base.",
    ),
) -> HTMLResponse:
    records, _src, load_err = try_load_records()
    if load_err is not None:
        return templates.TemplateResponse(
            request,
            "db_invalid.html",
            {
                "request": request,
                "message": load_err,
                "db_path": str(DEFAULT_DB_PATH.resolve()),
                "nav_active": "",
            },
            status_code=503,
        )

    city_options = distinct_cities_sorted(records)
    if not city_options:
        return templates.TemplateResponse(
            request,
            "stats.html",
            {
                "request": request,
                "kpis": None,
                "chart_price": {"labels": [], "values": []},
                "chart_m2": {"labels": [], "values": []},
                "nav_active": "stats",
                "market_count": 0,
                "hood_groups_count": 0,
                "chart_price_height": 320,
                "chart_m2_height": 320,
                "city_options": [],
                "city_selected": None,
                "stats_empty": True,
            },
        )

    raw_city = (city or "").strip()
    if raw_city and raw_city in city_options:
        selected_city = raw_city
    elif DEFAULT_STATS_CITY in city_options:
        selected_city = DEFAULT_STATS_CITY
    else:
        selected_city = most_common_city_label(records) or city_options[0]

    scoped = records_in_city(records, selected_city)
    kpis = collect_kpis(scoped)
    market = records_for_market_charts(scoped)
    all_hood_rows = chart_rows_neighborhoods(market, limit=None)
    chart_price = build_chart_price_mean(all_hood_rows)
    chart_m2 = build_chart_brl_m2(all_hood_rows)
    n_price = len(chart_price["labels"])
    n_m2 = len(chart_m2["labels"])
    chart_price_height = max(320, min(12000, n_price * 22 + 80))
    chart_m2_height = max(320, min(12000, n_m2 * 22 + 80))

    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "request": request,
            "kpis": kpis,
            "chart_price": chart_price,
            "chart_m2": chart_m2,
            "nav_active": "stats",
            "market_count": len(market),
            "hood_groups_count": len(all_hood_rows),
            "chart_price_height": chart_price_height,
            "chart_m2_height": chart_m2_height,
            "city_options": city_options,
            "city_selected": selected_city,
            "stats_empty": False,
        },
    )


@app.post("/api/imovel/{record_id}/review")
async def api_set_review(record_id: str, body: ReviewStatusBody) -> JSONResponse:
    try:
        set_review_status_for_id(record_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse({"ok": True})


@app.post("/api/imovel/{record_id}/archived")
async def api_set_archived(record_id: str, body: ArchivedBody) -> JSONResponse:
    set_archived_for_id(record_id, body.archived)
    return JSONResponse({"ok": True})
