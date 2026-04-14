"""Galeria de fotos: hero, miniaturas, navegação e diálogo fullscreen."""

from __future__ import annotations

import streamlit as st

from app_review.image_fetch import render_image_best_effort


@st.dialog("Foto")
def _fullscreen_dialog(url: str) -> None:
    render_image_best_effort(url, height=720)


def render_photo_gallery(
    photos: list[str],
    *,
    session_key_idx: str,
    thumb_cols: int = 8,
) -> None:
    """Mostra galeria com imagem principal e faixa de miniaturas."""
    if not photos:
        st.info("Sem fotos neste anúncio.")
        return

    if session_key_idx not in st.session_state:
        st.session_state[session_key_idx] = 0

    idx = int(st.session_state[session_key_idx])
    idx = max(0, min(idx, len(photos) - 1))

    hero_url = photos[idx]

    c1, c2, c3 = st.columns([1, 4, 1])
    with c1:
        if st.button("◀", key=f"{session_key_idx}_prev", disabled=len(photos) <= 1):
            st.session_state[session_key_idx] = (idx - 1) % len(photos)
            st.rerun()
    with c2:
        render_image_best_effort(hero_url, height=400)
    with c3:
        if st.button("▶", key=f"{session_key_idx}_next", disabled=len(photos) <= 1):
            st.session_state[session_key_idx] = (idx + 1) % len(photos)
            st.rerun()

    st.caption(f"Foto {idx + 1} de {len(photos)}")

    if len(photos) > 1:
        slide_val = st.slider(
            "Navegar",
            min_value=1,
            max_value=len(photos),
            value=idx + 1,
            key=f"{session_key_idx}_slider",
        )
        if slide_val - 1 != idx:
            st.session_state[session_key_idx] = slide_val - 1
            st.rerun()

    if st.button("Ver em grande", key=f"{session_key_idx}_big"):
        _fullscreen_dialog(hero_url)

    # Miniaturas (janela centrada)
    n = len(photos)
    span = min(thumb_cols, n)
    if span > 0:
        half = span // 2
        start = max(0, min(idx - half, n - span))
        row = photos[start : start + span]
        cols = st.columns(len(row))
        for j, url in enumerate(row):
            global_i = start + j
            with cols[j]:
                st.caption(f"{global_i + 1}" + (" ✓" if global_i == idx else ""))
                render_image_best_effort(url, height=96)
                if st.button(
                    "Ver",
                    key=f"{session_key_idx}_pick_{global_i}",
                    use_container_width=True,
                ):
                    st.session_state[session_key_idx] = global_i
                    st.rerun()


def reset_gallery_idx(session_key_idx: str) -> None:
    st.session_state[session_key_idx] = 0
