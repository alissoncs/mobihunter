"""Mobihunter — UI NiceGUI. Na raiz: python -m nicegui_app.main"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from nicegui import app, ui

import nicegui_app.reports_page  # noqa: F401  — regista /relatorios

from app_review.constants import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PAGE_SIZE_OPTIONS,
    SORT_IMPORTED_ASC,
    SORT_IMPORTED_DESC,
    SORT_PRICE_ASC,
    SORT_PRICE_DESC,
)
from app_review.data_source import save_review_updates
from app_review.filters import (
    all_tags_union,
    apply_filters,
    distinct_agencies,
    distinct_property_types,
    sort_records,
)
from app_review.pagination import paginate
from nicegui_app.cache import get_bundle, invalidate
from nicegui_app.layout import mobihunter_header

# Porta HTTP por defeito; sobrescreva com MOBIHUNTER_PORT.
DEFAULT_NICEGUI_PORT = 9090


def _fmt_money(v: Any) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    s = f"{x:,.0f}"
    return "R$ " + s.replace(",", ".")


def _user_state() -> dict[str, Any]:
    if "mh" not in app.storage.user:
        app.storage.user["mh"] = {
            "agency": "",
            "use_pmin": False,
            "price_min": 0.0,
            "use_pmax": False,
            "price_max": 0.0,
            "text_q": "",
            "tags": [],
        }
    s = app.storage.user["mh"]
    if "tags" not in s:
        s["tags"] = []
    if "sort" not in s:
        s["sort"] = SORT_PRICE_ASC
    if "page" not in s:
        s["page"] = 1
    if "page_size" not in s:
        s["page_size"] = DEFAULT_PAGE_SIZE
    try:
        ps = int(s.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        ps = DEFAULT_PAGE_SIZE
    s["page_size"] = max(1, min(ps, MAX_PAGE_SIZE))
    if "property_type" not in s:
        s["property_type"] = ""
    return s


def _photo_store() -> dict[str, int]:
    return app.storage.user.setdefault("mh_photo_idx", {})


def _find_record(rid: str) -> dict[str, Any] | None:
    records, _ = get_bundle()
    for r in records:
        if r.get("id") == rid:
            return r
    return None


def _build_grid_rows(page_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in page_items:
        feat = r.get("features") if isinstance(r.get("features"), dict) else {}
        photos = r.get("photos") if isinstance(r.get("photos"), list) else []
        area = feat.get("area_private") or feat.get("area_total") or ""
        rs = r.get("review_status")
        if rs not in (None, "like", "dislike"):
            rs = None
        rows.append(
            {
                "record_id": r.get("id"),
                "Preço": _fmt_money(r.get("price")),
                "price_sort": float(r["price"]) if r.get("price") is not None else None,
                "Bairro": r.get("neighborhood") or "",
                "Cidade": r.get("city") or "",
                "Tipo": feat.get("type") or "",
                "m²": str(area),
                "Cód.": feat.get("code"),
                "Fotos": len(photos),
                "Importado": str(r.get("imported_at") or "")[:19],
                "review_status": rs or "",
            }
        )
    return rows


def _open_detail_tab(record_id: str) -> None:
    ui.run_javascript(f'window.open("/imovel/{record_id}", "_blank")')


def _save_status_from_grid(record_id: str, new_status: str) -> None:
    new_status = (new_status or "").strip()
    if new_status not in ("", "like", "dislike"):
        return
    rec = _find_record(record_id)
    if not rec:
        ui.notify("Registo não encontrado.", type="negative")
        return
    all_recs, src = get_bundle()
    try:
        patch = {
            **{k: rec.get(k) for k in ("tags", "category", "rating", "notes", "comments", "review_status")},
            "review_status": new_status or None,
        }
        save_review_updates(src, all_recs, record_id, patch)
        invalidate()
        ui.notify("Estado guardado.", type="positive")
    except Exception as ex:
        ui.notify(str(ex), type="negative")


def render_detail_page(rid: str) -> None:
    """Conteúdo da página de detalhe (nova aba)."""
    rec = _find_record(rid)
    if not rec:
        ui.label("Imóvel não encontrado.").classes("text-negative text-h6 p-4")
        ui.link("← Voltar à lista", "/")
        return

    ui.link("← Lista principal", "/").classes("text-teal-800 mb-2")

    feat = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    chips = " · ".join(
        str(feat[k]) for k in ("type", "bedrooms", "bathrooms", "parking_spaces") if feat.get(k)
    )
    photos = [str(u) for u in (rec.get("photos") or []) if u]
    store = _photo_store()
    idx = int(store.get(rid, 0))
    if photos:
        idx = max(0, min(idx, len(photos) - 1))
    store[rid] = idx

    with ui.row().classes("w-full gap-4 flex-wrap"):
        with ui.column().classes("flex-1 min-w-[320px] gap-2"):
            ui.label(rec.get("title") or "—").classes("text-h5")
            if chips:
                ui.label(chips).classes("text-caption text-grey")
            if photos:

                def _step(delta: int) -> None:
                    n = len(photos)
                    if n <= 0:
                        return
                    i = int(_photo_store().get(rid, 0))
                    _photo_store()[rid] = (i + delta) % n
                    ui.navigate.reload()

                ui.image(photos[idx]).classes("w-full rounded-lg shadow-md max-h-[420px] object-contain")
                with ui.row().classes("items-center gap-2"):
                    ui.button(icon="chevron_left", on_click=lambda: _step(-1)).props("flat round dense")
                    ui.label(f"{idx + 1} / {len(photos)}")
                    ui.button(icon="chevron_right", on_click=lambda: _step(1)).props("flat round dense")
            else:
                ui.label("Sem fotos.").classes("text-grey")

            desc = rec.get("description") or ""
            if desc:
                with ui.expansion("Descrição", icon="article").classes("w-full"):
                    ui.markdown(str(desc).replace("\r\n", "\n")[:8000])

        with ui.column().classes("w-full max-w-md gap-3"):
            ui.metric("Preço", _fmt_money(rec.get("price")))
            ui.label(rec.get("address") or "—").classes("text-body2")
            if rec.get("source_url"):
                ui.link("Abrir no site", rec["source_url"], new_tab=True).classes("text-teal-700")

            rs_cur = rec.get("review_status")
            if rs_cur not in (None, "like", "dislike"):
                rs_cur = None
            with ui.row().classes("gap-2 items-center"):
                ui.label("Estado:").classes("text-weight-medium")
                status_sel = ui.select(
                    {"": "—", "like": "Gosto (like)", "dislike": "Não gosto (dislike)"},
                    value=rs_cur or "",
                ).props("dense outlined")

            tags_val = rec.get("tags")
            if isinstance(tags_val, list):
                tags_str = ", ".join(str(t) for t in tags_val)
            else:
                tags_str = str(tags_val or "")

            with ui.card().classes("w-full p-4"):
                ui.label("Revisão").classes("text-subtitle1")
                ti = ui.input("Tags (vírgulas)", value=tags_str)
                ci = ui.input("Categoria", value=str(rec.get("category") or ""))
                rt = ui.slider(min=0, max=5, step=0.5, value=0.0).props("label-always")
                rt.value = float(rec["rating"]) if rec.get("rating") is not None else 0.0
                nt = ui.textarea("Notas", value=str(rec.get("notes") or "")).classes("w-full")
                cm = ui.textarea("Comentários", value=str(rec.get("comments") or "")).classes("w-full")

                def save() -> None:
                    tags_list = [t.strip() for t in (ti.value or "").split(",") if t.strip()]
                    st_v = status_sel.value
                    if st_v not in ("", "like", "dislike"):
                        st_v = None
                    else:
                        st_v = st_v or None
                    review = {
                        "tags": tags_list,
                        "category": (ci.value or "").strip() or None,
                        "rating": float(rt.value) if float(rt.value) > 0 else None,
                        "notes": (nt.value or "").strip() or None,
                        "comments": (cm.value or "").strip() or None,
                        "review_status": st_v,
                    }
                    all_recs, src = get_bundle()
                    try:
                        save_review_updates(src, all_recs, str(rid), review)
                        invalidate()
                        ui.notify("Guardado.", type="positive")
                        ui.navigate.reload()
                    except Exception as ex:
                        ui.notify(str(ex), type="negative")

                ui.button("Guardar revisão", on_click=save).props("color=primary")


@ui.page("/imovel/{rid}")
def page_imovel(rid: str) -> None:
    mobihunter_header(active="list")
    with ui.column().classes("w-full max-w-6xl mx-auto p-4"):
        render_detail_page(rid)


@ui.page("/")
def index() -> None:
    get_bundle()  # garante cache; contagem no cabeçalho vem de layout

    mobihunter_header(active="list")
    ui.label(
        "Clique numa linha para abrir detalhes numa nova aba (cliques na coluna Estado só alteram like/dislike)."
    ).classes("text-xs opacity-90 px-4 py-1 bg-teal-900 text-white w-full block")

    @ui.refreshable
    def body() -> None:
        st_local = _user_state()
        recs, src = get_bundle()
        agencies = [""] + distinct_agencies(recs)
        ptypes = [""] + distinct_property_types(recs)
        tag_opts = all_tags_union(recs)

        pmn = float(st_local["price_min"]) if st_local.get("use_pmin") else None
        pmx = float(st_local["price_max"]) if st_local.get("use_pmax") else None
        if st_local.get("use_pmax") and pmx is not None and pmx <= 0:
            pmx = None

        filtered = apply_filters(
            recs,
            agency=st_local.get("agency") or None,
            price_min=pmn,
            price_max=pmx,
            text_query=st_local.get("text_q") or "",
            tags_any=st_local.get("tags") or None,
            property_type=st_local.get("property_type") or None,
        )
        filtered = sort_records(filtered, st_local.get("sort") or SORT_PRICE_ASC)

        page_items, pinfo = paginate(
            filtered,
            page=int(st_local.get("page") or 1),
            page_size=int(st_local.get("page_size") or DEFAULT_PAGE_SIZE),
        )
        if pinfo.page != st_local.get("page"):
            st_local["page"] = pinfo.page

        with ui.card().classes("w-full p-3 gap-2 shrink-0"):
            ui.label(f"{len(filtered)} resultados com estes filtros").classes("text-weight-medium text-sm")

            with ui.row().classes("w-full flex-wrap gap-x-3 gap-y-2 items-end"):
                agency_sel = ui.select(
                    {a: (a or "(todas)") for a in agencies},
                    value=st_local.get("agency") or "",
                    label="Imobiliária",
                ).classes("min-w-[160px]")
                pmin_in = ui.number(
                    label="Preço mín.",
                    value=float(st_local.get("price_min") or 0),
                    format="%.0f",
                ).props("dense outlined")
                use_pmin = ui.checkbox("Usar mín.", value=bool(st_local.get("use_pmin")))
                pmax_in = ui.number(
                    label="Preço máx.",
                    value=float(st_local.get("price_max") or 0),
                    format="%.0f",
                ).props("dense outlined")
                use_pmax = ui.checkbox("Usar máx.", value=bool(st_local.get("use_pmax")))
                q_in = ui.input(
                    label="Texto (bairro, título…)",
                    value=st_local.get("text_q") or "",
                ).classes("min-w-[200px]")
                tags_sel = ui.select(
                    tag_opts,
                    label="Tags (qualquer)",
                    multiple=True,
                    value=st_local.get("tags") or [],
                ).classes("min-w-[220px]")
                ptype_sel = ui.select(
                    {p: (p or "(todos)") for p in ptypes},
                    value=st_local.get("property_type") or "",
                    label="Tipo",
                ).classes("min-w-[180px]")
                sort_sel = ui.select(
                    {
                        SORT_PRICE_ASC: "Preço ↑",
                        SORT_PRICE_DESC: "Preço ↓",
                        SORT_IMPORTED_DESC: "Importado (recente)",
                        SORT_IMPORTED_ASC: "Importado (antigo)",
                    },
                    value=st_local.get("sort") or SORT_PRICE_ASC,
                    label="Ordenação",
                ).classes("min-w-[200px]")

            def apply_filters_click() -> None:
                st_local["agency"] = agency_sel.value or ""
                st_local["use_pmin"] = bool(use_pmin.value)
                st_local["price_min"] = float(pmin_in.value or 0)
                st_local["use_pmax"] = bool(use_pmax.value)
                st_local["price_max"] = float(pmax_in.value or 0)
                st_local["text_q"] = q_in.value or ""
                st_local["tags"] = list(tags_sel.value or [])
                st_local["property_type"] = ptype_sel.value or ""
                st_local["sort"] = sort_sel.value or SORT_PRICE_ASC
                st_local["page"] = 1
                body.refresh()

            with ui.row().classes("items-center gap-3 flex-wrap"):
                ui.button("Aplicar filtros", on_click=apply_filters_click).props("color=primary")
                ps_sel = ui.select(
                    [int(x) for x in PAGE_SIZE_OPTIONS],
                    value=int(st_local.get("page_size") or DEFAULT_PAGE_SIZE),
                    label=f"Linhas/página (máx. {MAX_PAGE_SIZE})",
                ).props("dense")

                def set_page_size() -> None:
                    st_local["page_size"] = int(ps_sel.value)
                    st_local["page"] = 1
                    body.refresh()

                ps_sel.on("update:model-value", lambda _: set_page_size())

                ui.label(
                    f"Página {pinfo.page}/{pinfo.total_pages} · "
                    f"{pinfo.start_1based}–{pinfo.end_1based} de {pinfo.total_items}"
                ).classes("text-sm")

                def go_page(delta: int) -> None:
                    np = max(1, min(pinfo.total_pages, pinfo.page + delta))
                    st_local["page"] = np
                    body.refresh()

                prev_b = ui.button("◀", on_click=lambda: go_page(-1)).props("flat dense")
                next_b = ui.button("▶", on_click=lambda: go_page(1)).props("flat dense")
                if pinfo.page <= 1:
                    prev_b.props("disable")
                if pinfo.page >= pinfo.total_pages:
                    next_b.props("disable")

        if not page_items:
            ui.label("Nenhum resultado.").classes("text-grey p-4")
            return

        grid_rows = _build_grid_rows(page_items)

        coldefs: list[dict[str, Any]] = [
            {
                "field": "review_status",
                "headerName": "Estado",
                "width": 112,
                "editable": True,
                "cellEditor": "agSelectCellEditor",
                "cellEditorParams": {"values": ["", "like", "dislike"]},
            },
            {
                "field": "Preço",
                "headerName": "Preço",
                "width": 120,
            },
            {
                "field": "price_sort",
                "headerName": "Preço (nº)",
                "hide": True,
                "type": "numericColumn",
            },
            {"field": "Bairro", "width": 130, "flex": 1, "minWidth": 100},
            {"field": "Cidade", "width": 100},
            {"field": "Tipo", "width": 130, "flex": 1, "minWidth": 90},
            {"field": "m²", "width": 64},
            {"field": "Cód.", "width": 82},
            {"field": "Fotos", "width": 64, "type": "numericColumn"},
            {"field": "Importado", "width": 140},
            {"field": "record_id", "hide": True},
        ]

        def on_cell_changed(e: Any) -> None:
            args = getattr(e, "args", None)
            if not isinstance(args, dict):
                return
            col = args.get("colDef") or {}
            field = col.get("field") if isinstance(col, dict) else None
            if field != "review_status":
                return
            row = args.get("data") or {}
            rid = row.get("record_id")
            if not rid:
                return
            new_val = args.get("newValue")
            if new_val not in ("", "like", "dislike", None):
                new_val = ""
            _save_status_from_grid(str(rid), str(new_val or ""))
            body.refresh()

        def on_row_clicked(e: Any) -> None:
            args = getattr(e, "args", None)
            if not isinstance(args, dict):
                return
            if args.get("colId") == "review_status":
                return
            row = args.get("data") or {}
            rid = row.get("record_id")
            if rid:
                _open_detail_tab(str(rid))

        grid = ui.aggrid(
            {
                "columnDefs": coldefs,
                "rowData": grid_rows,
                "defaultColDef": {
                    "sortable": True,
                    "filter": True,
                    "resizable": True,
                    "wrapHeaderText": True,
                    "autoHeaderHeight": True,
                },
                "rowHeight": 32,
                "headerHeight": 36,
                "rowSelection": {
                    "mode": "singleRow",
                    "checkboxes": False,
                    "enableClickSelection": True,
                },
            },
            theme="quartz",
            auto_size_columns=False,
        ).classes("w-full min-h-[420px] h-[min(85vh,calc(100dvh-12rem))]")
        grid.on("cellValueChanged", on_cell_changed)
        grid.on("rowClicked", on_row_clicked)

    body()


def main() -> None:
    secret = os.environ.get("MOBIHUNTER_STORAGE_SECRET", "mobihunter-dev-secret-change-in-prod")
    ui.run(
        title="Mobihunter",
        port=int(os.environ.get("MOBIHUNTER_PORT", str(DEFAULT_NICEGUI_PORT))),
        reload=False,
        favicon="🐾",
        storage_secret=secret,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
