-- ============================================================
-- memo列追加マイグレーション
-- Supabaseダッシュボード > SQL Editor で実行してください
-- ============================================================

-- 1. contracts テーブルに memo 列を追加
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS memo TEXT DEFAULT '';

-- 2. contract_summary ビューを memo 列込みで再作成
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
    c.contract_qty - COALESCE(SUM(d.delivery_qty), 0)       AS remaining_qty
FROM contracts c
LEFT JOIN deliveries d ON c.contract_no = d.contract_no
GROUP BY
    c.contract_no, c.field_name, c.seller, c.secondary_seller,
    c.general_contractor, c.contract_qty, c.memo;
