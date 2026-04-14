"""App Streamlit de revisão de imóveis (Mobihunter). Executar na raiz do repositório."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from app_review.constants import (
    DEFAULT_PAGE_SIZE,
    PAGE_SIZE_OPTIONS,
    SESSION_PAGE,
    SESSION_PAGE_SIZE,
    SESSION_PHOTO_IDX,
    SESSION_SELECTED_ID,
    SESSION_SORT,
    SESSION_SOURCE,
    SORT_IMPORTED_ASC,
    SORT_IMPORTED_DESC,
    SORT_PRICE_ASC,
    SORT_PRICE_DESC,
)
from app_review.data_source import (
    data_snapshot_key,
    load_records_uncached,
    parse_tags_csv,
    save_review_updates,
)
from app_review.filters import (
    all_tags_union,
    apply_filters,
    distinct_agencies,
    distinct_property_types,
    sort_records,
)
from app_review.pagination import paginate
from app_review.photo_gallery import render_photo_gallery, reset_gallery_idx


@st.cache_data
def _cached_load(_key: tuple[str, str, int]) -> tuple[list[dict[str, Any]], str]:
    return load_records_uncached()


def _fmt_money(v: Any) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    s = f"{x:,.0f}"
    return "R$ " + s.replace(",", ".")


def _area_cell_str(feat: dict[str, Any]) -> str:
    a = feat.get("area_private") or feat.get("area_total")
    if a is None or a == "":
        return ""
    return str(a).strip()


def _build_results_df(page_items: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for r in page_items:
        feat = r.get("features") if isinstance(r.get("features"), dict) else {}
        photos = r.get("photos") if isinstance(r.get("photos"), list) else []
        price = r.get("price")
        try:
            pnum = float(price) if price is not None else None
        except (TypeError, ValueError):
            pnum = None
        code = feat.get("code")
        cod_s = str(code).strip() if code is not None else ""
        rows.append(
            {
                "Preço": pnum,
                "Bairro": str(r.get("neighborhood") or ""),
                "Cidade": str(r.get("city") or ""),
                "Tipo": str(feat.get("type") or ""),
                "m²": _area_cell_str(feat),
                "Cód.": cod_s,
                "Título": str(r.get("title") or "")[:100],
                "Fotos": len(photos),
                "Agência": str(r.get("agency") or ""),
                "Importado": str(r.get("imported_at") or "")[:19],
            }
        )
    return pd.DataFrame(rows)


def _table_selected_index(state_key: str, n: int) -> int:
    if n <= 0:
        return 0
    stt = st.session_state.get(state_key)
    if stt is None:
        return 0
    rows: list = []
    try:
        if hasattr(stt, "selection"):
            sel = stt.selection
            rows = list(getattr(sel, "rows", []) or [])
        elif isinstance(stt, dict):
            rows = list(stt.get("selection", {}).get("rows", []) or [])
    except Exception:
        rows = []
    if rows:
        i = int(rows[0])
        if 0 <= i < n:
            return i
    return 0


def _init_session() -> None:
    if SESSION_PAGE not in st.session_state:
        st.session_state[SESSION_PAGE] = 1
    if SESSION_PAGE_SIZE not in st.session_state:
        st.session_state[SESSION_PAGE_SIZE] = DEFAULT_PAGE_SIZE
    if SESSION_SORT not in st.session_state:
        st.session_state[SESSION_SORT] = SORT_PRICE_ASC


def main() -> None:
    st.set_page_config(
        page_title="Mobihunter — Revisão",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _init_session()

    snap = data_snapshot_key()
    records, source = _cached_load(snap)
    st.session_state[SESSION_SOURCE] = source

    st.markdown("### Mobihunter")
    st.caption(
        f"{len(records)} imóveis · fonte **{source}** · "
        "Selecione uma linha na tabela para ver fotos e editar a revisão."
    )

    if not records:
        st.warning("Nenhum imóvel encontrado. Importe dados com `python scripts/importers/foxter.py`.")
        return

    # ——— Filtros no topo
    agencies = [""] + distinct_agencies(records)
    ptypes = [""] + distinct_property_types(records)
    tag_opts = all_tags_union(records)

    with st.container():
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            agency = st.selectbox("Imobiliária", agencies, format_func=lambda x: x or "(todas)")
        with c2:
            price_min = st.number_input("Preço mín. (R$)", value=0.0, min_value=0.0, step=50000.0)
            use_pmin = st.checkbox("Usar mínimo", value=False, key="use_pmin")
        with c3:
            price_max = st.number_input("Preço máx. (R$)", value=0.0, min_value=0.0, step=50000.0)
            use_pmax = st.checkbox("Usar máximo", value=False, key="use_pmax")
        with c4:
            sort_key = st.selectbox(
                "Ordenação",
                [
                    SORT_PRICE_ASC,
                    SORT_PRICE_DESC,
                    SORT_IMPORTED_DESC,
                    SORT_IMPORTED_ASC,
                ],
                format_func=lambda k: {
                    SORT_PRICE_ASC: "Preço ↑",
                    SORT_PRICE_DESC: "Preço ↓",
                    SORT_IMPORTED_DESC: "Importado (recente)",
                    SORT_IMPORTED_ASC: "Importado (antigo)",
                }.get(k, k),
                key="sort_sel",
            )

        c5, c6, c7 = st.columns([2, 2, 2])
        with c5:
            text_q = st.text_input("Bairro, endereço, título…", "")
        with c6:
            tags_sel = st.multiselect("Tags (qualquer uma)", tag_opts, default=[])
        with c7:
            ptype = st.selectbox("Tipo de imóvel", ptypes, format_func=lambda x: x or "(todos)")

    pmn = float(price_min) if use_pmin else None
    pmx = float(price_max) if use_pmax else None
    if use_pmax and pmx <= 0:
        pmx = None

    filtered = apply_filters(
        records,
        agency=agency or None,
        price_min=pmn,
        price_max=pmx,
        text_query=text_q,
        tags_any=tags_sel or None,
        property_type=ptype or None,
    )
    filtered = sort_records(filtered, sort_key)

    sig = (agency, use_pmin, pmn, use_pmax, pmx, text_q, tuple(tags_sel), ptype, sort_key)
    if st.session_state.get("_filter_sig") != sig:
        st.session_state[SESSION_PAGE] = 1
        st.session_state["_filter_sig"] = sig

    st.session_state[SESSION_SORT] = sort_key

    st.markdown(f"**{len(filtered)}** imóveis com estes filtros")

    try:
        _psi = PAGE_SIZE_OPTIONS.index(int(st.session_state[SESSION_PAGE_SIZE]))
    except ValueError:
        _psi = 1
    p1, p2, p3 = st.columns([1, 1, 2])
    with p1:
        ps = st.selectbox("Linhas por página", PAGE_SIZE_OPTIONS, index=_psi)
        st.session_state[SESSION_PAGE_SIZE] = int(ps)
    page_items, pinfo = paginate(
        filtered,
        page=st.session_state[SESSION_PAGE],
        page_size=st.session_state[SESSION_PAGE_SIZE],
    )
    if pinfo.page != st.session_state[SESSION_PAGE]:
        st.session_state[SESSION_PAGE] = pinfo.page

    with p2:
        st.caption(
            f"Página **{pinfo.page}** / {pinfo.total_pages} · "
            f"linhas {pinfo.start_1based}–{pinfo.end_1based} de {pinfo.total_items}"
        )
    with p3:
        b1, b2 = st.columns(2)
        with b1:
            if st.button("◀ Página anterior", disabled=pinfo.page <= 1, use_container_width=True):
                st.session_state[SESSION_PAGE] = pinfo.page - 1
                st.rerun()
        with b2:
            if st.button("Página seguinte ▶", disabled=pinfo.page >= pinfo.total_pages, use_container_width=True):
                st.session_state[SESSION_PAGE] = pinfo.page + 1
                st.rerun()

    if not page_items:
        st.info("Nenhum resultado nesta página.")
        return

    df = _build_results_df(page_items)
    column_config: dict[str, Any] = {
        "Preço": st.column_config.NumberColumn(
            "Preço (R$)",
            format="localized",
            help="Valor do anúncio",
        ),
        "Bairro": st.column_config.TextColumn("Bairro", width="small"),
        "Cidade": st.column_config.TextColumn("Cidade", width="small"),
        "Tipo": st.column_config.TextColumn("Tipo", width="medium"),
        "m²": st.column_config.TextColumn("m²", width="small"),
        "Cód.": st.column_config.TextColumn("Cód.", width="small"),
        "Título": st.column_config.TextColumn("Título", width="large"),
        "Fotos": st.column_config.NumberColumn("Fotos", format="plain", width="small"),
        "Agência": st.column_config.TextColumn("Agência", width="small"),
        "Importado": st.column_config.TextColumn("Importado", width="small"),
    }

    st.markdown("##### Tabela")
    st.dataframe(
        df,
        use_container_width=True,
        height=min(520, 48 + len(page_items) * 36),
        hide_index=True,
        key="properties_table",
        on_select="rerun",
        selection_mode="single-row-required",
        row_height=36,
        column_config=column_config,
    )

    ri = _table_selected_index("properties_table", len(page_items))
    rec = page_items[ri]
    sel_id = rec.get("id")
    st.session_state[SESSION_SELECTED_ID] = sel_id

    st.divider()
    _detail_panel(rec, records, source)


def _detail_panel(
    rec: dict[str, Any],
    all_records: list[dict[str, Any]],
    source: str,
) -> None:
    st.markdown(f"#### {rec.get('title') or 'Sem título'}")

    feat = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    chips = []
    for k in ("type", "bedrooms", "bathrooms", "parking_spaces"):
        if feat.get(k):
            chips.append(str(feat[k]))
    ap = feat.get("area_private") or feat.get("area_total")
    if ap:
        chips.append(f"{ap} m²")
    if chips:
        st.caption(" · ".join(chips))

    photos = rec.get("photos") if isinstance(rec.get("photos"), list) else []
    urls = [str(u) for u in photos if u]
    sk = f"{SESSION_PHOTO_IDX}_{rec.get('id', '')}"

    tags = rec.get("tags")
    if isinstance(tags, list):
        tags_s = ", ".join(str(t) for t in tags)
    else:
        tags_s = str(tags or "")

    col_gal, col_meta = st.columns([1.15, 1], gap="large")

    with col_gal:
        render_photo_gallery(urls, session_key_idx=sk)
        desc = rec.get("description") or ""
        if desc:
            with st.expander("Descrição completa"):
                st.write(desc)

    with col_meta:
        st.metric("Preço", _fmt_money(rec.get("price")))
        st.caption(rec.get("address") or "—")

        if rec.get("_price_previous") is not None:
            st.caption(
                f"Preço anterior: {_fmt_money(rec.get('_price_previous'))} · "
                f"alterações de preço: {rec.get('_price_change_count', 0)}"
            )

        src = rec.get("source_url")
        if src:
            st.markdown(f"[Abrir anúncio no site ↗]({src})")

        st.markdown("##### Revisão")
        rid = str(rec.get("id") or "")
        with st.form(f"review_form_{rid}"):
            tags_in = st.text_input("Tags (vírgulas)", value=tags_s)
            cat = st.text_input("Categoria", value=str(rec.get("category") or ""))
            rating = st.slider(
                "Classificação",
                0.0,
                5.0,
                float(rec["rating"]) if rec.get("rating") is not None else 0.0,
                0.5,
            )
            notes = st.text_area("Notas", value=str(rec.get("notes") or ""))
            comments = st.text_area("Comentários", value=str(rec.get("comments") or ""))
            submitted = st.form_submit_button("Guardar revisão")

    if submitted:
        review = {
            "tags": parse_tags_csv(tags_in),
            "category": cat.strip() or None,
            "rating": rating if rating > 0 else None,
            "notes": notes.strip() or None,
            "comments": comments.strip() or None,
        }
        rid = rec.get("id")
        if not rid:
            st.error("Registo sem id.")
            return
        try:
            save_review_updates(source, all_records, str(rid), review)
            _cached_load.clear()
            reset_gallery_idx(sk)
            st.success("Guardado.")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao guardar: {e}")


if __name__ == "__main__":
    main()
