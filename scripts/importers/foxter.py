"""
Importador Foxter (Foxter Cia Imobiliária e outros domínios Foxter).

- URL de **imóvel** (página de detalhe): obtém JSON em __NEXT_DATA__ (preço, fotos, texto).
- URL de **busca** (listagem filtrada em foxterciaimobiliaria.com.br/imoveis/...): usa Playwright
  para abrir cada ?page=N (o HTML estático não reflete a paginação), recolhe códigos e importa
  cada detalhe via HTTP.

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
DEFAULT_HTTP_HEADERS = {
    "User-Agent": "MobihunterImporter/1.0 (+local)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


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


def parse_product_from_html(html: str) -> dict[str, Any]:
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
    return prod


def product_to_record(product: dict[str, Any]) -> dict[str, Any]:
    canonical = product.get("canonical")
    if not canonical and product.get("code") is not None:
        canonical = f"https://{FOXT_CIA_HOST}/imovel/{product['code']}"
    if not canonical:
        raise ValueError("Product sem canonical nem code.")
    source_url = normalize_source_url(canonical)

    photos: list[str] = []
    images = product.get("images") or {}
    base = images.get("baseUrl") or ""
    for item in images.get("data") or []:
        etag = item.get("etag")
        if etag:
            photos.append(base + etag)

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
    client: httpx.Client, url_or_code: str | int
) -> dict[str, Any]:
    if isinstance(url_or_code, int):
        url = f"https://{FOXT_CIA_HOST}/imovel/{url_or_code}"
    else:
        url = url_or_code.strip()
    r = client.get(url)
    r.raise_for_status()
    product = parse_product_from_html(r.text)
    return product_to_record(product)


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
        page = browser.new_page()
        page.goto(set_page_query(search_url, 1), wait_until="networkidle", timeout=90_000)
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
                desc="Páginas",
                unit="pág",
                file=sys.stderr,
                bar_format="{desc}: {n}/{total} páginas |{bar}| [{elapsed}<{remaining}]",
            )
        for pg in page_iter:
            page.goto(set_page_query(search_url, pg), wait_until="networkidle", timeout=90_000)
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
                    f"  Busca: página {pg}/{num_pages} — {len(uniq)} anúncios nesta página "
                    f"(acumulado {len(ordered)}/{total})",
                    flush=True,
                )
        browser.close()

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
) -> dict[str, Any]:
    if is_foxter_cia_imovel_url(url):
        return fetch_foxter_cia_imovel(client, url)
    return fetch_foxter_listing_generic(client, url)


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
        help="Desativa a barra de progresso das páginas de listagem",
    )
    parser.add_argument(
        "--machine-progress",
        action="store_true",
        help="Emite uma linha JSON por evento em stdout (para a UI); logs humanos em stderr",
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

    show_page_progress = not args.no_progress and not args.machine_progress
    machine = args.machine_progress

    def emit_machine(obj: dict[str, Any]) -> None:
        if machine:
            payload = {"importer": "foxter", **obj}
            print(json.dumps(payload, ensure_ascii=False), flush=True)

    _out = sys.stderr if machine else sys.stdout
    print("Origem das URLs:", flush=True, file=_out)
    if config_resolved is not None:
        print(f"  Ficheiro: {config_resolved}", flush=True, file=_out)
    elif args.urls or args.search_urls:
        print("  Linha de comandos (--url / --search-url)", flush=True, file=_out)
    print(f"  Total: {len(urls)}", flush=True, file=_out)
    for i, u in enumerate(urls, 1):
        print(f"  [{i}] {u.strip()}", flush=True, file=_out)
    print(flush=True, file=_out)

    emit_machine({"phase": "start", "urls": [u.strip() for u in urls if u.strip()]})

    db_path = args.db_path or DEFAULT_DB_PATH
    records: list[dict[str, Any]] = []
    headless = not args.headed

    with httpx.Client(
        follow_redirects=True, timeout=45.0, headers=DEFAULT_HTTP_HEADERS
    ) as client:

        for raw_u in urls:
            u = raw_u.strip()
            if not u:
                continue
            if is_foxter_cia_search_url(u):
                print(
                    f"Busca (todas as páginas): {u}",
                    flush=True,
                    file=sys.stderr if machine else sys.stdout,
                )

                def on_page(ev: dict[str, Any]) -> None:
                    emit_machine(ev)

                codes = collect_codes_from_search_playwright(
                    u,
                    headless=headless,
                    max_pages=args.max_pages,
                    settle_ms=args.settle_ms,
                    show_progress=show_page_progress,
                    on_page=on_page if machine else None,
                )
                print(
                    f"  Total de códigos únicos: {len(codes)}",
                    flush=True,
                    file=sys.stderr if machine else sys.stdout,
                )
                emit_machine({"phase": "codes_ready", "count": len(codes)})

                def load_code(code: int) -> dict[str, Any]:
                    return fetch_foxter_cia_imovel(client, code)

                if args.workers <= 1:
                    for i, code in enumerate(codes, 1):
                        print(
                            f"  Detalhe {i}/{len(codes)}: {code}",
                            flush=True,
                            file=sys.stderr if machine else sys.stdout,
                        )
                        emit_machine(
                            {
                                "phase": "detail",
                                "current": i,
                                "total": len(codes),
                                "code": code,
                            }
                        )
                        records.append(load_code(code))
                else:
                    by_code: dict[int, dict[str, Any]] = {}
                    with ThreadPoolExecutor(max_workers=args.workers) as ex:
                        futs = {ex.submit(load_code, c): c for c in codes}
                        done = 0
                        for fut in as_completed(futs):
                            code = futs[fut]
                            by_code[code] = fut.result()
                            done += 1
                            emit_machine(
                                {
                                    "phase": "detail",
                                    "current": done,
                                    "total": len(codes),
                                    "code": code,
                                }
                            )
                            if done % 20 == 0 or done == len(codes):
                                print(
                                    f"  Detalhes HTTP: {done}/{len(codes)}",
                                    flush=True,
                                    file=sys.stderr if machine else sys.stdout,
                                )
                    records.extend(by_code[c] for c in codes if c in by_code)
            else:
                print(
                    f"A importar: {u}",
                    flush=True,
                    file=sys.stderr if machine else sys.stdout,
                )
                records.append(fetch_one_url(client, u))

    if args.dry_run:
        print(json.dumps(records, ensure_ascii=False, indent=2), file=sys.stderr if machine else sys.stdout)
        emit_machine(
            {
                "phase": "done",
                "dry_run": True,
                "records": len(records),
            }
        )
        return

    conn = connect_db(db_path)
    try:
        init_schema(conn)
        st = upsert_import_records(conn, records, agency=AGENCY)
        print(
            f"Inseridos: {st['inserted']}, atualizados: {st['updated']}, "
            f"preços alterados: {st['price_changes']}",
            flush=True,
            file=sys.stderr if machine else sys.stdout,
        )
        emit_machine(
            {
                "phase": "done",
                "db": str(db_path),
                "inserted": st["inserted"],
                "updated": st["updated"],
                "price_changes": st["price_changes"],
                "records": len(records),
            }
        )
    finally:
        conn.close()
    print(
        f"Base de dados: {db_path}",
        flush=True,
        file=sys.stderr if machine else sys.stdout,
    )


if __name__ == "__main__":
    main()
