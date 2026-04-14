"""SQL parametrizado para a tabela `imoveis` — usado por sqlite_store e testável."""

# --- Leituras
SELECT_BY_ID = "SELECT * FROM imoveis WHERE id = ?"

SELECT_BY_AGENCY_AND_LISTING_CODE = (
    "SELECT * FROM imoveis WHERE agency = ? AND listing_code = ?"
)

SELECT_BY_SOURCE_URL = "SELECT * FROM imoveis WHERE source_url = ?"

COUNT_IMOVEIS = "SELECT COUNT(*) AS c FROM imoveis"

SELECT_ALL_ORDERED = """
SELECT * FROM imoveis
ORDER BY COALESCE(price_current, 1e308) ASC, id ASC
"""

# --- Escrita (importação)
INSERT_IMOVEL = """
INSERT INTO imoveis (
    id, source_url, agency, listing_code, imported_at,
    title, description, currency,
    price_current, price_previous, listing_promo_old_price,
    price_changed_at, price_change_count,
    thumbnail_url, photos_json, address, city, neighborhood, state,
    features_json, tags_json, category, rating, notes, comments,
    review_status
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

UPDATE_IMOVEL_AFTER_IMPORT = """
UPDATE imoveis SET
    source_url = ?,
    agency = ?,
    listing_code = ?,
    imported_at = ?,
    title = ?,
    description = ?,
    currency = ?,
    price_current = ?,
    price_previous = ?,
    listing_promo_old_price = ?,
    price_changed_at = ?,
    price_change_count = ?,
    thumbnail_url = ?,
    photos_json = ?,
    address = ?,
    city = ?,
    neighborhood = ?,
    state = ?,
    features_json = ?,
    tags_json = ?,
    category = ?,
    rating = ?,
    notes = ?,
    comments = ?,
    review_status = ?
WHERE id = ?
"""

DELETE_IMOVEL_BY_ID = "DELETE FROM imoveis WHERE id = ?"

UPDATE_IMOVEL_PRIMARY_KEY = "UPDATE imoveis SET id = ? WHERE id = ?"

# --- Revisão humana (app web)
UPDATE_REVIEW_FIELDS = """
UPDATE imoveis SET
    tags_json = ?,
    category = ?,
    rating = ?,
    notes = ?,
    comments = ?,
    review_status = ?
WHERE id = ?
"""
