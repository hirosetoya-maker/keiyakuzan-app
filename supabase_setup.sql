-- ============================================================
-- 生コン契約残管理アプリ - Supabase初期セットアップSQL
-- Supabaseダッシュボード > SQL Editor で実行してください
-- ============================================================

-- 1. 契約テーブル（is_completed なし：完了=物理削除）
CREATE TABLE IF NOT EXISTS contracts (
    contract_no         TEXT PRIMARY KEY,
    field_name          TEXT,
    seller              TEXT,
    secondary_seller    TEXT,
    general_contractor  TEXT,
    contract_qty        NUMERIC,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. 出荷実績テーブル
--    ON DELETE CASCADE：契約削除時に出荷実績も自動削除
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_date   DATE    NOT NULL,
    field_no        TEXT    NOT NULL,
    contract_no     TEXT    NOT NULL
        REFERENCES contracts(contract_no) ON DELETE CASCADE,
    slip_no         TEXT    NOT NULL,
    delivery_qty    NUMERIC NOT NULL DEFAULT 0,
    PRIMARY KEY (delivery_date, field_no, contract_no, slip_no)
);

-- 3. 契約残サマリビュー（集計をDB側で実行）
CREATE OR REPLACE VIEW contract_summary AS
SELECT
    c.contract_no,
    c.field_name,
    c.seller,
    c.secondary_seller,
    c.general_contractor,
    c.contract_qty,
    COALESCE(SUM(d.delivery_qty), 0)                        AS shipped_qty,
    c.contract_qty - COALESCE(SUM(d.delivery_qty), 0)       AS remaining_qty
FROM contracts c
LEFT JOIN deliveries d ON c.contract_no = d.contract_no
GROUP BY
    c.contract_no, c.field_name, c.seller, c.secondary_seller,
    c.general_contractor, c.contract_qty;

-- 4. updated_at 自動更新トリガー
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_contracts_updated_at ON contracts;
CREATE TRIGGER trg_contracts_updated_at
BEFORE UPDATE ON contracts
FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- 5. RLS無効化（service_role キー使用時）
ALTER TABLE contracts  DISABLE ROW LEVEL SECURITY;
ALTER TABLE deliveries DISABLE ROW LEVEL SECURITY;
