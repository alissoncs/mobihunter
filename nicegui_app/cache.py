"""Cache em memória da lista completa (invalidar após gravar revisão)."""

from __future__ import annotations

from typing import Any

_bundle: tuple[list[dict[str, Any]], str] | None = None


def get_bundle() -> tuple[list[dict[str, Any]], str]:
    global _bundle
    if _bundle is None:
        from app_review.data_source import load_records_uncached

        _bundle = load_records_uncached()
    return _bundle


def invalidate() -> None:
    global _bundle
    _bundle = None
