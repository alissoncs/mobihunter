# Mobihunter

Buscador pessoal de imóveis: importação para SQLite (`data/imoveis.db`) e revisão com interface web (NiceGUI).

A documentação de visão do produto, modelo de dados, pastas e fases de implementação está em **[docs/VISAO.md](docs/VISAO.md)**. A especificação de **sincronização e desativação** de anúncios ausentes na origem (regra comum a todos os importadores) está em **[docs/SPEC_SINCRONIZACAO_E_DESATIVACAO.md](docs/SPEC_SINCRONIZACAO_E_DESATIVACAO.md)**.

## Importação (Foxter)

Na raiz do repositório, com ambiente virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Copie `config/foxter_urls.example.json` para `config/foxter_urls.json`, edite as URLs e execute (o ficheiro é lido automaticamente se existir):

```bash
python scripts/importers/foxter.py
```

**Busca filtrada (Foxter Cia — todas as páginas):** use uma URL de listagem (`.../imoveis/a-venda/...`) com `--search-url` ou um objeto `{"search_url": "..."}` no JSON. O script abre o Chromium (headless), percorre `?page=1`, `?page=2`, … e importa cada imóvel pelo detalhe `/imovel/{código}`.

```bash
python scripts/importers/foxter.py --search-url "https://www.foxterciaimobiliaria.com.br/imoveis/..."
```

Ou uma URL avulsa de **anúncio** (incluindo teste sem gravar):

```bash
python scripts/importers/foxter.py --url "https://www.foxterciaimobiliaria.com.br/imovel/123" --dry-run
```

Os dados são gravados em **`data/imoveis.db`** (WAL, adequado a leituras paralelas). Na reimportação mantêm-se tags/categoria/notas/comentários; o preço é atualizado com rastreio (`price_previous`, `price_changed_at`, `price_change_count`).

**Progresso para a UI:** `python scripts/importers/foxter.py --machine-progress` emite uma linha JSON por evento em **stdout** (fases `start`, `pages`, `detail`, `done`); mensagens legíveis vão para **stderr**.

## App de revisão (web) — NiceGUI

Interface **NiceGUI** (AG Grid, filtros, paginação, fotos e revisão). Usa **`data/imoveis.db`** se existir e tiver registos; caso contrário lê **`data/imoveis.json`**. A lógica partilha os módulos em `app_review/` (`filters`, `pagination`, `data_source`).

Na raiz do repositório, com o ambiente virtual ativo:

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m nicegui_app.main
```

Abre o browser em **`http://localhost:9090`** (porta por defeito; outra: `MOBIHUNTER_PORT=8080 python -m nicegui_app.main`). Clique numa linha da tabela para ver o detalhe e gravar a revisão.

**Alternativa legada (Streamlit):** `streamlit run app_review/app.py` (porta 8501).

Evite versionar ficheiros grandes ou sensíveis em `data/` sem necessidade (pode usar `.gitignore` local).
