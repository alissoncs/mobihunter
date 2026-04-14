"""
Importador Foxter (Foxter Cia Imobiliária e outros domínios Foxter).

- URL de **imóvel** (página de detalhe): obtém JSON em __NEXT_DATA__ (preço, fotos, texto).
- URL de **busca** (listagem): por defeito pede cada `?page=N` via **HTTP em paralelo** e extrai
  códigos do HTML; se falhar, usa Playwright (sequencial). Cada imóvel é gravado no SQLite logo
  após o detalhe HTTP (não só no fim).

Uso (na raiz do projeto):
  python scripts/importers/foxter.py
  (lê por defeito config/foxter_urls.json se existir)

  python scripts/importers/foxter.py --config config/foxter_urls.json
  python scripts/importers/foxter.py --url https://www.foxterciaimobiliaria.com.br/imovel/123
  python scripts/importers/foxter.py --search-url "https://www.foxterciaimobiliaria.com.br/imoveis/..."
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Permite correr como script sem instalar pacote
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.importers.common import normalize_source_url  # noqa: E402
from scripts.importers.sqlite_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    connect_db,
    init_schema,
    upsert_import_records,
)

try:
    import httpx
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "Instale dependências: pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None  # type: ignore[misc, assignment]

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore[misc, assignment]

AGENCY = "foxter"
DEFAULT_CONFIG_PATH = _ROOT / "config" / "foxter_urls.json"
FOXT_CIA_HOST = "www.foxterciaimobiliaria.com.br"
# CDN usado no HTML de detalhe (carousel); o JSON antigo usa blob.foxter — normalizamos para este padrão.
FOXT_CDN_WM_480 = "https://images.foxter.com.br/rest/image/outer/480/1/foxter/wm/"
# "networkidle" pode não ocorrer em SPAs (analytics, polling) e parece “travar”.
_PLAYWRIGHT_GOTO_WAIT = "load"
DEFAULT_HTTP_HEADERS = {
    "User-Agent": "MobihunterImporter/1.0 (+local)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def _warn(msg: str) -> None:
    print(f"[foxter] WARN {msg}", file=sys.stderr, flush=True)


def _parse_price(text: str | None) -> float | None:
    if not text:
        return None
    digits = re.sub(r"[^\d,.]", "", text)
    if not digits:
        return None
    if "," in digits and "." in digits:
        digits = digits.replace(".", "").replace(",", ".")
    elif "," in digits:
        digits = digits.replace(",", ".")
    else:
        digits = digits.replace(".", "")
    try:
        return float(digits)
    except ValueError:
        return None


def is_foxter_cia_search_url(url: str) -> bool:
    u = url.lower()
    if FOXT_CIA_HOST not in u:
        return False
    if "/imoveis/" not in u:
        return False
    if re.search(r"/imovel/\d+", url):
        return False
    return True


def is_foxter_cia_imovel_url(url: str) -> bool:
    return FOXT_CIA_HOST in url.lower() and bool(re.search(r"/imovel/\d+", url))


def imovel_code_from_url(url: str) -> int | None:
    m = re.search(r"/imovel/(\d+)", url)
    return int(m.group(1)) if m else None


def set_page_query(url: str, page: int) -> str:
    p = urlparse(url.strip())
    q = parse_qs(p.query)
    q["page"] = [str(page)]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q, doseq=True), ""))


def _abs_url(url: str) -> str:
    u = url.strip()
    if u.startswith("//"):
        return "https:" + u
    return u


def _is_foxter_cdn_image_url(url: str) -> bool:
    u = url.lower()
    return "foxter.com.br" in u and ("/rest/image/" in u or "/product/" in u.lower())


def foxter_cdn_url_from_etag(etag: str) -> str:
    """Monta URL do mesmo padrão do carousel (wm + 480) a partir do etag do JSON."""
    etag = etag.strip().lstrip("/")
    return FOXT_CDN_WM_480 + etag


def foxter_photos_from_product_json(product: dict[str, Any]) -> list[str]:
    """Fotos a partir de images.data[].etag com CDN images.foxter (wm/480)."""
    out: list[str] = []
    images = product.get("images") or {}
    for item in images.get("data") or []:
        etag = item.get("etag")
        if not etag:
            continue
        out.append(foxter_cdn_url_from_etag(str(etag)))
    return out


def foxter_photos_from_detail_soup(soup: Any) -> list[str]:
    """Extrai URLs do carousel a partir de um soup já construído (evita segundo parse)."""
    ordered: list[str] = []
    seen: set[str] = set()
    for srcset_el in soup.select("picture source[srcset]"):
        raw = (srcset_el.get("srcset") or "").strip()
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            u = _abs_url(chunk.split()[0])
            if _is_foxter_cdn_image_url(u) and u not in seen:
                seen.add(u)
                ordered.append(u)
    for img in soup.select(
        'img[src*="images.foxter.com.br"], img[src*="blob.foxter"], '
        'img[id^="imovel-carousel-photo-"]'
    ):
        u = _abs_url((img.get("src") or "").strip())
        if u and _is_foxter_cdn_image_url(u) and u not in seen:
            seen.add(u)
            ordered.append(u)
    return ordered


def foxter_photos_from_detail_html(html: str) -> list[str]:
    """Extrai URLs do carousel (<picture srcset>, <img>) como no HTML real da Foxter."""
    return foxter_photos_from_detail_soup(BeautifulSoup(html, "html.parser"))


def _merge_photo_urls(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for u in lst:
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out


def _blob_to_images_host(url: str) -> str:
    if "blob.foxter.com.br" in url:
        return url.replace("blob.foxter.com.br", "images.foxter.com.br", 1)
    return url


def _photo_url_variants(url: str) -> list[str]:
    """Tenta wm e sem wm, e host images vs blob."""
    u = url.strip()
    seen: set[str] = set()
    order: list[str] = []

    def add(x: str) -> None:
        if x and x not in seen:
            seen.add(x)
            order.append(x)

    add(u)
    add(_blob_to_images_host(u))
    if "/foxter/wm/" in u:
        add(u.replace("/foxter/wm/", "/foxter/", 1))
    else:
        if "/foxter/" in u and "/foxter/wm/" not in u:
            add(u.replace("/foxter/", "/foxter/wm/", 1))
    return order


def _url_reachable(client: httpx.Client, url: str) -> bool:
    try:
        r = client.head(url, follow_redirects=True, timeout=4.0)
        if r.status_code == 200:
            return True
        if r.status_code in (405, 501):
            g = client.get(url, follow_redirects=True, timeout=6.0)
            return g.status_code == 200
    except Exception:
        pass
    try:
        g = client.get(url, follow_redirects=True, timeout=6.0)
        return g.status_code == 200
    except Exception:
        return False


def resolve_foxter_photo_url(client: httpx.Client, url: str) -> str | None:
    """Devolve a primeira variante da URL que responder OK."""
    for cand in _photo_url_variants(url):
        if _url_reachable(client, cand):
            return cand
    return None


def finalize_foxter_photos(
    client: httpx.Client,
    record: dict[str, Any],
    *,
    check_reachable: bool,
    max_photo_checks: int | None = None,
    photo_check_workers: int = 6,
) -> None:
    """
    Filtra fotos para URLs que existem (HEAD/GET). Se nenhuma existir, avisa no stderr.
    Reimportações: substitui sempre a lista em `record` (atualiza a base no upsert).
    """
    raw_list = list(record.get("photos") or [])
    if max_photo_checks is not None and len(raw_list) > max_photo_checks:
        raw_list = raw_list[:max_photo_checks]
        record["photos"] = raw_list
    if not raw_list:
        code = record.get("listing_code") or record.get("features", {}).get("code")
        _warn(f"sem imagens no JSON/HTML · código {code}")
        record["thumbnail_url"] = None
        return

    if not check_reachable:
        record["thumbnail_url"] = raw_list[0]
        return

    def resolve_one(u: str) -> str | None:
        return resolve_foxter_photo_url(client, u)

    if photo_check_workers <= 1 or len(raw_list) <= 1:
        ok = [x for x in (resolve_one(u) for u in raw_list) if x]
    else:
        nw = min(photo_check_workers, len(raw_list))
        with ThreadPoolExecutor(max_workers=nw) as ex:
            ok = [x for x in ex.map(resolve_one, raw_list) if x]

    record["photos"] = ok
    record["thumbnail_url"] = ok[0] if ok else None

    if not ok:
        code = record.get("listing_code") or record.get("features", {}).get("code")
        _warn(f"nenhuma URL de foto respondeu (HEAD/GET) · código {code}")


def parse_foxter_cia_detail_html(html: str) -> tuple[dict[str, Any], list[str]]:
    """Um único parse BeautifulSoup: produto em __NEXT_DATA__ + URLs do carousel."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("script", id="__NEXT_DATA__")
    if not el or not el.string:
        raise ValueError("Página sem __NEXT_DATA__ (não é detalhe Foxter Cia?).")
    data = json.loads(el.string)
    pp = data.get("props", {}).get("pageProps") or {}
    if "product" not in pp:
        raise ValueError("pageProps sem product.")
    prod = pp["product"]
    if not isinstance(prod, dict):
        raise ValueError("product inválido.")
    carousel = foxter_photos_from_detail_soup(soup)
    return prod, carousel


def product_to_record(
    product: dict[str, Any],
    *,
    detail_html: str | None = None,
    carousel_urls: list[str] | None = None,
) -> dict[str, Any]:
    canonical = product.get("canonical")
    if not canonical and product.get("code") is not None:
        canonical = f"https://{FOXT_CIA_HOST}/imovel/{product['code']}"
    if not canonical:
        raise ValueError("Product sem canonical nem code.")
    source_url = normalize_source_url(canonical)

    from_json = foxter_photos_from_product_json(product)
    if carousel_urls is not None:
        from_html = carousel_urls
    elif detail_html:
        from_html = foxter_photos_from_detail_html(detail_html)
    else:
        from_html = []
    # HTML do carousel costuma ter srcset 480/wm; JSON monta o mesmo padrão — prioridade ao HTML.
    photos = _merge_photo_urls(from_html, from_json)

    price = _parse_price(product.get("saleValue"))
    old_p = _parse_price(product.get("oldPrice"))
    place_parts = [product.get("place"), product.get("district"), product.get("city")]
    address = ", ".join(str(x) for x in place_parts if x) or None

    listing_tags = product.get("siteTags") or product.get("tag")

    code_raw = product.get("code")
    try:
        listing_code = int(code_raw) if code_raw is not None else None
    except (TypeError, ValueError):
        listing_code = None

    record: dict[str, Any] = {
        "source_url": source_url,
        "listing_code": listing_code,
        "title": (product.get("title") or product.get("h1") or "").strip() or None,
        "price": price,
        "currency": "BRL" if price is not None else None,
        "photos": photos,
        "thumbnail_url": photos[0] if photos else None,
        "description": product.get("description"),
        "address": address,
        "city": product.get("city"),
        "neighborhood": product.get("district"),
        "state": product.get("state"),
        "features": {
            "code": product.get("code"),
            "type": product.get("type"),
            "bedrooms": product.get("bedrooms"),
            "bathrooms": product.get("bathrooms"),
            "parking_spaces": product.get("parkingSpaces"),
            "area_private": product.get("areaPrivate"),
            "area_total": product.get("areaTotal"),
            "old_price": old_p,
            "listing_tags": listing_tags,
        },
    }
    return record


def fetch_foxter_cia_imovel(
    client: httpx.Client,
    url_or_code: str | int,
    *,
    check_photos: bool = True,
    max_photo_checks: int | None = None,
    photo_check_workers: int = 6,
) -> dict[str, Any]:
    if isinstance(url_or_code, int):
        url = f"https://{FOXT_CIA_HOST}/imovel/{url_or_code}"
    else:
        url = url_or_code.strip()
    r = client.get(url)
    r.raise_for_status()
    html = r.text
    product, carousel = parse_foxter_cia_detail_html(html)
    record = product_to_record(product, carousel_urls=carousel)
    finalize_foxter_photos(
        client,
        record,
        check_reachable=check_photos,
        max_photo_checks=max_photo_checks,
        photo_check_workers=photo_check_workers,
    )
    return record


def collect_codes_from_search_playwright(
    search_url: str,
    *,
    headless: bool,
    max_pages: int | None,
    settle_ms: int,
    show_progress: bool,
    on_page: Callable[[dict[str, Any]], None] | None = None,
) -> list[int]:
    if sync_playwright is None:
        raise RuntimeError(
            "Busca paginada requer Playwright: pip install playwright && playwright install chromium"
        )
    ordered: list[int] = []
    seen: set[int] = set()

    def add_batch(codes: list[int]) -> None:
        for c in codes:
            if c not in seen:
                seen.add(c)
                ordered.append(c)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            page.goto(
                set_page_query(search_url, 1),
                wait_until=_PLAYWRIGHT_GOTO_WAIT,
                timeout=90_000,
            )
            page.wait_for_timeout(settle_ms)
            nd = json.loads(page.locator("#__NEXT_DATA__").inner_text())
            pp = nd["props"]["pageProps"]
            total = int(pp["total"])
            results = pp.get("results") or []
            per_page = max(1, len(results))
            num_pages = math.ceil(total / per_page)
            if max_pages is not None:
                num_pages = min(num_pages, max_pages)

            page_iter = range(1, num_pages + 1)
            if show_progress and tqdm is not None:
                page_iter = tqdm(
                    page_iter,
                    total=num_pages,
                    desc="Listagem",
                    unit="pág",
                    file=sys.stderr,
                    bar_format="{desc} {n}/{total}|{bar}|{elapsed}",
                )
            for pg in page_iter:
                page.goto(
                    set_page_query(search_url, pg),
                    wait_until=_PLAYWRIGHT_GOTO_WAIT,
                    timeout=90_000,
                )
                page.wait_for_timeout(settle_ms)
                hrefs = page.eval_on_selector_all(
                    'a[href*="/imovel/"]', "els => els.map(e => e.href)"
                )
                batch: list[int] = []
                for h in hrefs:
                    m = re.search(r"/imovel/(\d+)", h)
                    if m:
                        batch.append(int(m.group(1)))
                uniq = list(dict.fromkeys(batch))
                add_batch(uniq)
                if on_page is not None:
                    on_page(
                        {
                            "phase": "pages",
                            "current": pg,
                            "total": num_pages,
                            "search_url": search_url,
                            "batch_unique": len(uniq),
                            "accumulated": len(ordered),
                            "total_listings": total,
                        }
                    )
                if show_progress and tqdm is None:
                    print(
                        f"[foxter] listagem {pg}/{num_pages} · +{len(uniq)} códigos "
                        f"· acumulado {len(ordered)} (total site {total})",
                        file=sys.stderr,
                        flush=True,
                    )
        finally:
            browser.close()

    return ordered


def _parse_search_listing_page_props(html: str) -> dict[str, Any] | None:
    """pageProps da listagem (tem `total`, `results`); None se não for listagem."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find("script", id="__NEXT_DATA__")
    if not el or not el.string:
        return None
    try:
        data = json.loads(el.string)
    except json.JSONDecodeError:
        return None
    pp = data.get("props", {}).get("pageProps")
    if not isinstance(pp, dict):
        return None
    if "total" not in pp:
        return None
    if "product" in pp and "results" not in pp:
        return None
    return pp


def extract_codes_from_search_html(html: str) -> list[int]:
    """Códigos únicos na ordem em que aparecem (links /imovel/{id})."""
    ordered: list[int] = []
    seen: set[int] = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select('a[href*="/imovel/"]'):
        h = (a.get("href") or "").strip()
        if not h:
            continue
        m = re.search(r"/imovel/(\d+)", h)
        if not m:
            continue
        c = int(m.group(1))
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def codes_from_results_list(results: list[Any]) -> list[int]:
    """Códigos a partir de `pageProps.results[].code` (listagem Next.js)."""
    ordered: list[int] = []
    seen: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        c = item.get("code")
        if c is None:
            continue
        try:
            ci = int(c)
        except (TypeError, ValueError):
            continue
        if ci not in seen:
            seen.add(ci)
            ordered.append(ci)
    return ordered


def extract_codes_from_listing_html(html: str) -> list[int]:
    """Preferir JSON `results`; senão links no HTML."""
    pp = _parse_search_listing_page_props(html)
    if pp:
        results = pp.get("results") or []
        if results:
            jc = codes_from_results_list(results)
            if jc:
                return jc
    return extract_codes_from_search_html(html)


def collect_codes_from_search_httpx(
    client: httpx.Client,
    search_url: str,
    *,
    max_pages: int | None,
    page_workers: int,
    show_progress: bool,
    on_page: Callable[[dict[str, Any]], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[int]:
    """
    Listagem via GET: cada página devolve HTML com __NEXT_DATA__ (como no Playwright).
    Páginas 2..N são pedidas em paralelo (page_workers).
    Lança ValueError se a primeira página não for uma listagem parseável.
    """
    r = client.get(set_page_query(search_url, 1))
    r.raise_for_status()
    html1 = r.text
    pp = _parse_search_listing_page_props(html1)
    if pp is None:
        raise ValueError("primeira página: sem __NEXT_DATA__ de listagem")
    total = int(pp["total"])
    results = pp.get("results") or []
    codes_p1 = extract_codes_from_listing_html(html1)
    if results:
        per_page = max(1, len(results))
    else:
        per_page = max(1, len(codes_p1))
    if total > 0 and not results and not codes_p1:
        raise ValueError("primeira página: total>0 mas sem results nem links (precisa Playwright?)")

    num_pages = math.ceil(total / per_page)
    if max_pages is not None:
        num_pages = min(num_pages, max_pages)

    ordered: list[int] = []
    seen: set[int] = set()

    def add_batch(codes: list[int]) -> None:
        for c in codes:
            if c not in seen:
                seen.add(c)
                ordered.append(c)

    add_batch(codes_p1)
    if on_page is not None:
        on_page(
            {
                "phase": "pages",
                "current": 1,
                "total": num_pages,
                "search_url": search_url,
                "batch_unique": len(codes_p1),
                "accumulated": len(ordered),
                "total_listings": total,
            }
        )
    if log and show_progress:
        log(
            f"[foxter] listagem HTTP pág.1/{num_pages} · +{len(codes_p1)} códigos "
            f"· acumulado {len(ordered)} (total site {total})"
        )

    if num_pages <= 1:
        return ordered

    def fetch_page_html(pg: int) -> tuple[int, str]:
        resp = client.get(set_page_query(search_url, pg))
        resp.raise_for_status()
        return pg, resp.text

    pages_rest = list(range(2, num_pages + 1))
    page_iter = pages_rest
    if show_progress and tqdm is not None:
        page_iter = tqdm(
            pages_rest,
            total=len(pages_rest),
            desc="Listagem HTTP",
            unit="pág",
            file=sys.stderr,
            bar_format="{desc} {n}/{total}|{bar}|{elapsed}",
        )

    if page_workers <= 1:
        for pg in page_iter:
            _, h = fetch_page_html(pg)
            uq = extract_codes_from_listing_html(h)
            add_batch(uq)
            if on_page is not None:
                on_page(
                    {
                        "phase": "pages",
                        "current": pg,
                        "total": num_pages,
                        "search_url": search_url,
                        "batch_unique": len(uq),
                        "accumulated": len(ordered),
                        "total_listings": total,
                    }
                )
            if log and show_progress and tqdm is None:
                log(
                    f"[foxter] listagem HTTP pág.{pg}/{num_pages} · +{len(uq)} códigos "
                    f"· acumulado {len(ordered)}"
                )
    else:
        npar = min(page_workers, len(pages_rest))
        with ThreadPoolExecutor(max_workers=npar) as ex:
            futs = {ex.submit(fetch_page_html, pg): pg for pg in pages_rest}
            by_pg: dict[int, str] = {}
            for fut in as_completed(futs):
                pg, h = fut.result()
                by_pg[pg] = h
        for pg in sorted(by_pg.keys()):
            h = by_pg[pg]
            uq = extract_codes_from_listing_html(h)
            add_batch(uq)
            if on_page is not None:
                on_page(
                    {
                        "phase": "pages",
                        "current": pg,
                        "total": num_pages,
                        "search_url": search_url,
                        "batch_unique": len(uq),
                        "accumulated": len(ordered),
                        "total_listings": total,
                    }
                )
            if log and show_progress:
                log(
                    f"[foxter] listagem HTTP pág.{pg}/{num_pages} · +{len(uq)} códigos "
                    f"· acumulado {len(ordered)}"
                )

    return ordered


def fetch_foxter_listing_generic(client: httpx.Client, url: str) -> dict[str, Any]:
    """Fallback HTML genérico (outros domínios Foxter)."""
    canonical = normalize_source_url(url)
    r = client.get(canonical)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find("meta", property="og:title") or soup.find("title")
    title = None
    if title_el:
        title = (
            title_el.get("content")
            if title_el.name == "meta"
            else title_el.get_text(strip=True)
        )

    photos: list[str] = []
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        photos.append(og["content"].strip())
    for img in soup.find_all("img", src=True):
        src = img["src"].strip()
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith("http") and src not in photos:
            photos.append(src)
        if len(photos) >= 40:
            break

    price = None
    ld = soup.find("script", type="application/ld+json")
    if ld and ld.string:
        try:
            data = json.loads(ld.string)
            if isinstance(data, dict) and "offers" in data:
                off = data["offers"]
                if isinstance(off, dict):
                    price = _parse_price(str(off.get("price", "")))
        except (json.JSONDecodeError, TypeError):
            pass
    if price is None:
        for node in soup.find_all(string=re.compile(r"R\$\s*[\d.]+")):
            price = _parse_price(node.parent.get_text() if node.parent else str(node))
            if price is not None:
                break

    return {
        "source_url": canonical,
        "title": title,
        "price": price,
        "currency": "BRL" if price is not None else None,
        "photos": photos,
        "thumbnail_url": photos[0] if photos else None,
        "description": None,
        "address": None,
        "city": None,
        "neighborhood": None,
        "state": None,
        "features": {},
    }


def fetch_one_url(
    client: httpx.Client,
    url: str,
    *,
    check_photos: bool = True,
    max_photo_checks: int | None = None,
    photo_check_workers: int = 6,
) -> dict[str, Any]:
    if is_foxter_cia_imovel_url(url):
        return fetch_foxter_cia_imovel(
            client,
            url,
            check_photos=check_photos,
            max_photo_checks=max_photo_checks,
            photo_check_workers=photo_check_workers,
        )
    return fetch_foxter_listing_generic(client, url)


def _httpx_limits_for_workers(workers: int, page_workers: int) -> httpx.Limits:
    n = max(32, int(workers) + int(page_workers) + 8)
    return httpx.Limits(
        max_connections=n,
        max_keepalive_connections=max(n // 2, 16),
    )


def load_url_list(config_path: Path) -> list[str]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                if "url" in item:
                    out.append(str(item["url"]))
                elif "search_url" in item:
                    out.append(str(item["search_url"]))
        return out
    raise ValueError(
        "O ficheiro de config deve ser uma lista de URLs ou de objetos com url/search_url."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa anúncios Foxter para SQLite (data/imoveis.db por defeito)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"JSON com URLs (se não passar --url/--search-url, usa {DEFAULT_CONFIG_PATH} se existir)",
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        default=[],
        help="URL de um anúncio ou busca Foxter Cia (pode repetir)",
    )
    parser.add_argument(
        "--search-url",
        action="append",
        dest="search_urls",
        default=[],
        help="URL de uma página de busca (importa todos os imóveis, todas as páginas)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        dest="db_path",
        help=f"Ficheiro SQLite (predefinido: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas mostra o que seria gravado, sem escrever ficheiro",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Pedidos HTTP paralelos ao obter detalhes (predefinido: 8)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Abre o Chromium com interface (útil para depurar a busca)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Limita o número de páginas de listagem (testes)",
    )
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=2000,
        help="Espera após cada navegação de listagem em ms (predefinido: 2000)",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Desativa barras de progresso (listagem playwright + detalhes HTTP)",
    )
    parser.add_argument(
        "--machine-progress",
        action="store_true",
        help="Emite uma linha JSON por evento em stdout (para a UI); logs humanos em stderr",
    )
    parser.add_argument(
        "--skip-photo-check",
        action="store_true",
        help="Não valida URLs de imagem (HEAD/GET); mais rápido, sem aviso de falha por URL",
    )
    parser.add_argument(
        "--listing-playwright",
        action="store_true",
        help="Força listagem com Playwright (uma página de cada vez); por defeito usa HTTP em paralelo",
    )
    parser.add_argument(
        "--page-workers",
        type=int,
        default=8,
        help="Paralelismo ao pedir páginas de listagem via HTTP (predefinido: 8)",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=25,
        metavar="N",
        help="COMMIT SQLite a cada N imóveis (predefinido: 25; use 1 para fsync por imóvel)",
    )
    parser.add_argument(
        "--verbose-db",
        action="store_true",
        help="Uma linha stderr por INSERT/UPDATE no SQLite",
    )
    parser.add_argument(
        "--max-photo-checks",
        type=int,
        default=None,
        metavar="N",
        help="Valida no máximo N fotos por imóvel; predefinido: todas",
    )
    parser.add_argument(
        "--photo-check-workers",
        type=int,
        default=6,
        help="Paralelismo na validação de fotos por imóvel (predefinido: 6)",
    )
    args = parser.parse_args()

    urls: list[str] = list(args.urls)
    urls.extend(args.search_urls)
    config_resolved: Path | None = args.config
    if config_resolved is not None:
        urls.extend(load_url_list(config_resolved))
    elif not urls:
        if DEFAULT_CONFIG_PATH.is_file():
            config_resolved = DEFAULT_CONFIG_PATH
            urls.extend(load_url_list(config_resolved))
        else:
            parser.error(
                f"Crie {DEFAULT_CONFIG_PATH} ou use --url, --search-url ou --config"
            )
    if not urls:
        parser.error("Lista de URLs vazia.")

    show_progress = not args.no_progress and not args.machine_progress
    machine = args.machine_progress

    def emit_machine(obj: dict[str, Any]) -> None:
        if machine:
            payload = {"importer": "foxter", **obj}
            print(json.dumps(payload, ensure_ascii=False), flush=True)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    emit_machine({"phase": "start", "urls": [u.strip() for u in urls if u.strip()]})

    db_path = args.db_path or DEFAULT_DB_PATH
    records: list[dict[str, Any]] = []
    stats_total = {"inserted": 0, "updated": 0, "price_changes": 0}
    n_imported = 0
    db_lock = threading.Lock()
    headless = not args.headed
    check_photos = not args.skip_photo_check
    commit_every = max(1, args.commit_every)
    max_photo_checks = args.max_photo_checks
    photo_check_workers = max(1, args.photo_check_workers)

    log(
        f"[foxter] {len(urls)} URL(s) · detalhe workers={args.workers} · "
        f"listagem HTTP page-workers={args.page_workers} · "
        f"fotos={'validar URL' if check_photos else 'sem validação'} · "
        f"commit a cada {commit_every} · db={db_path}"
    )
    if config_resolved is not None:
        log(f"[foxter] config: {config_resolved}")
    for u in urls:
        log(f"[foxter]   → {u.strip()}")

    conn = None
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=45.0,
            headers=DEFAULT_HTTP_HEADERS,
            limits=_httpx_limits_for_workers(args.workers, args.page_workers),
        ) as client:
            if not args.dry_run:
                conn = connect_db(db_path)
                init_schema(conn)

            pending_batch: list[dict[str, Any]] = []

            def flush_batch() -> None:
                if args.dry_run or conn is None or not pending_batch:
                    return
                with db_lock:
                    st = upsert_import_records(
                        conn,
                        pending_batch,
                        agency=AGENCY,
                        log_each_save=args.verbose_db,
                        commit_each=False,
                    )
                    for k in stats_total:
                        stats_total[k] += st[k]
                    conn.commit()
                pending_batch.clear()

            def save_record(rec: dict[str, Any]) -> None:
                nonlocal n_imported
                if args.dry_run:
                    records.append(rec)
                    n_imported += 1
                    return
                assert conn is not None
                pending_batch.append(rec)
                n_imported += 1
                if len(pending_batch) >= commit_every:
                    flush_batch()

            for raw_u in urls:
                u = raw_u.strip()
                if not u:
                    continue
                if is_foxter_cia_search_url(u):

                    def on_page(ev: dict[str, Any]) -> None:
                        emit_machine(ev)

                    if args.listing_playwright:
                        log(f"[foxter] listagem (Playwright): {u}")
                        codes = collect_codes_from_search_playwright(
                            u,
                            headless=headless,
                            max_pages=args.max_pages,
                            settle_ms=args.settle_ms,
                            show_progress=show_progress,
                            on_page=on_page if machine else None,
                        )
                    else:
                        log(f"[foxter] listagem (HTTP, paralela por página): {u}")
                        try:
                            codes = collect_codes_from_search_httpx(
                                client,
                                u,
                                max_pages=args.max_pages,
                                page_workers=args.page_workers,
                                show_progress=show_progress,
                                on_page=on_page if machine else None,
                                log=log,
                            )
                        except (ValueError, httpx.HTTPError) as ex:
                            log(
                                f"[foxter] listagem HTTP falhou ({ex}); "
                                "a usar Playwright…"
                            )
                            codes = collect_codes_from_search_playwright(
                                u,
                                headless=headless,
                                max_pages=args.max_pages,
                                settle_ms=args.settle_ms,
                                show_progress=show_progress,
                                on_page=on_page if machine else None,
                            )
                    log(f"[foxter] códigos únicos recolhidos: {len(codes)}")
                    emit_machine({"phase": "codes_ready", "count": len(codes)})

                    def load_code(code: int) -> dict[str, Any]:
                        return fetch_foxter_cia_imovel(
                            client,
                            code,
                            check_photos=check_photos,
                            max_photo_checks=max_photo_checks,
                            photo_check_workers=photo_check_workers,
                        )

                    detail_pbar = None
                    if show_progress and tqdm is not None:
                        detail_pbar = tqdm(
                            total=len(codes),
                            desc="Detalhes",
                            unit="imo",
                            file=sys.stderr,
                            bar_format="{desc} {n}/{total}|{bar}|{elapsed}",
                        )

                    if args.workers <= 1:
                        try:
                            for i, code in enumerate(codes, 1):
                                emit_machine(
                                    {
                                        "phase": "detail",
                                        "current": i,
                                        "total": len(codes),
                                        "code": code,
                                    }
                                )
                                save_record(load_code(code))
                                if detail_pbar is not None:
                                    detail_pbar.update(1)
                                elif show_progress and tqdm is None:
                                    log(
                                        f"[foxter] detalhe {i}/{len(codes)} "
                                        f"código {code}"
                                    )
                        finally:
                            if detail_pbar is not None:
                                detail_pbar.close()
                    else:
                        try:
                            with ThreadPoolExecutor(
                                max_workers=args.workers
                            ) as ex:
                                futs = {
                                    ex.submit(load_code, c): c for c in codes
                                }
                                done = 0
                                for fut in as_completed(futs):
                                    code = futs[fut]
                                    try:
                                        rec = fut.result()
                                    except Exception as exn:
                                        log(
                                            f"[foxter] ERRO código {code}: {exn}"
                                        )
                                        raise
                                    done += 1
                                    emit_machine(
                                        {
                                            "phase": "detail",
                                            "current": done,
                                            "total": len(codes),
                                            "code": code,
                                        }
                                    )
                                    save_record(rec)
                                    if detail_pbar is not None:
                                        detail_pbar.update(1)
                                    elif show_progress and tqdm is None and (
                                        done % 25 == 0 or done == len(codes)
                                    ):
                                        log(
                                            f"[foxter] detalhes HTTP {done}/"
                                            f"{len(codes)}"
                                        )
                        finally:
                            if detail_pbar is not None:
                                detail_pbar.close()
                else:
                    log(f"[foxter] URL única: {u}")
                    save_record(
                        fetch_one_url(
                            client,
                            u,
                            check_photos=check_photos,
                            max_photo_checks=max_photo_checks,
                            photo_check_workers=photo_check_workers,
                        )
                    )

            if not args.dry_run:
                flush_batch()

        if args.dry_run:
            log(f"[foxter] dry-run: {len(records)} registos (nada gravado)")
            print(
                json.dumps(records, ensure_ascii=False, indent=2),
                file=sys.stderr if machine else sys.stdout,
            )
            emit_machine(
                {
                    "phase": "done",
                    "dry_run": True,
                    "records": len(records),
                }
            )
            return

        log(
            f"[foxter] resumo SQLite ({db_path}): +{stats_total['inserted']} "
            f"inseridos · {stats_total['updated']} atualizados · "
            f"{stats_total['price_changes']} mudança(s) de preço "
            f"· {n_imported} imóvel(is) processado(s)"
        )
        emit_machine(
            {
                "phase": "done",
                "db": str(db_path),
                "inserted": stats_total["inserted"],
                "updated": stats_total["updated"],
                "price_changes": stats_total["price_changes"],
                "records": n_imported,
            }
        )
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
