"""
Importador Guarida — API REST de busca (listagem paginada).

Lê URLs de busca em guarida.com.br (``/busca/...``), repassa a query string à API
``busca-api`` e grava cada imóvel no SQLite com ``agency=guarida``.

Uso (na raiz do projeto):

  python scripts/importers/guarida.py

  Lê sempre ``config/urls.json`` (sem argumentos de linha de comandos).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from sqlite3 import Connection
from typing import Any
from urllib.parse import parse_qsl, urlparse

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
except ImportError:
    print(
        "Instale dependências: pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise

AGENCY = "guarida"
GUARIDA_PUBLIC_BASE = "https://guarida.com.br"
GUARIDA_API_URL = (
    "https://busca-api-oprkvhcira-uc.a.run.app/api/busca/v1/imoveis/comprar"
)

URLS_CONFIG_PATH = _ROOT / "config" / "urls.json"

DEFAULT_HTTP_HEADERS = {
    "User-Agent": "MobihunterImporter/1.0 (+local)",
    "Accept": "application/json",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


def _warn(msg: str) -> None:
    print(f"[guarida] WARN {msg}", file=sys.stderr, flush=True)


def _log(msg: str) -> None:
    print(f"[guarida] {msg}", file=sys.stderr, flush=True)


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


def is_guarida_host(url: str) -> bool:
    try:
        host = (urlparse(url.strip()).netloc or "").lower()
    except ValueError:
        return False
    return host == "guarida.com.br" or host.endswith(".guarida.com.br")


def is_guarida_search_url(url: str) -> bool:
    if not is_guarida_host(url):
        return False
    path = (urlparse(url).path or "").lower()
    return "/busca/" in path


def api_query_pairs_from_browser_url(browser_url: str, pagina: int) -> list[tuple[str, str]]:
    """Query string da URL do site + ``pagina`` (sempre sobrescreve ``pagina``)."""
    p = urlparse(browser_url.strip())
    pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() != "pagina"]
    pairs.append(("pagina", str(pagina)))
    return pairs


def guarida_item_to_record(item: dict[str, Any]) -> dict[str, Any]:
    raw_path = (item.get("url") or "").strip()
    if not raw_path.startswith("/"):
        raw_path = "/" + raw_path
    source_url = normalize_source_url(GUARIDA_PUBLIC_BASE.rstrip("/") + raw_path)

    codigo = item.get("codigo")
    listing_code = int(codigo) if codigo is not None else None

    valores = item.get("valores") if isinstance(item.get("valores"), dict) else {}
    price = _parse_price_br(valores.get("valor") if isinstance(valores, dict) else None)

    fotos_raw = item.get("fotos") if isinstance(item.get("fotos"), list) else []
    sorted_fotos = sorted(
        [f for f in fotos_raw if isinstance(f, dict)],
        key=lambda x: int(x.get("ordem") or 0),
    )
    photos: list[str] = []
    for f in sorted_fotos:
        u = f.get("url")
        if isinstance(u, str) and u.strip():
            photos.append(u.strip())

    neighborhood, city, state = _parse_endereco(item.get("endereco"))

    tipo = item.get("tipo") if isinstance(item.get("tipo"), dict) else {}
    props_flat: dict[str, Any] = {}
    for pr in item.get("propriedades") or []:
        if isinstance(pr, dict) and pr.get("slug"):
            props_flat[str(pr["slug"])] = pr.get("valor")

    features: dict[str, Any] = {
        "negocio": item.get("negocio"),
        "finalidade": item.get("finalidade"),
        "tipo_slug": tipo.get("slug"),
        "tipo_nome": tipo.get("nome"),
        "propriedades": props_flat,
    }
    lat, lon = item.get("latitude"), item.get("longitude")
    if lat is not None:
        features["latitude"] = str(lat).strip()
    if lon is not None:
        features["longitude"] = str(lon).strip()
    if isinstance(valores, dict):
        for k in ("condominio", "iptu", "seguroFogo", "seguroFianca"):
            if valores.get(k) is not None:
                features[k] = valores.get(k)

    rec: dict[str, Any] = {
        "source_url": source_url,
        "listing_code": listing_code,
        "title": item.get("titulo"),
        "description": None,
        "price": price,
        "currency": "BRL" if price is not None else None,
        "thumbnail_url": photos[0] if photos else None,
        "photos": photos,
        "address": item.get("logradouro"),
        "city": city,
        "neighborhood": neighborhood,
        "state": state,
        "features": features,
    }
    return rec


def _parse_endereco(endereco: str | None) -> tuple[str | None, str | None, str | None]:
    if not endereco or not str(endereco).strip():
        return None, None, None
    s = str(endereco).strip()
    neighborhood: str | None
    tail: str
    if ", " in s:
        parts = s.rsplit(", ", 1)
        neighborhood = parts[0].strip()
        tail = parts[1].strip()
    else:
        neighborhood = None
        tail = s
    if " - " in tail:
        city, _, st = tail.partition(" - ")
        return neighborhood, city.strip(), st.strip()
    return neighborhood, tail, None


def fetch_search_page(
    client: httpx.Client,
    browser_url: str,
    pagina: int,
) -> dict[str, Any]:
    pairs = api_query_pairs_from_browser_url(browser_url, pagina)
    r = client.get(GUARIDA_API_URL, params=pairs)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("Resposta da API: JSON não é um objeto")
    return data


def import_guarida_search_url(
    client: httpx.Client,
    browser_url: str,
    *,
    conn: Connection,
    verbose_db: bool,
    commit_every: int,
) -> dict[str, int]:
    stats_total = {"inserted": 0, "updated": 0, "price_changes": 0}
    if not is_guarida_search_url(browser_url):
        _warn(f"ignorada (não é busca Guarida): {browser_url}")
        return stats_total

    pagina = 1
    processed = 0
    pending: list[dict[str, Any]] = []
    commit_every = max(1, commit_every)

    while True:
        data = fetch_search_page(client, browser_url, pagina)
        imoveis = data.get("imoveis") if isinstance(data.get("imoveis"), list) else []
        pag = data.get("paginacao") if isinstance(data.get("paginacao"), dict) else {}

        total_api = pag.get("total")
        paginas = pag.get("paginas")
        tem_proxima = pag.get("temProxima")
        desc = pag.get("descricao") or ""

        total_s = str(total_api) if total_api is not None else "?"
        paginas_s = str(paginas) if paginas is not None else "?"
        _log(
            f"listagem · importando página {pagina}/{paginas_s} · "
            f"{len(imoveis)} imóveis nesta resposta · total API {total_s}"
            + (f" · {desc}" if desc else "")
        )
        for item in imoveis:
            if not isinstance(item, dict):
                continue
            try:
                rec = guarida_item_to_record(item)
            except Exception as ex:
                _warn(f"item inválido codigo={item.get('codigo')!r}: {ex}")
                continue
            processed += 1
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

        _log(f"página {pagina} concluída · total acumulado {processed} imóveis processados")

        if not imoveis:
            _log("lista vazia — fim.")
            break

        if tem_proxima is False:
            _log("temProxima=false — fim.")
            break
        if paginas is not None:
            try:
                if pagina >= int(paginas):
                    _log("última página (pagina >= paginas) — fim.")
                    break
            except (TypeError, ValueError):
                pass

        pagina += 1

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
        pending.clear()

    return stats_total


def main() -> None:
    """Sem CLI: lê sempre ``config/urls.json``; restantes opções são predefinidas no código."""
    if not URLS_CONFIG_PATH.is_file():
        print(
            f"Crie {URLS_CONFIG_PATH} (veja config/urls.example.json).",
            file=sys.stderr,
        )
        sys.exit(1)
    urls = load_url_list(URLS_CONFIG_PATH)
    if not urls:
        print(
            f"{URLS_CONFIG_PATH} não contém URLs.",
            file=sys.stderr,
        )
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
        with httpx.Client(
            follow_redirects=True,
            timeout=60.0,
            headers=DEFAULT_HTTP_HEADERS,
        ) as client:
            for u in urls:
                u = u.strip()
                if not u:
                    continue
                if not is_guarida_search_url(u):
                    _warn(f"ignorada (só URLs de busca /busca/...): {u}")
                    continue
                q = urlparse(u).query
                if not q.strip():
                    _warn(
                        "URL sem query string — a API pode devolver listagem genérica; "
                        "prefira copiar a URL completa do browser com filtros."
                    )
                _log(f"→ {u}")
                st = import_guarida_search_url(
                    client,
                    u,
                    conn=conn,
                    verbose_db=verbose_db,
                    commit_every=commit_every,
                )
                for k in stats_all:
                    stats_all[k] += st[k]
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
