-- ============================================================
-- 取込履歴テーブル追加マイグレーション
-- Supabaseダッシュボード > SQL Editor で実行してください
-- （実行するまでアプリは従来どおり動作します。実行すると
-- 　CSV取込の「ファイル単位の取消」が使えるようになります）
-- ============================================================

-- 1. 取込履歴テーブル
CREATE TABLE IF NOT EXISTS import_batches (
    id           UUID PRIMARY KEY,
    filename     TEXT NOT NULL,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    record_count INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE import_batches DISABLE ROW LEVEL SECURITY;

-- 2. deliveries に取込バッチID列を追加
--    （バッチ削除時は出荷実績も消すのでここでは SET NULL でよい）
ALTER TABLE deliveries
    ADD COLUMN IF NOT EXISTS batch_id UUID
    REFERENCES import_batches(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_deliveries_batch ON deliveries(batch_id);
