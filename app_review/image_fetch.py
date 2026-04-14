"""Descarrega imagens por HTTP com cabeçalhos de browser — muitos CDNs bloqueiam pedidos anónimos."""

from __future__ import annotations

import html
from io import BytesIO
from typing import Any

import httpx
import streamlit as st
import streamlit.components.v1 as components

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _headers_for_url(url: str) -> dict[str, str]:
    h: dict[str, str] = {
        "User-Agent": _DEFAULT_UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    }
    u = url.lower()
    if "foxter" in u or "blob.foxter" in u:
        h["Referer"] = "https://www.foxterciaimobiliaria.com.br/"
    return h


@st.cache_data(ttl=3600, show_spinner=False, max_entries=300)
def fetch_image_bytes(url: str) -> bytes | None:
    """Descarrega bytes da imagem; None se falhar."""
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return None
    try:
        with httpx.Client(timeout=25.0, follow_redirects=True) as client:
            r = client.get(url, headers=_headers_for_url(url))
            r.raise_for_status()
            data = r.content
            if len(data) < 32:
                return None
            return data
    except Exception:
        return None


def image_to_streamlit(data: bytes | None) -> Any:
    """Objeto aceite por st.image (bytes ou BytesIO)."""
    if not data:
        return None
    return BytesIO(data)


def render_image_best_effort(url: str, *, height: int = 420) -> bool:
    """
    Tenta mostrar a imagem: primeiro bytes (httpx), senão `<img>` no browser
    (muitas CDNs só servem com cookies/referrer do browser).
    Devolve True se mostrou algo.
    """
    if not url or not str(url).startswith("http"):
        return False
    data = fetch_image_bytes(url)
    buf = image_to_streamlit(data)
    if buf is not None:
        try:
            st.image(buf, use_container_width=True)
            return True
        except Exception:
            pass
    esc = html.escape(str(url), quote=True)
    components.html(
        f"""
        <div style="text-align:center;background:#11182708;padding:8px;border-radius:8px;">
          <img src="{esc}" referrerpolicy="no-referrer-when-downgrade"
               style="max-width:100%;max-height:{height}px;height:auto;object-fit:contain;border-radius:6px;"
               loading="lazy" alt="" />
        </div>
        """,
        height=min(height + 40, 520),
    )
    return True
