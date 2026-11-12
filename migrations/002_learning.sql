-- Learning loop (ARCHITECTURE §16). Everything learned is versioned, attributable and reversible.

CREATE TABLE IF NOT EXISTS layouts (
  id             bigserial PRIMARY KEY,
  vendor_id      text NOT NULL REFERENCES vendors(id),
  fingerprint    jsonb NOT NULL,              -- sorted static label set
  template       jsonb NOT NULL,              -- field rules + line-table columns
  status         text NOT NULL DEFAULT 'shadow',  -- shadow | t2 | t1 | retired
  agree_streak   int NOT NULL DEFAULT 0,
  uses           int NOT NULL DEFAULT 0,
  demotions      int NOT NULL DEFAULT 0,
  version        int NOT NULL DEFAULT 1,
  learned_from   text[] NOT NULL DEFAULT '{}',
  history        jsonb NOT NULL DEFAULT '[]',
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS layouts_vendor ON layouts(vendor_id);

CREATE TABLE IF NOT EXISTS vendor_item_map (
  vendor_id        text NOT NULL REFERENCES vendors(id),
  invoice_text_norm text NOT NULL,
  sku              text NOT NULL,
  source           text NOT NULL,             -- description_pairing | human
  confirmed_by     text,
  uses             int NOT NULL DEFAULT 0,
  learned_from     text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (vendor_id, invoice_text_norm)
);

CREATE TABLE IF NOT EXISTS judgment_cache (
  key        text PRIMARY KEY,
  question   text NOT NULL,
  backend    text NOT NULL,
  answer     jsonb NOT NULL,
  hits       int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reading_hints (
  id          bigserial PRIMARY KEY,
  vendor_id   text NOT NULL REFERENCES vendors(id),
  hint        text NOT NULL,
  source_run  text,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS learning_events (
  id        bigserial PRIMARY KEY,
  run_id    text,
  vendor_id text,
  kind      text NOT NULL,   -- layout_learned | promoted | demoted | alias_learned | item_learned | cache_hit | hint_added
  detail    jsonb NOT NULL DEFAULT '{}',
  at        timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE vendor_aliases ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'master';
ALTER TABLE vendor_aliases ADD COLUMN IF NOT EXISTS learned_from text;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS read_tier text;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS layout_id bigint;
