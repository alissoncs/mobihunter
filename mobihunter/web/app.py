"""Listagem simples de imóveis (SQLite) com filtros de preço e código."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app_review.constants import SORT_PRICE_ASC
from app_review.data_source import (
    set_archived_for_id,
    set_review_status_for_id,
    try_load_records,
)
from app_review.filters import apply_filters, sort_records
from scripts.importers.sqlite_store import DEFAULT_DB_PATH
from mobihunter.web.records import (
    agency_label,
    area_m2,
    fmt_money,
    listing_code_from_record,
    price_previous_display,
    row_status_label,
    thumb_url,
)

_BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

app = FastAPI(title="Mobihunter", description="Listagem de imóveis")


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
    only_like: str | None = Query(None, description="Só imóveis com like"),
    listing_status: str | None = Query(
        None,
        description="all | active | archived | removed",
    ),
) -> HTMLResponse:
    pm = _parse_float(price_min)
    px = _parse_float(price_max)
    code_i = _parse_int(code)
    only_l = _parse_bool(only_like)
    ls = _norm_listing_status(listing_status)

    records, _src, load_err = try_load_records()
    if load_err is not None:
        return templates.TemplateResponse(
            request,
            "db_invalid.html",
            {
                "request": request,
                "message": load_err,
                "db_path": str(DEFAULT_DB_PATH.resolve()),
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
    )
    filtered = sort_records(filtered, SORT_PRICE_ASC)

    if code_i is not None:
        filtered = [
            r for r in filtered if listing_code_from_record(r) == code_i
        ]

    ls_form = (listing_status or "").strip() or "all"

    return templates.TemplateResponse(
        request,
        "imoveis.html",
        {
            "request": request,
            "rows": filtered,
            "count": len(filtered),
            "total_db": len(records),
            "price_min": price_min or "",
            "price_max": price_max or "",
            "code": code or "",
            "only_like": only_l,
            "listing_status": ls_form,
            "fmt_money": fmt_money,
            "listing_code_from_record": listing_code_from_record,
            "thumb_url": thumb_url,
            "area_m2": area_m2,
            "row_status_label": row_status_label,
            "agency_label": agency_label,
            "price_previous_display": price_previous_display,
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
