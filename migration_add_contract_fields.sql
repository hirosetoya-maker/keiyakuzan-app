-- ============================================================
-- 契約項目追加マイグレーション（契約日・着工日・JV・単価）
-- Supabaseダッシュボード > SQL Editor で実行してください
-- ============================================================

-- 1. contracts に新しい列を追加
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS contract_date DATE;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS start_date    DATE;
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS jv            TEXT NOT NULL DEFAULT '';
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS unit_price    NUMERIC;

-- 2. JV選択肢マスタ（アプリのデータ管理ページから追加・改名・削除できます）
CREATE TABLE IF NOT EXISTS jv_options (
    name       TEXT PRIMARY KEY,
    sort_order INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE jv_options DISABLE ROW LEVEL SECURITY;

INSERT INTO jv_options (name, sort_order) VALUES
    ('石川', 1),
    ('トウザキ', 2),
    ('高浜', 3),
    ('竹村', 4),
    ('マジマ', 5)
ON CONFLICT (name) DO NOTHING;

-- 3. contract_summary ビューに新しい列を追加（末尾に追加）
CREATE OR REPLACE VIEW contract_summary AS
SELECT
    c.contract_no,
    c.field_name,
    c.seller,
    c.secondary_seller,
    c.general_contractor,
    c.contract_qty,
    c.memo,
    COALESCE(SUM(d.delivery_qty), 0)                        AS shipped_qty,
    c.contract_qty - COALESCE(SUM(d.delivery_qty), 0)       AS remaining_qty,
    c.contract_date,
    c.start_date,
    c.jv,
    c.unit_price
FROM contracts c
LEFT JOIN deliveries d ON c.contract_no = d.contract_no
GROUP BY
    c.contract_no, c.field_name, c.seller, c.secondary_seller,
    c.general_contractor, c.contract_qty, c.memo,
    c.contract_date, c.start_date, c.jv, c.unit_price;
