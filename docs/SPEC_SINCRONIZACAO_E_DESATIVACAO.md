# Especificação: sincronização, imóveis ausentes na origem e desativação local

Este documento define a **regra de negócio** e o **contrato técnico** comum a todos os importadores (Foxter, futuros sites, etc.). Serve de base para implementar scripts novos no Cursor **sem divergir** da lógica aqui descrita.

---

## 1. Problema

Os anúncios podem **deixar de existir** no site da imobiliária (vendido, retirado, expirado). A base local deve refletir isso **sem apagar** o registo nem perder revisão humana (tags, comentários, etc.).

**Objetivo:** marcar no armazenamento local que o imóvel **não está mais disponível na origem** (estado “desativado” / “inativo na origem”), com **rastreabilidade** (quando e no âmbito de que sincronização).

---

## 2. Princípios

1. **Nunca apagar** um imóvel por ter sumido do site — apenas mudar **estado** e **metadados de sincronização**.
2. **Preservar** sempre campos de **revisão humana** (tags, categoria, notas, comentários, rating, **like/dislike** em `review_status`, `archived`), salvo política explícita futura. Nova importação **atualiza** dados do anúncio na origem (preço, fotos, texto, morada, etc.) sem apagar essa revisão — implementado em `upsert_import_records` (`scripts/importers/sqlite_store.py`).
3. **Desativação por ausência** só é válida no **âmbito de uma sincronização completa** em que o sistema conhece o **conjunto fechado** de anúncios ainda listados para aquele contexto (ver secção 5).
4. **Reativação:** se o mesmo `source_url` voltar a aparecer numa sincronização futura, o registo deve poder voltar a **ativo na origem**.
5. **Idempotência:** repetir a mesma importação não deve alternar estados sem necessidade.

---

## 3. Modelo de dados (campos de estado na origem)

Além dos campos já existentes de anúncio e preço, cada imóvel deve suportar:

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `is_active` | INTEGER (0/1) ou BOOLEAN | `1` = ainda visto na origem no último critério aplicável; `0` = marcado como ausente / inativo na origem. |
| `last_seen_at` | TEXT (ISO 8601) | Última vez em que o anúncio foi **confirmado** presente (listagem ou detalhe com sucesso). |
| `inactive_since` | TEXT (ISO 8601), nullable | Quando passou a inativo na origem (só preenchido ao desativar). |
| `inactive_reason` | TEXT, nullable | Valores sugeridos: `missing_from_listing`, `detail_not_found`, `manual`, `unknown`. |
| `sync_source_hash` | TEXT, nullable | Identificador estável do **contexto de descoberta** (ver secção 4). Atualizado quando o imóvel é importado/atualizado a partir desse contexto. |

**Notas:**

- `listing_promo_old_price`, `price_previous`, etc. continuam a tratar **preço**; os campos acima tratam **existência na origem**.
- Para imóveis importados só por URL de **detalhe** (sem listagem), definir política: normalmente **não desativar outros** nessa execução; opcionalmente atualizar só `last_seen_at` desse URL.

---

## 4. Contexto de sincronização (`sync_source_hash`)

Cada configuração que representa um “universo” de anúncios (tipicamente **uma URL de busca** normalizada) gera um identificador:

- `sync_source_hash = SHA-256 hex truncado (ex.: 32 chars) de normalize(URL_de_busca)`  
- Todos os imóveis descobertos nessa busca devem gravar/atualizar esse valor no registo ao serem importados.

Assim, na **mesma** imobiliária pode haver várias buscas (vários hashes); um imóvel pode ser reencontrado por outra busca — a política de qual hash prevalece deve ser **uma linha por `source_url` único** e o `sync_source_hash` o da **última importação que tocou** esse registo, ou armazenar **lista** — para a v1 recomenda-se **um hash por registo = última origem de descoberta**, documentado.

---

## 5. Quando aplicar desativação por “sumiu do site”

### 5.1 Modo elegível: sincronização completa por listagem

Disponível quando o importador:

1. Percorre **todas** as páginas da busca (ou API equivalente).
2. Obtém o conjunto **S** de identificadores canónicos (`source_url` normalizado ou `id` estável já usado na base).
3. Completa sem erro fatal que invalide o conjunto S (definir o que fazer em falha parcial — ver secção 7).

**Após** gravar/atualizar todos os imóveis em S:

- Para registos na base com `agency = A` **e** `sync_source_hash = H` (o desta execução),
- Se `source_url` **não** está em S **e** `is_active = 1`,
- Então: `is_active = 0`, `inactive_since = agora`, `inactive_reason = missing_from_listing` (ou mais específico se aplicável).

### 5.2 Modo não elegível

- Importação de **uma única URL** de detalhe: **não** desativar outros imóveis dessa agência.
- Execução que **não** obtém lista completa (ex.: limite de páginas, erro a meio): **não** desativar por ausência nessa execução, salvo flag explícita “aceito risco” (não recomendado na v1).

---

## 6. Reativação

Se `source_url ∈ S` na sincronização atual:

- `is_active = 1`
- `inactive_since = NULL`
- `inactive_reason = NULL` (ou mantém histórico noutra tabela no futuro)
- `last_seen_at = agora`

---

## 7. Falhas parciais e segurança

Para não desativar em massa por bug de rede ou HTML:

- Só executar o passo de desativação se `success_complete_listing = true` (todas as páginas esperadas processadas).
- Opcional: exigir `min_pages == expected_pages` ou `len(S) >= total_anunciado_no_portal` quando o portal expõe total.
- Em caso de dúvida: **não desativar**; registar log/`last_run_status` na execução (extensão futura).

---

## 8. Contrato comum para qualquer script de imobiliária

Cada importador (Foxter, outro site) deve implementar os mesmos **pontos de extensão** lógicos:

| Etapa | Responsabilidade |
|-------|------------------|
| **Descoberta** | A partir de URL(s) de config, produzir conjunto S de imóveis (ids + URLs canónicos) e, por item, dados de anúncio. |
| **Normalização** | `normalize_source_url`, `stable_id` alinhados ao projeto. |
| **Upsert** | Atualizar dados de anúncio; preservar revisão humana; atualizar preço com regras já definidas no projeto. |
| **Marcação de contexto** | Em cada upsert a partir de busca, gravar `sync_source_hash` e `last_seen_at`. |
| **Fecho de sincronização** | Se modo completo e sucesso: chamar rotina central **“aplicar ausentes”** com `(agency, sync_source_hash, conjunto_S)`. |

A rotina **“aplicar ausentes”** deve ser **uma função partilhada** (ex. `sqlite_store` ou módulo `sync_rules.py`), **não** duplicada por site.

---

## 9. Parâmetros de CLI sugeridos (padrão entre sites)

- `--deactivate-missing` — boolean; só com sincronização completa bem-sucedida.
- `--sync-source-url` — URL de busca usada para calcular `sync_source_hash` (se omitido, derivar da config ou do único search em execução).

Comportamento quando `--deactivate-missing` está desligado: apenas upsert + `last_seen_at` para o que foi visto; **nenhuma** desativação por ausência.

---

## 10. Concorrência e UI

- Vários processos podem correr em paralelo (UI); SQLite em modo **WAL** reduz bloqueios de leitura.
- Escritas de desativação devem usar **transação curta** na mesma função que o upsert do lote, ou transação dedicada imediatamente a seguir, para evitar estados intermédios visíveis.
- A UI não deve assumir JSON; ler sempre da base com filtros `is_active`, `inactive_since`, etc.

---

## 11. Resumo da regra de negócio (checklist para novos scripts)

- [ ] Usar `sync_source_hash` para amarrar desativação a uma **busca** concreta.
- [ ] Atualizar `last_seen_at` em todo imóvel **presente** em S após importação bem-sucedida.
- [ ] Só desativar com **lista completa** e flag explícita.
- [ ] Nunca apagar linha por ausência; usar `is_active` + timestamps + motivo.
- [ ] Reativar automaticamente se o anúncio voltar a S.
- [ ] Centralizar “aplicar ausentes” num único módulo partilhado.

---

*Documento de especificação — Mobihunter. A implementação concreta (nomes exatos de colunas, migrações SQL) deve seguir este documento e a estrutura actual em `sqlite_store.py`.*
