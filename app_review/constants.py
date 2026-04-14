"""Constantes da app de revisão."""

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000
PAGE_SIZE_OPTIONS: tuple[int, ...] = (25, 50, 100, 200, 500, 1000)

SESSION_RECORDS = "mh_records"
SESSION_SOURCE = "mh_source"
SESSION_SELECTED_ID = "mh_selected_id"
SESSION_PHOTO_IDX = "mh_photo_idx"
SESSION_PAGE = "mh_page"
SESSION_PAGE_SIZE = "mh_page_size"
SESSION_SORT = "mh_sort"

SORT_PRICE_ASC = "price_asc"
SORT_PRICE_DESC = "price_desc"
SORT_IMPORTED_ASC = "imported_asc"
SORT_IMPORTED_DESC = "imported_desc"
