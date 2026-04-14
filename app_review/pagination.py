"""Paginação sobre listas já filtradas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from app_review.constants import MAX_PAGE_SIZE

T = TypeVar("T")


@dataclass(frozen=True)
class PageInfo:
    page: int
    page_size: int
    total_items: int
    total_pages: int
    start_1based: int
    end_1based: int


def clamp_page(page: int, total_pages: int) -> int:
    if total_pages < 1:
        return 1
    return max(1, min(page, total_pages))


def paginate(items: list[T], *, page: int, page_size: int) -> tuple[list[T], PageInfo]:
    n = len(items)
    if page_size < 1:
        page_size = 1
    page_size = min(page_size, MAX_PAGE_SIZE)
    total_pages = max(1, (n + page_size - 1) // page_size) if n else 1
    page = clamp_page(page, total_pages)
    start = (page - 1) * page_size
    end = min(start + page_size, n)
    slice_items = items[start:end]
    start_1 = start + 1 if n else 0
    end_1 = end if n else 0
    info = PageInfo(
        page=page,
        page_size=page_size,
        total_items=n,
        total_pages=total_pages,
        start_1based=start_1,
        end_1based=end_1,
    )
    return slice_items, info
