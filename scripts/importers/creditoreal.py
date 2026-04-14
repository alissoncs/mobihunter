"""
Importador Crédito Real — listagem paginada + detalhe por imóvel.

Lê URLs de busca em ``www.creditoreal.com.br`` (``/vendas/...``), percorre a
paginação começando em ``page=1`` e importa cada detalhe ``/vendas/imovel/...``
para SQLite com ``agency=creditoreal``.

Uso (na raiz do projeto):

  python scripts/importers/creditoreal.py

  Lê sempre ``config/urls.json`` (sem argumentos de linha de comandos).
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from sqlite3 import Connection
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

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
    from bs4 import BeautifulSoup
except ImportError:
    print("Instale dependências: pip install -r requirements.txt", file=sys.stderr)
    raise

try:
    from playwright.sync_api import Page, sync_playwright
except ImportError:
    sync_playwright = None  # type: ignore[assignment]
    Page = Any  # type: ignore[misc, assignment]

AGENCY = "creditoreal"
CREDITO_REAL_BASE = "https://www.creditoreal.com.br"
URLS_CONFIG_PATH = _ROOT / "config" / "urls.json"


def _warn(msg: str) -> None:
    print(f"[creditoreal] WARN {msg}", file=sys.stderr, flush=True)


def _log(msg: str) -> None:
    print(f"[creditoreal] {msg}", file=sys.stderr, flush=True)


def _parse_price_br(text: str | None) -> float | None:
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
        "O ficheiro de URLs deve ser uma lista de URLs ou objetos com url/search_url."
    )


def is_creditoreal_host(url: str) -> bool:
    try:
        host = (urlparse(url.strip()).netloc or "").lower()
    except ValueError:
        return False
    return host == "www.creditoreal.com.br" or host.endswith(".creditoreal.com.br")


def is_creditoreal_search_url(url: str) -> bool:
    if not is_creditoreal_host(url):
        return False
    path = (urlparse(url).path or "").lower()
    return "/vendas/" in path and "/vendas/imovel/" not in path


def _set_page_query(url: str, page_number: int) -> str:
    p = urlparse(url.strip())
    q = parse_qsl(p.query, keep_blank_values=True)
    q = [(k, v) for (k, v) in q if k.lower() not in ("page", "pagina")]
    q.append(("page", str(page_number)))
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), ""))


def _wait_checkpoint_if_needed(page: Page, timeout_s: float = 20.0) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        title = (page.title() or "").lower().strip()
        if "security checkpoint" not in title:
            return
        time.sleep(0.5)


def _extract_detail_links(page: Page) -> list[str]:
    hrefs = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(el => el.getAttribute('href')).filter(Boolean)",
    )
    out: list[str] = []
    seen: set[str] = set()
    for raw in hrefs:
        h = str(raw or "").strip()
        if "/vendas/imovel/" not in h:
            continue
        abs_u = normalize_source_url(urljoin(CREDITO_REAL_BASE, h))
        if abs_u not in seen:
            seen.add(abs_u)
            out.append(abs_u)
    return out


def _wait_listing_links(page: Page, timeout_ms: int = 15000) -> None:
    """Espera cards de imóvel renderizarem no DOM (hidratação Next.js pode atrasar)."""
    try:
        page.wait_for_selector('a[href*="/vendas/imovel/"]', timeout=timeout_ms)
    except Exception:
        # fallback: dá mais tempo para requests assíncronas terminarem
        page.wait_for_timeout(5000)


def collect_creditoreal_detail_urls(page: Page, search_url: str) -> list[str]:
    """
    Percorre ``page=1,2,...`` até não haver mais resultados.

    **Não** usamos o maior ``page=`` encontrado no HTML: a listagem costuma
    expor só alguns links de paginação (ex.: 1 e 2), o que faria ``max==2``
    e encerrava a importação cedo.
    """
    all_urls: list[str] = []
    all_set: set[str] = set()
    page_no = 1
    hard_limit = 500

    while page_no <= hard_limit:
        page_url = _set_page_query(search_url, page_no)

        links: list[str] = []
        # Retry leve porque esta listagem às vezes demora para hidratar no 1º load.
        for attempt in range(1, 4):
            page.goto(page_url, wait_until="load", timeout=60000)
            _wait_checkpoint_if_needed(page)
            _wait_listing_links(page, timeout_ms=12000)
            links = _extract_detail_links(page)
            if links:
                break
            _warn(
                f"página {page_no}: tentativa {attempt}/3 sem links de detalhe; a recarregar..."
            )
            page.wait_for_timeout(2500)

        new_urls = [u for u in links if u not in all_set]

        _log(
            f"listagem · importando página {page_no} · "
            f"{len(links)} imóveis na página · {len(new_urls)} novo(s)"
        )

        for u in new_urls:
            all_set.add(u)
            all_urls.append(u)

        if not links:
            _log("listagem sem links de imóvel — fim da paginação.")
            break
        if page_no > 1 and not new_urls:
            _log("página repetida sem novos imóveis — fim da paginação.")
            break

        page_no += 1

    if page_no > hard_limit:
        _warn(f"limite de páginas ({hard_limit}) atingido — interrompendo por segurança.")

    return all_urls


def _pick_meta(soup: Any, prop: str) -> str | None:
    m = soup.select_one(f'meta[property="{prop}"]')
    if m and m.get("content"):
        v = str(m.get("content")).strip()
        if v:
            return v
    return None


def _extract_jsonld_objects(soup: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for el in soup.select('script[type="application/ld+json"]'):
        raw = (el.string or el.get_text() or "").strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    out.append(item)
    return out


def _extract_listing_code(detail_url: str, soup: Any, html: str) -> int | None:
    joined = " ".join([
        detail_url,
        _pick_meta(soup, "og:title") or "",
        _pick_meta(soup, "og:url") or "",
        html[:20000],
    ])
    m = re.search(r"cod-([a-z]*)(\d+)", joined, flags=re.I)
    if m:
        try:
            return int(m.group(2))
        except ValueError:
            pass
    m2 = re.search(r"\b(\d{4,8})\b", joined)
    if m2:
        try:
            return int(m2.group(1))
        except ValueError:
            pass
    return None


def _extract_location_from_jsonld(objs: list[dict[str, Any]]) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
    address = None
    city = None
    state = None
    features: dict[str, Any] = {}
    for obj in objs:
        addr = obj.get("address")
        if isinstance(addr, dict):
            address = address or (addr.get("streetAddress") and str(addr.get("streetAddress")).strip())
            city = city or (addr.get("addressLocality") and str(addr.get("addressLocality")).strip())
            state = state or (addr.get("addressRegion") and str(addr.get("addressRegion")).strip())
        geo = obj.get("geo")
        if isinstance(geo, dict):
            lat = geo.get("latitude")
            lon = geo.get("longitude")
            if lat is not None:
                features["latitude"] = str(lat)
            if lon is not None:
                features["longitude"] = str(lon)
    return address, city, state, features


def _extract_images_from_jsonld(objs: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for obj in objs:
        imgs = obj.get("image")
        candidates: list[str] = []
        if isinstance(imgs, str):
            candidates = [imgs]
        elif isinstance(imgs, list):
            for it in imgs:
                if isinstance(it, str):
                    candidates.append(it)
                elif isinstance(it, dict) and it.get("url"):
                    candidates.append(str(it.get("url")))
        elif isinstance(imgs, dict) and imgs.get("url"):
            candidates = [str(imgs.get("url"))]
        for c in candidates:
            u = normalize_source_url(urljoin(CREDITO_REAL_BASE, c))
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out


def _extract_photos_fallback(soup: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    for prop in ("og:image", "twitter:image"):
        u = _pick_meta(soup, prop)
        if u:
            n = normalize_source_url(urljoin(CREDITO_REAL_BASE, u))
            if n not in seen:
                seen.add(n)
                out.append(n)

    for img in soup.select("img[src]"):
        src = str(img.get("src") or "").strip()
        if not src:
            continue
        if any(x in src.lower() for x in ["logo", "icon", "sprite", "favicon"]):
            continue
        n = normalize_source_url(urljoin(CREDITO_REAL_BASE, src))
        if n not in seen:
            seen.add(n)
            out.append(n)
        if len(out) >= 40:
            break
    return out


def creditoreal_detail_to_record(page: Page, detail_url: str) -> dict[str, Any]:
    page.goto(detail_url, wait_until="load", timeout=60000)
    _wait_checkpoint_if_needed(page)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    title = _pick_meta(soup, "og:title")
    if not title:
        h1 = soup.select_one("h1")
        if h1:
            title = h1.get_text(" ", strip=True)
    if not title:
        title_el = soup.select_one("title")
        title = title_el.get_text(" ", strip=True) if title_el else None

    desc = _pick_meta(soup, "og:description") or _pick_meta(soup, "description")
    canonical = _pick_meta(soup, "og:url") or detail_url
    source_url = normalize_source_url(urljoin(CREDITO_REAL_BASE, canonical))

    objs = _extract_jsonld_objects(soup)
    jsonld_images = _extract_images_from_jsonld(objs)
    photos = jsonld_images or _extract_photos_fallback(soup)

    price = None
    for obj in objs:
        offer = obj.get("offers")
        if isinstance(offer, dict):
            p = offer.get("price")
            if p is not None:
                try:
                    price = float(p)
                    break
                except (TypeError, ValueError):
                    pass
    if price is None:
        price = _parse_price_br((soup.get_text(" ", strip=True)[:8000]))

    address, city, state, geo_features = _extract_location_from_jsonld(objs)
    neighborhood = None
    if title:
        m = re.search(r" em ([^-|]+) - ([^|]+)", title, flags=re.I)
        if m:
            neighborhood = m.group(1).strip()
            city = city or m.group(2).strip()

    listing_code = _extract_listing_code(detail_url, soup, html)
    features: dict[str, Any] = {"source": "creditoreal", **geo_features}

    rec: dict[str, Any] = {
        "source_url": source_url,
        "listing_code": listing_code,
        "title": title,
        "description": desc,
        "price": price,
        "currency": "BRL" if price is not None else None,
        "thumbnail_url": photos[0] if photos else None,
        "photos": photos,
        "address": address,
        "city": city,
        "neighborhood": neighborhood,
        "state": state,
        "features": features,
    }
    return rec


def import_creditoreal_search_url(
    page: Page,
    search_url: str,
    *,
    conn: Connection,
    verbose_db: bool,
    commit_every: int,
) -> dict[str, int]:
    stats_total = {"inserted": 0, "updated": 0, "price_changes": 0}
    if not is_creditoreal_search_url(search_url):
        _warn(f"ignorada (não é busca Crédito Real): {search_url}")
        return stats_total

    detail_urls = collect_creditoreal_detail_urls(page, search_url)
    _log(f"detalhes a importar: {len(detail_urls)}")

    pending: list[dict[str, Any]] = []
    commit_every = max(1, commit_every)

    for idx, durl in enumerate(detail_urls, 1):
        _log(f"detalhe {idx}/{len(detail_urls)} · {durl}")
        try:
            rec = creditoreal_detail_to_record(page, durl)
        except Exception as ex:
            _warn(f"falha ao ler detalhe: {durl} · {ex}")
            continue

        pending.append(rec)
        if len(pending) >= commit_every:
            st = upsert_import_records(
                conn,
                pending,
                agency=AGENCY,
                log_each_save=verbose_db,
                commit_each=False,
            )
            conn.commit()
            stats_total["inserted"] += st["inserted"]
            stats_total["updated"] += st["updated"]
            stats_total["price_changes"] += st["price_changes"]
            pending.clear()

    if pending:
        st = upsert_import_records(
            conn,
            pending,
            agency=AGENCY,
            log_each_save=verbose_db,
            commit_each=False,
        )
        conn.commit()
        stats_total["inserted"] += st["inserted"]
        stats_total["updated"] += st["updated"]
        stats_total["price_changes"] += st["price_changes"]

    return stats_total


def main() -> None:
    """Sem CLI: lê sempre ``config/urls.json``; restantes opções são predefinidas no código."""
    if sync_playwright is None:
        print(
            "Playwright não instalado. Execute: pip install -r requirements.txt && playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(1)

    if not URLS_CONFIG_PATH.is_file():
        print(
            f"Crie {URLS_CONFIG_PATH} (veja config/urls.example.json).",
            file=sys.stderr,
        )
        sys.exit(1)
    urls = load_url_list(URLS_CONFIG_PATH)
    if not urls:
        print(f"{URLS_CONFIG_PATH} não contém URLs.", file=sys.stderr)
        sys.exit(1)

    db_path = DEFAULT_DB_PATH
    commit_every = max(1, 25)
    verbose_db = False

    _log(f"{len(urls)} URL(s) · commit a cada {commit_every} · db={db_path}")
    _log(f"config: {URLS_CONFIG_PATH}")

    conn = connect_db(db_path)
    init_schema(conn)
    stats_all = {"inserted": 0, "updated": 0, "price_changes": 0}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            for u in urls:
                u = u.strip()
                if not u:
                    continue
                if not is_creditoreal_search_url(u):
                    _warn(f"ignorada (só URLs de busca /vendas/...): {u}")
                    continue
                _log(f"→ {u}")
                st = import_creditoreal_search_url(
                    page,
                    u,
                    conn=conn,
                    verbose_db=verbose_db,
                    commit_every=commit_every,
                )
                for k in stats_all:
                    stats_all[k] += st[k]
            browser.close()
    finally:
        conn.close()

    _log(
        "feito · "
        f"inseridos={stats_all['inserted']} · atualizados={stats_all['updated']} · "
        f"alterações de preço={stats_all['price_changes']}"
    )


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(
            "Este script não aceita argumentos — use apenas config/urls.json.",
            file=sys.stderr,
        )
        sys.exit(2)
    main()
