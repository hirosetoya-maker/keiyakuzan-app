-- ============================================================
-- 集計高速化ビュー追加マイグレーション
-- Supabaseダッシュボード > SQL Editor で実行してください
-- （実行しなくてもアプリは動きますが、データが増えると
-- 　データ管理ページの表示が遅くなります）
-- ============================================================

-- 1. 月別出荷件数（データ管理ページの月別一覧用）
CREATE OR REPLACE VIEW delivery_month_counts AS
SELECT
    to_char(delivery_date, 'YYYY-MM') AS ym,
    COUNT(*)                          AS cnt
FROM deliveries
GROUP BY 1;

-- 2. 孤立契約一覧（出荷実績が1件もない契約）
CREATE OR REPLACE VIEW orphan_contracts AS
SELECT c.contract_no
FROM contracts c
WHERE NOT EXISTS (
    SELECT 1 FROM deliveries d WHERE d.contract_no = c.contract_no
);
