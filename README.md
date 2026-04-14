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

## Importar (Foxter)

Opcional: copie `config/foxter_urls.example.json` para `config/foxter_urls.json` e edite as URLs (o script usa esse ficheiro automaticamente se existir).

```bash
python scripts/importers/foxter.py
```

Import mais rápido: `--skip-photo-check` (não valida URLs de fotos). Por defeito o script faz COMMIT a cada 25 imóveis (`--commit-every N`; use `1` para gravar um a um). Ver `python scripts/importers/foxter.py --help` para `--workers`, `--page-workers`, `--max-photo-checks`, etc.

## Interface web (listagem)

Na raiz do projeto, com o venv ativo e dependências instaladas (`pip install -r requirements.txt`):

```bash
python -m mobihunter.web
```

Abre em **http://127.0.0.1:9090** (filtros: preço mín/máx e código do anúncio). Usa `data/imoveis.db` — importe dados antes com o Foxter.

Outra porta: `MOBIHUNTER_UI_PORT=8080 python -m mobihunter.web`

## Código partilhado

Em `app_review/` ficam filtros, paginação e `data_source` (SQLite). A UI em `mobihunter/web/` reutiliza isso.

Evite versionar ficheiros grandes ou sensíveis em `data/` sem necessidade (pode usar `.gitignore` local).
