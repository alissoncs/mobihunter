"""Página de gráficos e tabela: preços e métricas por bairro / cidade."""

from __future__ import annotations

from typing import Any

from nicegui import app, ui

from app_review.neighborhood_stats import aggregate_by_neighborhood, distinct_cities_sorted
from nicegui_app.cache import get_bundle
from nicegui_app.layout import mobihunter_header


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    s = f"{float(v):,.0f}"
    return "R$ " + s.replace(",", ".")


def _fmt_money_m2(v: float | None) -> str:
    if v is None:
        return "—"
    s = f"{float(v):,.0f}"
    return "R$ " + s.replace(",", ".") + "/m²"


def _fmt_m2(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.1f}".replace(",", ".")


def _reports_state() -> dict[str, Any]:
    if "reports" not in app.storage.user:
        app.storage.user["reports"] = {"city": ""}
    return app.storage.user["reports"]


def _bar_label(r: dict[str, Any], *, single_city: bool) -> str:
    if single_city:
        return str(r["neighborhood"])
    c, h = str(r["city"]), str(r["neighborhood"])
    if len(c) + len(h) > 32:
        return f"{c[:14]}… · {h[:14]}"
    return f"{c} · {h}"


def _chart_count_by_hood(
    rows: list[dict[str, Any]], *, max_bars: int = 16, single_city: bool = False
) -> dict[str, Any]:
    top = rows[:max_bars]
    labels = [_bar_label(r, single_city=single_city) for r in top]
    data = [r["count"] for r in top]
    return {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "3%", "right": "4%", "bottom": "18%", "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": labels,
            "axisLabel": {"rotate": 35, "interval": 0},
        },
        "yAxis": {"type": "value", "name": "Imóveis"},
        "series": [
            {
                "name": "Quantidade",
                "type": "bar",
                "data": data,
                "itemStyle": {"color": "#0d9488"},
            }
        ],
    }


def _chart_median_price(
    rows: list[dict[str, Any]], *, max_bars: int = 16, single_city: bool = False
) -> dict[str, Any]:
    with_price = [r for r in rows if r.get("price_median") is not None][:max_bars]
    labels = [_bar_label(r, single_city=single_city) for r in with_price]
    data = [round(float(r["price_median"]), 0) for r in with_price]
    return {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "3%", "right": "4%", "bottom": "18%", "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": labels,
            "axisLabel": {"rotate": 35, "interval": 0},
        },
        "yAxis": {"type": "value", "name": "R$ (mediana)"},
        "series": [
            {
                "name": "Preço mediano",
                "type": "bar",
                "data": data,
                "itemStyle": {"color": "#6366f1"},
            }
        ],
    }


def _chart_median_brl_m2(
    rows: list[dict[str, Any]], *, max_bars: int = 16, single_city: bool = False
) -> dict[str, Any]:
    with_ppm = [r for r in rows if r.get("brl_m2_median") is not None][:max_bars]
    labels = [_bar_label(r, single_city=single_city) for r in with_ppm]
    data = [round(float(r["brl_m2_median"]), 0) for r in with_ppm]
    return {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "3%", "right": "4%", "bottom": "18%", "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": labels,
            "axisLabel": {"rotate": 35, "interval": 0},
        },
        "yAxis": {"type": "value", "name": "R$/m² (mediana)"},
        "series": [
            {
                "name": "R$/m² mediano",
                "type": "bar",
                "data": data,
                "itemStyle": {"color": "#ea580c"},
            }
        ],
    }


@ui.page("/relatorios")
def reports_page() -> None:
    mobihunter_header(active="reports")

    @ui.refreshable
    def content() -> None:
        st = _reports_state()
        records, _ = get_bundle()
        cities = distinct_cities_sorted(records)
        city_val = st.get("city") or ""
        if city_val and city_val not in cities:
            city_val = ""
            st["city"] = ""

        rows = aggregate_by_neighborhood(records, city=city_val if city_val else None)
        total_imoveis = sum(r["count"] for r in rows)
        n_bairros = len(rows)
        n_cidades = len(cities) if not city_val else 1

        with ui.row().classes("w-full flex-wrap gap-3 p-4"):
            with ui.card().classes("p-4 min-w-[140px]"):
                ui.label("Imóveis (filtro)").classes("text-caption text-grey")
                ui.label(str(total_imoveis)).classes("text-h6")
            with ui.card().classes("p-4 min-w-[140px]"):
                ui.label("Cidades" if not city_val else "Cidade").classes("text-caption text-grey")
                ui.label(str(n_cidades)).classes("text-h6")
            with ui.card().classes("p-4 min-w-[140px]"):
                ui.label("Bairros listados").classes("text-caption text-grey")
                ui.label(str(n_bairros)).classes("text-h6")

        with ui.card().classes("w-full p-4 mx-4 mb-2"):
            ui.label("Filtro").classes("text-subtitle1 text-weight-medium")
            city_opts = {"": "(todas as cidades)"}
            for c in cities:
                city_opts[c] = c
            cs = ui.select(
                city_opts,
                value=city_val,
                label="Cidade",
            ).classes("min-w-[260px] max-w-full")

            def on_city() -> None:
                st["city"] = cs.value or ""
                content.refresh()

            cs.on("update:model-value", lambda _: on_city())

        hint = (
            "Bairros apenas desta cidade; eixos dos gráficos mostram o nome do bairro."
            if city_val
            else "Cada linha é cidade + bairro. Nos gráficos, o eixo usa «cidade · bairro» para distinguir zonas."
        )
        ui.label(hint).classes("text-caption text-grey px-4 pb-2")

        if not rows:
            ui.label("Sem dados para mostrar.").classes("text-grey p-8")
            return

        sc = bool(city_val)
        with ui.row().classes("w-full flex-wrap gap-4 px-4 pb-4"):
            with ui.card().classes("p-3 flex-1 min-w-[320px]"):
                ui.label("Imóveis por bairro (top 16)").classes("text-subtitle2 q-mb-sm")
                ui.echart(_chart_count_by_hood(rows, single_city=sc)).classes("w-full h-[360px]")
            with ui.card().classes("p-3 flex-1 min-w-[320px]"):
                ui.label("Preço mediano por bairro (top 16 com preço)").classes("text-subtitle2 q-mb-sm")
                ui.echart(_chart_median_price(rows, single_city=sc)).classes("w-full h-[360px]")

        with ui.row().classes("w-full px-4 pb-6"):
            with ui.card().classes("p-3 w-full min-w-[320px]"):
                ui.label("R$/m² mediano (imóveis com preço e área; top 16)").classes("text-subtitle2 q-mb-sm")
                ui.echart(_chart_median_brl_m2(rows, single_city=sc)).classes("w-full h-[360px]")

        table_rows = []
        for r in rows:
            table_rows.append(
                {
                    "Cidade": r["city"],
                    "Bairro": r["neighborhood"],
                    "N": r["count"],
                    "Com preço": r["with_price"],
                    "Preço min": _fmt_money(r["price_min"]),
                    "Preço med.": _fmt_money(r["price_median"]),
                    "Preço máx": _fmt_money(r["price_max"]),
                    "m² med.": _fmt_m2(r["m2_median"]),
                    "R$/m² med.": _fmt_money_m2(r["brl_m2_median"]),
                }
            )

        with ui.card().classes("w-full p-4 mx-4 mb-8"):
            ui.label("Tabela por bairro").classes("text-subtitle1 q-mb-md")
            ui.aggrid(
                {
                    "columnDefs": [
                        {"field": "Cidade", "width": 140},
                        {"field": "Bairro", "minWidth": 160, "flex": 1},
                        {"field": "N", "width": 70, "type": "numericColumn"},
                        {"field": "Com preço", "width": 110, "type": "numericColumn"},
                        {"field": "Preço min", "width": 130},
                        {"field": "Preço med.", "width": 130},
                        {"field": "Preço máx", "width": 130},
                        {"field": "m² med.", "width": 100},
                        {"field": "R$/m² med.", "width": 120},
                    ],
                    "rowData": table_rows,
                    "defaultColDef": {"sortable": True, "filter": True, "resizable": True},
                },
                theme="quartz",
            ).classes("w-full max-h-[520px] min-h-[280px]")

    content()
