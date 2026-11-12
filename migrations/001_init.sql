-- Clearline schema v1. Money is NUMERIC(14,2); quantities NUMERIC(14,3).

CREATE TABLE IF NOT EXISTS vendors (
  id              text PRIMARY KEY,
  legal_name      text NOT NULL,
  tax_id          text,
  status          text NOT NULL DEFAULT 'approved',      -- approved | blocked | pending
  requires_po     boolean NOT NULL DEFAULT true,
  domains         text[] NOT NULL DEFAULT '{}',
  phone           text,
  category        text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vendor_aliases (
  vendor_id text REFERENCES vendors(id),
  alias     text NOT NULL,
  PRIMARY KEY (vendor_id, alias)
);

CREATE TABLE IF NOT EXISTS vendor_bank_accounts (
  id              bigserial PRIMARY KEY,
  vendor_id       text NOT NULL REFERENCES vendors(id),
  iban            text,
  account_number  text,
  routing_number  text,
  bank_name       text,
  verified_at     timestamptz,
  verified_by     text,
  callback_phone  text,
  added_at        timestamptz NOT NULL DEFAULT now(),
  active          boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS purchase_orders (
  id          text PRIMARY KEY,                -- PO-4404
  vendor_id   text NOT NULL REFERENCES vendors(id),
  currency    text NOT NULL DEFAULT 'USD',
  status      text NOT NULL DEFAULT 'open',    -- open | closed
  total       numeric(14,2) NOT NULL,
  blanket     boolean NOT NULL DEFAULT false,
  issued_on   date NOT NULL,
  requester   text
);

CREATE TABLE IF NOT EXISTS po_lines (
  id          text PRIMARY KEY,                -- PO-4404-1
  po_id       text NOT NULL REFERENCES purchase_orders(id),
  line_no     int NOT NULL,
  sku         text,
  description text NOT NULL,
  qty         numeric(14,3) NOT NULL,
  uom         text NOT NULL DEFAULT 'EA',
  unit_price  numeric(14,4) NOT NULL,
  gl_account  text,
  cost_center text
);

CREATE TABLE IF NOT EXISTS goods_receipts (
  id          text PRIMARY KEY,                -- GRN-880
  po_id       text NOT NULL REFERENCES purchase_orders(id),
  received_on date NOT NULL,
  posted_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS grn_lines (
  grn_id      text REFERENCES goods_receipts(id),
  po_line_id  text REFERENCES po_lines(id),
  qty         numeric(14,3) NOT NULL,
  PRIMARY KEY (grn_id, po_line_id)
);

CREATE TABLE IF NOT EXISTS documents (
  id            bigserial PRIMARY KEY,
  sha256        text UNIQUE NOT NULL,
  filename      text,
  mime          text NOT NULL,
  pages         int,
  text_layer    boolean,
  channel       text NOT NULL DEFAULT 'upload',  -- upload | email | scenario
  email_from    text,
  email_reply_to text,
  email_subject text,
  email_body    text,
  storage_path  text NOT NULL,
  line_map      jsonb,
  scenario      text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoices (
  id                  bigserial PRIMARY KEY,
  document_id         bigint REFERENCES documents(id),
  document_type       text,
  invoice_number      text,
  invoice_number_norm text,
  vendor_id           text REFERENCES vendors(id),
  po_id               text REFERENCES purchase_orders(id),
  currency            text,
  invoice_date        date,
  subtotal            numeric(14,2),
  tax                 numeric(14,2),
  freight             numeric(14,2),
  total               numeric(14,2),
  state               text NOT NULL DEFAULT 'received', -- received | in_review | approved | partially_paid | rejected | filed
  external            boolean NOT NULL DEFAULT false,   -- seeded history
  created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
  id               text PRIMARY KEY,
  invoice_id       bigint REFERENCES invoices(id),
  document_id      bigint REFERENCES documents(id),
  parent_run_id    text REFERENCES runs(id),
  trigger          text NOT NULL,      -- ingest | correction | policy_replay | grn_posted | vendor_reply
  status           text NOT NULL,      -- running | awaiting_human | completed | failed
  sla_due_at       timestamptz,
  policy_version   text,
  model_versions   jsonb NOT NULL DEFAULT '{}',
  decision_backend text,
  mode             text,               -- live | fixture | offline
  cost_usd         numeric(10,5) NOT NULL DEFAULT 0,
  pinned_fields    jsonb NOT NULL DEFAULT '{}',
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  error            text
);

CREATE TABLE IF NOT EXISTS run_events (
  seq         bigserial PRIMARY KEY,
  run_id      text NOT NULL REFERENCES runs(id),
  stage       text NOT NULL,
  type        text NOT NULL,           -- stage.started | stage.finished | agent.step | run.finished | info
  status      text,                    -- running | pass | warn | block | degraded | skipped
  ts          timestamptz NOT NULL DEFAULT clock_timestamp(),
  duration_ms int,
  summary     text,
  data        jsonb
);
CREATE INDEX IF NOT EXISTS run_events_run ON run_events(run_id, seq);

CREATE TABLE IF NOT EXISTS invoice_fields (
  id          bigserial PRIMARY KEY,
  run_id      text NOT NULL REFERENCES runs(id),
  field       text NOT NULL,
  value       text,
  evidence    text NOT NULL,           -- verified | read | uncertain
  reasons     text[] NOT NULL DEFAULT '{}',
  readings    jsonb,
  page        int,
  bbox        jsonb,
  set_by      text NOT NULL DEFAULT 'machine'
);

CREATE TABLE IF NOT EXISTS facts (
  run_id          text PRIMARY KEY REFERENCES runs(id),
  extraction      jsonb NOT NULL,
  facts           jsonb NOT NULL,
  master_snapshot jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS investigations (
  run_id              text PRIMARY KEY REFERENCES runs(id),
  backend             text NOT NULL,
  steps               jsonb NOT NULL,
  hypotheses          jsonb NOT NULL,
  proposed_resolution text,
  question            text,
  recheck_result      jsonb
);

CREATE TABLE IF NOT EXISTS proposals (
  id                 bigserial PRIMARY KEY,
  run_id             text NOT NULL REFERENCES runs(id),
  invoice_id         bigint NOT NULL REFERENCES invoices(id),
  outcome            text NOT NULL,
  sub_status         text,
  owed               text NOT NULL,     -- yes | partly | no
  safe_to_pay        text NOT NULL,     -- yes | no | n/a
  recommended_action text,
  owner_role         text,
  question           text,
  attempted          jsonb NOT NULL DEFAULT '[]',
  amount_payable     numeric(14,2),
  amount_held        numeric(14,2),
  gl_coding          jsonb NOT NULL DEFAULT '[]',
  narrative          text,
  vendor_email       text,
  findings           jsonb NOT NULL,
  zen_trace          jsonb,
  superseded_by      bigint REFERENCES proposals(id),
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decisions (
  id           bigserial PRIMARY KEY,
  proposal_id  bigint NOT NULL REFERENCES proposals(id),
  actor        text NOT NULL,
  role         text NOT NULL,
  action       text NOT NULL,
  reason       text NOT NULL,
  amount       numeric(14,2),
  data         jsonb,
  decided_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (proposal_id, action)
);

CREATE TABLE IF NOT EXISTS billed_ledger (
  id                  bigserial PRIMARY KEY,
  po_line_id          text REFERENCES po_lines(id),
  po_id               text REFERENCES purchase_orders(id),
  invoice_id          bigint NOT NULL REFERENCES invoices(id),
  vendor_id           text NOT NULL REFERENCES vendors(id),
  invoice_number_norm text NOT NULL,
  qty                 numeric(14,3),
  amount              numeric(14,2) NOT NULL,
  state               text NOT NULL DEFAULT 'billed',  -- billed | reversed
  tranche             int NOT NULL DEFAULT 1,
  created_at          timestamptz NOT NULL DEFAULT now()
);
-- Final duplicate guard: one live first-tranche bill per vendor + normalised number.
CREATE UNIQUE INDEX IF NOT EXISTS billed_ledger_no_dup
  ON billed_ledger (vendor_id, invoice_number_norm, tranche, coalesce(po_line_id, ''))
  WHERE state <> 'reversed';

CREATE TABLE IF NOT EXISTS erp_bills (
  id          text PRIMARY KEY,         -- B-1042
  invoice_id  bigint NOT NULL REFERENCES invoices(id),
  vendor_id   text NOT NULL,
  amount      numeric(14,2) NOT NULL,
  currency    text NOT NULL,
  due_date    date,
  lines       jsonb NOT NULL,
  posted_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policies (
  version        text PRIMARY KEY,
  jdm            jsonb NOT NULL,
  line_jdm       jsonb NOT NULL,
  text_md        text,
  status         text NOT NULL DEFAULT 'active', -- draft | active | retired
  drafted_by     text,
  approved_by    text,
  approved_at    timestamptz,
  replay_summary jsonb,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
  id      bigserial PRIMARY KEY,
  actor   text NOT NULL,
  action  text NOT NULL,
  entity  text NOT NULL,
  before  jsonb,
  after   jsonb,
  at      timestamptz NOT NULL DEFAULT now()
);

-- Append-only audit: block UPDATE/DELETE/TRUNCATE at the database.
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log;
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();
