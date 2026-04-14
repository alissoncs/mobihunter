# Mobihunter

Importação de anúncios para SQLite (`data/imoveis.db`) e listagem simples na web (`mobihunter/web`).

Documentação: [docs/VISAO.md](docs/VISAO.md) · [sincronização e desativação](docs/SPEC_SINCRONIZACAO_E_DESATIVACAO.md).

## Instalação

Na raiz do repositório:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Importar (Foxter, Guarida e Crédito Real)

1. Copie `config/urls.example.json` para `config/urls.json` e edite as URLs.
2. Cada importador lê **apenas** esse ficheiro (sem argumentos de linha de comandos nos scripts Python).
3. O mesmo JSON pode misturar URLs de várias imobiliárias; **cada script ignora o que não for da sua origem** (Foxter: domínios Foxter; Guarida: `guarida.com.br` com `/busca/...`; Crédito Real: `www.creditoreal.com.br` com `/vendas/...`).

**Correr todos os importadores em sequência:**

```bash
./run-import-all.sh
```

**Só uma imobiliária** (única opção do shell script; slug = campo `agency` na base: `foxter`, `guarida`, `creditoreal`):

```bash
./run-import-all.sh --agency guarida
./run-import-all.sh --agency foxter
./run-import-all.sh --agency creditoreal
```

**Um importador diretamente** (lê `config/urls.json`):

```bash
python scripts/importers/foxter.py
python scripts/importers/guarida.py
python scripts/importers/creditoreal.py
```

Para um só anúncio Foxter ou uma busca, coloque a URL em `config/urls.json` e corra `python scripts/importers/foxter.py`. O mesmo fluxo em código: `from scripts.importers.foxter import import_foxter_product_url` (na raiz do projeto, com `PYTHONPATH` ou `python -c` a partir da raiz).

### Guarida

Coloque no `urls.json` uma **URL de busca** copiada do browser (de preferência com query string de filtros), por exemplo `https://guarida.com.br/busca/comprar/porto-alegre-rs?...`. Cada imóvel fica com `agency=guarida`, `source_url` em `https://guarida.com.br/...` e fotos em `photos_json`.

### Crédito Real

Coloque no `urls.json` uma URL de busca da Crédito Real, por exemplo `https://www.creditoreal.com.br/vendas/porto-alegre-rs/apartamento-residencial?...`. O importador percorre a paginação (`page=1,2,...`) até terminar, abre cada detalhe `/vendas/imovel/...` e grava com `agency=creditoreal`.

## Interface web (listagem)

Na raiz do projeto, com o venv ativo e dependências instaladas (`pip install -r requirements.txt`):

```bash
python -m mobihunter.web
```

Abre em **http://127.0.0.1:9090** (filtros: preço mín/máx e código do anúncio). Usa `data/imoveis.db` — importe dados antes.

Outra porta: `MOBIHUNTER_UI_PORT=8080 python -m mobihunter.web`

## Código partilhado

Em `app_review/` ficam filtros, paginação e `data_source` (SQLite). A UI em `mobihunter/web/` reutiliza isso.

Evite versionar ficheiros grandes ou sensíveis em `data/` sem necessidade (pode usar `.gitignore` local).
