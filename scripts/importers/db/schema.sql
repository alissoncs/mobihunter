CREATE TABLE IF NOT EXISTS imoveis (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL UNIQUE,
    agency TEXT NOT NULL,
    listing_code INTEGER,
    imported_at TEXT NOT NULL,
    title TEXT,
    description TEXT,
    currency TEXT,
    price_current REAL,
    price_previous REAL,
    listing_promo_old_price REAL,
    price_changed_at TEXT,
    price_change_count INTEGER NOT NULL DEFAULT 0,
    thumbnail_url TEXT,
    photos_json TEXT NOT NULL DEFAULT '[]',
    address TEXT,
    city TEXT,
    neighborhood TEXT,
    state TEXT,
    features_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT,
    category TEXT,
    rating REAL,
    notes TEXT,
    comments TEXT,
    review_status TEXT
);
CREATE INDEX IF NOT EXISTS idx_imoveis_agency ON imoveis(agency);
CREATE INDEX IF NOT EXISTS idx_imoveis_imported ON imoveis(imported_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_imoveis_agency_listing_code
    ON imoveis(agency, listing_code)
    WHERE listing_code IS NOT NULL;
