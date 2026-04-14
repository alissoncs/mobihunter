"""Layout partilhado (cabeçalho e navegação)."""

from __future__ import annotations

from typing import Literal

from nicegui import ui

from nicegui_app.cache import get_bundle

NavKey = Literal["list", "reports"]


def mobihunter_header(*, active: NavKey = "list") -> None:
    records, source = get_bundle()
    ui.colors(primary="#0f766e")
    with ui.header().classes("items-center justify-between px-4 py-2 bg-teal-800 text-white"):
        with ui.row().classes("items-center gap-4 flex-wrap"):
            ui.link("Mobihunter", "/").classes("text-h5 font-bold text-white no-underline hover:underline")
            with ui.row().classes("items-center gap-1"):
                link_cls = "px-3 py-1 rounded text-white no-underline"
                active_cls = " bg-teal-950 font-medium"
                inactive_cls = " opacity-90 hover:opacity-100 hover:bg-teal-700"
                ui.link("Imóveis", "/").classes(
                    link_cls + (active_cls if active == "list" else inactive_cls)
                )
                ui.link("Relatórios (bairro/cidade)", "/relatorios").classes(
                    link_cls + (active_cls if active == "reports" else inactive_cls)
                )
        ui.label(f"Fonte: {source} · {len(records)} imóveis").classes("text-sm opacity-90")
