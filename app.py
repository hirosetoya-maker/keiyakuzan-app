import hashlib
import hmac
import html as html_mod
import io
import time
import uuid
import zipfile
from datetime import datetime, timedelta

import extra_streamlit_components as stx
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from dateutil import parser as dateutil_parser
from supabase import Client, create_client

# ── ページ設定 ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="生コン契約残管理",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── カスタムCSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* 全デバイス共通：不要UI非表示 */
/* ※サイドバーの開閉ボタン（stSidebarCollapseButton / stSidebarCollapsedControl）と
   ヘッダーは隠さないこと。隠すと畳んだサイドバーを戻せなくなる */
#MainMenu        {display: none !important;}
footer           {display: none !important;}
[data-testid="stDeployButton"]    {display: none !important;}
[data-testid="stAppDeployButton"] {display: none !important;}
[data-testid="stStatusWidget"]   {display: none !important;}
[data-testid="stMainMenu"]       {display: none !important;}

/* ヘッダーは開閉ボタンの置き場所として残しつつ、目立たなくする */
header[data-testid="stHeader"] {
    background: transparent !important;
}
/* データエディタの列ヘッダーメニューを非表示 */
.ag-header-cell-menu-button      {display: none !important;}
.ag-icon-menu                    {display: none !important;}

/* KPIカードに白背景・影・角丸 */
[data-testid="stMetric"] {
    background: white;
    border-radius: 10px;
    padding: 1.2rem 1.5rem;
    box-shadow: 0 2px 8px rgba(0,0,0,0.07);
    border: 1px solid #e8ecf0;
}

/* ログイン画面：メインエリアの余白をなくす */
.login-page .block-container {
    padding-top: 6rem;
}

/* スマホ：フィルタボックスを縦1列に */
@media (max-width: 640px) {
    [data-testid="column"] {
        flex: 0 0 100% !important;
        max-width: 100% !important;
    }
}
</style>
""",
    unsafe_allow_html=True,
)


# ── ダークモード CSS ────────────────────────────────────────────────────────────
DARK_MODE_CSS = """
<style>
.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="block-container"] {
    background-color: #0e1117 !important;
    color: #e0e4f0 !important;
}
[data-testid="stSidebar"] { background-color: #161b2e !important; }
[data-testid="stSidebar"] * { color: #e0e4f0 !important; }
[data-testid="stMetric"] {
    background: #1a1f2e !important;
    border-color: #2d3d6b !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4) !important;
}
[data-testid="stMetric"] * { color: #e0e4f0 !important; }
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    background-color: #1a1f2e !important;
    color: #e0e4f0 !important;
    border-color: #3d4466 !important;
}
label, p, h1, h2, h3, h4, h5 { color: #e0e4f0 !important; }
hr { border-color: #2d3d6b !important; }
[data-testid="stExpander"] {
    border-color: #2d3d6b !important;
    background-color: #1a1f2e !important;
}
[data-testid="stDataEditor"] > div { background-color: #1a1f2e !important; }
[data-testid="stRadio"] * { color: #e0e4f0 !important; }
.stCaption { color: #8892b0 !important; }
[data-testid="stBaseButton-secondary"] {
    background-color: #1a1f2e !important;
    color: #e0e4f0 !important;
    border: 1px solid #3d4466 !important;
}
[data-testid="stBaseButton-secondary"] * { color: #e0e4f0 !important; }
</style>
"""

# ── Supabase クライアント ──────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


# ── データ読込（キャッシュ付き） ──────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_data() -> pd.DataFrame:
    supabase = get_supabase()
    PAGE_SIZE = 1000
    all_rows: list[dict] = []
    offset = 0
    while True:
        res = (
            supabase.table("contract_summary")
            .select("*")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        all_rows.extend(res.data)
        if len(res.data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if not all_rows:
        return pd.DataFrame(
            columns=[
                "contract_no", "field_name", "seller", "secondary_seller",
                "general_contractor", "contract_qty", "memo", "shipped_qty",
                "remaining_qty", "contract_date", "start_date", "jv", "unit_price",
            ]
        )

    df = pd.DataFrame(all_rows)

    # マイグレーション未実行でも動くよう、無い列はデフォルトで補う
    for col, default in (
        ("memo", ""), ("contract_date", None), ("start_date", None),
        ("jv", ""), ("unit_price", None),
    ):
        if col not in df.columns:
            df[col] = default

    df["contract_qty"] = pd.to_numeric(df["contract_qty"], errors="coerce")
    df["shipped_qty"] = pd.to_numeric(df["shipped_qty"], errors="coerce").fillna(0.0)
    df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce")

    # "nan" 文字列を空欄に統一
    for col in ("seller", "secondary_seller", "general_contractor",
                "field_name", "memo", "jv"):
        if col in df.columns:
            df[col] = df[col].replace("nan", "").fillna("")

    def calc_remaining(row):
        if pd.isna(row["contract_qty"]):
            return None
        return max(0.0, float(row["contract_qty"]) - float(row["shipped_qty"]))

    df["remaining_qty"] = pd.to_numeric(df.apply(calc_remaining, axis=1), errors="coerce")
    return df


# ── 取込済み年月一覧（データ管理用） ──────────────────────────────────────────
@st.cache_data(ttl=60)
def load_delivery_months() -> dict[str, int]:
    """deliveries テーブルから年月ごとのレコード数を返す（新しい順）"""
    supabase = get_supabase()

    # DB側で集計するビューがあれば使う（高速）。なければ全行スキャン
    try:
        res = supabase.table("delivery_month_counts").select("*").execute()
        return dict(sorted(
            ((row["ym"], int(row["cnt"])) for row in res.data),
            reverse=True,
        ))
    except Exception:
        pass

    PAGE_SIZE = 1000
    offset = 0
    month_counts: dict[str, int] = {}
    while True:
        res = (
            supabase.table("deliveries")
            .select("delivery_date")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        for row in res.data:
            ym = str(row["delivery_date"])[:7]  # "YYYY-MM"
            month_counts[ym] = month_counts.get(ym, 0) + 1
        if len(res.data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return dict(sorted(month_counts.items(), reverse=True))


# ── JV選択肢マスタ ────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_jv_options() -> list[str] | None:
    """JV選択肢を表示順で返す。テーブル未作成なら None"""
    try:
        res = (
            get_supabase().table("jv_options")
            .select("*")
            .order("sort_order")
            .order("name")
            .execute()
        )
        return [row["name"] for row in res.data]
    except Exception:
        return None


def _jv_to_list(jv: object) -> list[str]:
    """カンマ区切りのJV文字列をリストに変換"""
    if jv is None or pd.isna(jv) or str(jv).strip() == "":
        return []
    return [p.strip() for p in str(jv).split(",") if p.strip()]


# ── 月別出荷量（サマリー印刷用） ──────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_monthly_shipments() -> list[tuple[str, float]]:
    """(年月, 出荷量合計) のリストを古い順に返す"""
    supabase = get_supabase()

    # ビューに sum_qty 列があればそれを使う（高速）
    try:
        res = supabase.table("delivery_month_counts").select("*").execute()
        if res.data and "sum_qty" in res.data[0]:
            return sorted(
                (row["ym"], float(row["sum_qty"] or 0)) for row in res.data
            )
    except Exception:
        pass

    # フォールバック: 全行を取得して集計
    rows = []
    offset = 0
    while True:
        res = (
            supabase.table("deliveries")
            .select("delivery_date,delivery_qty")
            .range(offset, offset + 999)
            .execute()
        )
        rows.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    agg: dict[str, float] = {}
    for r in rows:
        ym = str(r["delivery_date"])[:7]
        agg[ym] = agg.get(ym, 0.0) + float(r["delivery_qty"] or 0)
    return sorted(agg.items())


# ── 取込履歴一覧（ファイル単位取消用） ────────────────────────────────────────
@st.cache_data(ttl=60)
def load_import_batches() -> list[dict] | None:
    """取込履歴を新しい順に返す。テーブル未作成なら None"""
    try:
        res = (
            get_supabase().table("import_batches")
            .select("*")
            .order("imported_at", desc=True)
            .execute()
        )
        return res.data
    except Exception:
        return None


# ── ログイン保持（Cookie） ─────────────────────────────────────────────────────
AUTH_COOKIE = "keiyakuzan_auth"
AUTH_DAYS = 30


def _auth_secret() -> bytes:
    """トークン署名用の秘密鍵（既存のシークレットから導出、追加設定不要）"""
    raw = st.secrets["SUPABASE_KEY"] + st.secrets.get("APP_PASSWORD", "")
    return raw.encode()


def _make_auth_token() -> str:
    """有効期限つき署名トークン: "<期限unix秒>.<HMAC署名>" """
    expiry = int(time.time()) + AUTH_DAYS * 24 * 3600
    sig = hmac.new(_auth_secret(), str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def _verify_auth_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expiry_s, sig = token.split(".", 1)
    try:
        expiry = int(expiry_s)
    except ValueError:
        return False
    if time.time() > expiry:
        return False
    expected = hmac.new(_auth_secret(), expiry_s.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def get_cookie_manager() -> stx.CookieManager:
    return stx.CookieManager(key="cookie_manager")


# ── 認証画面 ──────────────────────────────────────────────────────────────────
def show_login() -> None:
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        st.markdown(
            """
            <div style="text-align:center; padding: 2rem 0 1.5rem;">
                <div style="font-size:3rem;">🏗️</div>
                <div style="font-size:1.6rem; font-weight:700; margin: 0.4rem 0 0.2rem;">
                    生コン契約残管理
                </div>
                <div style="font-size:1rem; color:#555;">中央コンクリート</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("---")

        fail_count = st.session_state.get("login_fail_count", 0)
        lock_until = st.session_state.get("login_lock_until", 0.0)

        if time.time() < lock_until:
            remaining = int(lock_until - time.time()) + 1
            st.error(f"⛔ {remaining}秒後に再試行できます")
            time.sleep(1)
            st.rerun()
            return

        pw = st.text_input("パスワード", type="password", key="login_pw",
                           placeholder="パスワードを入力")
        if st.button("ログイン", type="primary", use_container_width=True, key="login_btn"):
            if pw == st.secrets.get("APP_PASSWORD", ""):
                st.session_state.authenticated = True
                st.session_state.login_fail_count = 0
                st.rerun()
            else:
                fail_count += 1
                st.session_state.login_fail_count = fail_count
                if fail_count >= 3:
                    st.session_state.login_lock_until = time.time() + 5
                    st.error("⛔ 3回失敗しました。5秒間ロックします。")
                else:
                    st.error(f"パスワードが違います（{fail_count}/3回）")

        st.markdown("<br>", unsafe_allow_html=True)
        st.caption("※ パスワードは管理者にお問合せください")


def _vals_equal(col: str, edited, initial) -> bool:
    """編集値と初期値が実質同じなら True（二重保存防止用）"""
    import math
    if col in ("contract_qty", "unit_price"):
        def _n(v):
            if v is None:
                return None
            try:
                f = float(v)
                return None if math.isnan(f) else f
            except (TypeError, ValueError):
                return None
        return _n(edited) == _n(initial)
    if col == "memo":
        return (edited or "") == (initial or "")
    if col in ("contract_date", "start_date"):
        return edited == initial
    if col == "jv":
        def _jv(v):
            if isinstance(v, list):
                return sorted(v)
            if not v:
                return []
            return sorted(x.strip() for x in str(v).split(",") if x.strip())
        return _jv(edited) == _jv(initial)
    return edited == initial


@st.fragment
def _data_editor_fragment(supabase: Client) -> None:
    """データエディタをフラグメントとして描画。
    保存後は fragment スコープのみリランするため、スクロール位置が保持される。"""
    f_query      = st.session_state.get("f_query", "")
    f_seller     = st.session_state.get("f_seller", "")
    f_secondary  = st.session_state.get("f_secondary", "")
    f_contractor = st.session_state.get("f_contractor", "")
    f_field      = st.session_state.get("f_field", "")
    sort_label   = st.session_state.get("sort_order", list(SORT_OPTIONS)[0])

    # キャッシュクリア後は最新データを取得
    df = load_data()

    # フィルタ適用
    filtered = df.copy()
    if f_query:
        search_cols = [
            "seller", "secondary_seller", "general_contractor",
            "field_name", "contract_no", "memo",
        ]
        mask = pd.Series(False, index=filtered.index)
        for col in search_cols:
            if col in filtered.columns:
                mask |= (
                    filtered[col].astype(str)
                    .str.contains(f_query, case=False, na=False)
                )
        filtered = filtered[mask]
    if f_seller:
        filtered = filtered[
            filtered["seller"].str.contains(f_seller, case=False, na=False)
        ]
    if f_secondary:
        filtered = filtered[
            filtered["secondary_seller"].str.contains(f_secondary, case=False, na=False)
        ]
    if f_contractor:
        filtered = filtered[
            filtered["general_contractor"].str.contains(f_contractor, case=False, na=False)
        ]
    if f_field:
        filtered = filtered[
            filtered["field_name"].str.contains(f_field, case=False, na=False)
        ]
    filtered = (
        filtered
        .sort_values("secondary_seller", ascending=True, na_position="last")
        .reset_index(drop=True)
    )
    sorted_df = _apply_sort(filtered, sort_label)

    memo_col = "memo" if "memo" in sorted_df.columns else None
    display_cols = [
        "contract_no", "seller", "secondary_seller", "general_contractor",
        "field_name", "contract_date", "start_date", "jv",
        "contract_qty", "shipped_qty", "remaining_qty", "unit_price",
    ]
    if memo_col:
        display_cols.append("memo")
    display_df = sorted_df[display_cols].copy()
    for col in ("contract_qty", "remaining_qty", "unit_price"):
        if col in display_df.columns:
            display_df[col] = pd.to_numeric(
                display_df[col], errors="coerce"
            ).astype("Float64")
    for col in ("contract_date", "start_date"):
        display_df[col] = pd.to_datetime(
            display_df[col], errors="coerce"
        ).dt.date
    display_df["jv"] = display_df["jv"].map(_jv_to_list)
    jv_master = load_jv_options()
    jv_choices = list(dict.fromkeys(
        (jv_master or [])
        + [v for lst in display_df["jv"] for v in lst]
    ))
    display_df["is_completed"] = False

    col_config = {
        "contract_no":        st.column_config.TextColumn(
                                  "契約NO", disabled=True, width="small"),
        "seller":             st.column_config.TextColumn("販売店", disabled=True),
        "secondary_seller":   st.column_config.TextColumn("二次店", disabled=True),
        "general_contractor": st.column_config.TextColumn("ゼネコン", disabled=True),
        "field_name":         st.column_config.TextColumn(
                                  "現場名", disabled=True, width="large"),
        "contract_date":      st.column_config.DateColumn(
                                  "契約日", format="YYYY/MM/DD"),
        "start_date":         st.column_config.DateColumn(
                                  "着工日", format="YYYY/MM/DD"),
        "jv":                 st.column_config.MultiselectColumn(
                                  "JV", options=jv_choices, width="medium",
                                  help="セルをクリックすると複数選択できます。"
                                       "選択肢の追加はデータ管理ページのJVマスタから"),
        "contract_qty":       st.column_config.NumberColumn(
                                  "契約数量（m³）", min_value=0, step=0.5),
        "shipped_qty":        st.column_config.NumberColumn(
                                  "出荷実績（m³）", format="%g", disabled=True),
        "remaining_qty":      st.column_config.NumberColumn(
                                  "契約残（m³）", disabled=True),
        "unit_price":         st.column_config.NumberColumn(
                                  "単価（円/m³）", min_value=0, step=100),
        "memo":               st.column_config.TextColumn("備考", width="medium"),
        "is_completed":       st.column_config.CheckboxColumn("✅ 完了"),
    }

    editor_key = f"editor_{f_query}_{f_seller}_{f_secondary}_{f_contractor}_{f_field}"

    st.data_editor(
        display_df,
        column_config=col_config,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=editor_key,
    )

    editor_state = st.session_state.get(editor_key, {})
    edited_rows  = editor_state.get("edited_rows", {})

    # 完了チェック済み行を収集 → まとめて削除ボタン
    checked_for_delete = [
        {
            "contract_no": str(display_df.iloc[int(idx)]["contract_no"]),
            "field_name":  str(display_df.iloc[int(idx)]["field_name"]),
        }
        for idx, changes in edited_rows.items()
        if changes.get("is_completed")
    ]
    if checked_for_delete:
        st.warning(f"⚠️ **{len(checked_for_delete)}件** が完了チェックされています")
        if st.button(
            f"🗑️ 完了現場を削除（{len(checked_for_delete)}件）",
            type="primary", key="btn_batch_delete",
        ):
            st.session_state["pending_delete"] = checked_for_delete
            st.rerun()

    # 数量・備考・日付・単価・JVの自動保存（初期値と同値なら保存しない＝二重保存防止）
    EDITABLE = ("contract_qty", "memo", "contract_date", "start_date",
                "unit_price", "jv")
    save_edits = {}
    for _idx, _changes in edited_rows.items():
        _row = display_df.iloc[int(_idx)]
        _real = {
            k: v for k, v in _changes.items()
            if k in EDITABLE and not _vals_equal(k, v, _row.get(k))
        }
        if _real:
            save_edits[_idx] = _real
    if save_edits:
        _handle_save(display_df, save_edits, supabase)


# ── Page 1: 契約残一覧 ─────────────────────────────────────────────────────────
def page_contracts() -> None:
    st.header("契約残一覧")
    supabase = get_supabase()

    # ── 削除確認ダイアログ（優先表示） ──────────────────────────────────────
    if "pending_delete" in st.session_state:
        pending: list[dict] = st.session_state["pending_delete"]
        lines = "\n\n".join(
            [f"・契約NO {r['contract_no']}　{r['field_name']}" for r in pending]
        )
        st.warning(
            f"⚠️ 以下 **{len(pending)}件** を完了・削除します。  \n"
            f"この操作は取り消せません。\n\n{lines}"
        )
        col1, col2, _ = st.columns([1, 1, 5])
        with col1:
            if st.button("🗑️ 削除する", type="primary", key="btn_confirm_delete"):
                for r in pending:
                    supabase.table("contracts").delete().eq(
                        "contract_no", r["contract_no"]
                    ).execute()
                count = len(pending)
                st.session_state.pop("pending_delete", None)
                load_data.clear()
                st.success(f"✅ {count}件を削除しました")
                st.rerun()
        with col2:
            if st.button("キャンセル", key="btn_cancel_delete"):
                st.session_state.pop("pending_delete", None)
                st.rerun()
        return  # 確認中はテーブルを表示しない

    # ── データ読込 ────────────────────────────────────────────────────────────
    df = load_data()

    # ── 検索（1つの窓で全項目を横断検索） ────────────────────────────────────
    f_query = st.text_input(
        "🔍 検索",
        key="f_query",
        placeholder="販売店・二次店・ゼネコン・現場名・契約NO・備考 から検索",
    )
    with st.expander("詳細検索（項目別に絞り込み）"):
        fc1, fc2 = st.columns(2)
        with fc1:
            f_seller     = st.text_input("販売店",  key="f_seller")
            f_contractor = st.text_input("ゼネコン", key="f_contractor")
        with fc2:
            f_secondary  = st.text_input("二次店",  key="f_secondary")
            f_field      = st.text_input("現場名",  key="f_field")

    # フィルタ適用
    filtered = df.copy()
    if f_query:
        search_cols = [
            "seller", "secondary_seller", "general_contractor",
            "field_name", "contract_no", "memo",
        ]
        mask = pd.Series(False, index=filtered.index)
        for col in search_cols:
            if col in filtered.columns:
                mask |= (
                    filtered[col].astype(str)
                    .str.contains(f_query, case=False, na=False)
                )
        filtered = filtered[mask]
    if f_seller:
        filtered = filtered[
            filtered["seller"].str.contains(f_seller, case=False, na=False)
        ]
    if f_secondary:
        filtered = filtered[
            filtered["secondary_seller"].str.contains(f_secondary, case=False, na=False)
        ]
    if f_contractor:
        filtered = filtered[
            filtered["general_contractor"].str.contains(f_contractor, case=False, na=False)
        ]
    if f_field:
        filtered = filtered[
            filtered["field_name"].str.contains(f_field, case=False, na=False)
        ]

    # デフォルトソート：二次店
    filtered = (
        filtered
        .sort_values("secondary_seller", ascending=True, na_position="last")
        .reset_index(drop=True)
    )

    # ── KPIカード（絞り込みに連動） ──────────────────────────────────────────
    total_count = len(df)
    is_filtering = len(filtered) != total_count
    suffix = "（絞り込み）" if is_filtering else ""
    qty_series = (
        filtered["contract_qty"].dropna() if not filtered.empty
        else pd.Series(dtype=float)
    )
    rem_series = (
        filtered["remaining_qty"].dropna() if not filtered.empty
        else pd.Series(dtype=float)
    )

    _price = pd.to_numeric(filtered["unit_price"], errors="coerce") \
        if not filtered.empty else pd.Series(dtype=float)
    _rem = pd.to_numeric(filtered["remaining_qty"], errors="coerce") \
        if not filtered.empty else pd.Series(dtype=float)
    sales_series = (_price * _rem).dropna()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"契約件数{suffix}", f"{len(filtered):,} 件")
    c2.metric(
        f"契約数量合計{suffix}",
        f"{qty_series.sum():,.1f} m³" if len(qty_series) > 0 else "－",
    )
    c3.metric(
        f"契約残合計{suffix}",
        f"{rem_series.sum():,.1f} m³" if len(rem_series) > 0 else "－",
    )
    c4.metric(
        f"見込み売上{suffix}",
        f"{sales_series.sum():,.0f} 円" if len(sales_series) > 0 else "－",
        help="単価 × 契約残の合計（単価が入力されている現場のみ）",
    )

    st.markdown("---")

    # ── テーブル表示用 DataFrame ─────────────────────────────────────────────
    memo_col = "memo" if "memo" in filtered.columns else None
    display_cols = [
        "contract_no", "seller", "secondary_seller", "general_contractor",
        "field_name", "contract_date", "start_date", "jv",
        "contract_qty", "shipped_qty", "remaining_qty", "unit_price",
    ]
    if memo_col:
        display_cols.append("memo")
    display_df = filtered[display_cols].copy()
    # NaN を pandas nullable Float64 に変換（Arrow シリアライズ時の "None" 表示を防ぐ）
    for col in ("contract_qty", "remaining_qty", "unit_price"):
        if col in display_df.columns:
            display_df[col] = pd.to_numeric(
                display_df[col], errors="coerce"
            ).astype("Float64")
    # 日付列はカレンダー編集できるよう date 型に変換
    for col in ("contract_date", "start_date"):
        display_df[col] = pd.to_datetime(
            display_df[col], errors="coerce"
        ).dt.date
    # JV はセル内で複数選択できるようリスト型に変換
    display_df["jv"] = display_df["jv"].map(_jv_to_list)
    jv_master = load_jv_options()
    # マスタ未作成でも、設定済みの値は選択肢に出す
    jv_choices = list(dict.fromkeys(
        (jv_master or [])
        + [v for lst in display_df["jv"] for v in lst]
    ))
    display_df["is_completed"] = False

    col_config = {
        "contract_no":        st.column_config.TextColumn(
                                  "契約NO", disabled=True, width="small"),
        "seller":             st.column_config.TextColumn("販売店", disabled=True),
        "secondary_seller":   st.column_config.TextColumn("二次店", disabled=True),
        "general_contractor": st.column_config.TextColumn("ゼネコン", disabled=True),
        "field_name":         st.column_config.TextColumn(
                                  "現場名", disabled=True, width="large"),
        "contract_date":      st.column_config.DateColumn(
                                  "契約日", format="YYYY/MM/DD"),
        "start_date":         st.column_config.DateColumn(
                                  "着工日", format="YYYY/MM/DD"),
        "jv":                 st.column_config.MultiselectColumn(
                                  "JV", options=jv_choices, width="medium",
                                  help="セルをクリックすると複数選択できます。"
                                       "選択肢の追加はデータ管理ページのJVマスタから"),
        "contract_qty":       st.column_config.NumberColumn(
                                  "契約数量（m³）", min_value=0, step=0.5),
        "shipped_qty":        st.column_config.NumberColumn(
                                  "出荷実績（m³）", format="%g", disabled=True),
        "remaining_qty":      st.column_config.NumberColumn(
                                  "契約残（m³）", disabled=True),
        "unit_price":         st.column_config.NumberColumn(
                                  "単価（円/m³）", min_value=0, step=100),
        "memo":               st.column_config.TextColumn("備考", width="medium"),
        "is_completed":       st.column_config.CheckboxColumn("✅ 完了"),
    }

    editor_key = f"editor_{f_query}_{f_seller}_{f_secondary}_{f_contractor}_{f_field}"

    cap_col, sort_col, dash_col, pr_col, dl_col = st.columns(
        [1.9, 1.6, 1.1, 1.0, 1.0], vertical_alignment="bottom"
    )
    cap_col.caption(f"表示中 **{len(filtered):,} 件** ／ 全 {total_count:,} 件")
    sort_label = sort_col.selectbox(
        "🖨️ 並び順（画面・印刷・CSV 共通）",
        list(SORT_OPTIONS),
        key="sort_order",
        help="ここで選んだ並び順が、画面の表だけでなく印刷した紙とCSV出力にもそのまま使われます",
    )
    sorted_df = _apply_sort(filtered, sort_label)
    if not df.empty:
        with dash_col:
            components.html(
                _dashboard_component_html(df, load_monthly_shipments()),
                height=44,
            )
    if not filtered.empty:
        with pr_col:
            components.html(
                _print_component_html(sorted_df, total_count, sort_label),
                height=44,
            )
        dl_col.download_button(
            "📥 CSV出力",
            data=_filtered_to_csv(sorted_df),
            file_name=f"契約残一覧_{datetime.now():%Y%m%d}.csv",
            mime="text/csv",
            key="btn_csv_download",
        )

    if df.empty:
        st.info("データがありません。まずCSV取込を行ってください。")
        st.markdown("---")
        _show_add_form(df, supabase)
        return

    # データエディタ（常時編集可・フラグメントで保存後のスクロール位置を保持）
    _data_editor_fragment(supabase)

    # ── 手動追加フォーム ──────────────────────────────────────────────────────
    st.markdown("---")
    _show_add_form(df, supabase)


# ── 並び替え（画面・印刷・CSVの3つに共通で効く） ───────────────────────────────
SORT_OPTIONS: dict[str, tuple[str, bool]] = {
    "契約残が多い順":     ("remaining_qty", False),
    "契約残が少ない順":   ("remaining_qty", True),
    "消化率が低い順":     ("_sort_pct", True),
    "消化率が高い順":     ("_sort_pct", False),
    "超過が多い順":       ("_sort_over", False),
    "見込み売上が多い順": ("_sort_sales", False),
    "契約日が新しい順":   ("contract_date", False),
    "二次店順":           ("secondary_seller", True),
    "販売店順":           ("seller", True),
    "ゼネコン順":         ("general_contractor", True),
    "現場名順":           ("field_name", True),
}


def _apply_sort(filtered: pd.DataFrame, sort_label: str) -> pd.DataFrame:
    """選択された並び順を適用して返す"""
    df = filtered.copy()
    qty = pd.to_numeric(df["contract_qty"], errors="coerce")
    shipped = pd.to_numeric(df["shipped_qty"], errors="coerce")
    rem = pd.to_numeric(df["remaining_qty"], errors="coerce")
    price = pd.to_numeric(df["unit_price"], errors="coerce")
    df["_sort_pct"] = (shipped / qty).where(qty > 0)
    df["_sort_over"] = (shipped - qty).where(qty > 0).clip(lower=0)
    df["_sort_sales"] = price * rem

    col, asc = SORT_OPTIONS.get(sort_label, ("remaining_qty", False))
    df = df.sort_values(col, ascending=asc, na_position="last")
    return df.drop(
        columns=["_sort_pct", "_sort_over", "_sort_sales"]
    ).reset_index(drop=True)


def _filtered_to_csv(filtered: pd.DataFrame) -> bytes:
    """絞り込み結果を Excel でそのまま開ける CSV（CP932）に変換する"""
    out = filtered.copy()
    qty = pd.to_numeric(out["contract_qty"], errors="coerce")
    shipped = pd.to_numeric(out["shipped_qty"], errors="coerce")
    out["consumption_pct"] = (shipped / qty * 100).where(qty > 0).round(1)
    out["overage_qty"] = (shipped - qty).where(qty > 0).clip(lower=0).round(1)

    cols = {
        "contract_no":        "契約NO",
        "seller":             "販売店",
        "secondary_seller":   "二次店",
        "general_contractor": "ゼネコン",
        "field_name":         "現場名",
        "contract_date":      "契約日",
        "start_date":         "着工日",
        "jv":                 "JV",
        "contract_qty":       "契約数量(m3)",
        "shipped_qty":        "出荷実績(m3)",
        "remaining_qty":      "契約残(m3)",
        "overage_qty":        "超過(m3)",
        "consumption_pct":    "消化率(%)",
    }
    if "memo" in out.columns:
        cols["memo"] = "備考"
    out = out[list(cols)].rename(columns=cols)
    return out.to_csv(index=False).encode("cp932", errors="replace")


# ── サマリー印刷（A4縦ダッシュボード） ────────────────────────────────────────
# 白黒印刷でも見分けられる濃淡（濃い→薄い、最後の灰色は「その他」用）
PIE_COLORS = ["#1e3a5f", "#3f6491", "#7a9cc4", "#b3c7de", "#dde7f1", "#c4c4c4"]


def _pie_svg(items: list[tuple[str, float, float]], size: int = 150) -> str:
    """(ラベル, 値, 全体比%) のリストから円グラフSVGを作る"""
    import math

    total = sum(v for _, v, _ in items)
    if total <= 0:
        return "<div style='color:#888;font-size:9pt;'>データなし</div>"

    cx = cy = size / 2
    radius = size / 2 - 4
    paths = []
    angle = -90.0  # 12時の位置から時計回り
    for i, (_, value, _) in enumerate(items):
        frac = value / total
        if frac <= 0:
            continue
        start, end = angle, angle + frac * 360
        angle = end
        color = PIE_COLORS[min(i, len(PIE_COLORS) - 1)]
        if frac >= 0.9999:  # 1社のみの場合は円
            paths.append(
                f"<circle cx='{cx}' cy='{cy}' r='{radius}' fill='{color}' "
                f"stroke='white' stroke-width='1'/>"
            )
            continue
        x1 = cx + radius * math.cos(math.radians(start))
        y1 = cy + radius * math.sin(math.radians(start))
        x2 = cx + radius * math.cos(math.radians(end))
        y2 = cy + radius * math.sin(math.radians(end))
        large = 1 if (end - start) > 180 else 0
        paths.append(
            f"<path d='M{cx},{cy} L{x1:.1f},{y1:.1f} "
            f"A{radius},{radius} 0 {large} 1 {x2:.1f},{y2:.1f} Z' "
            f"fill='{color}' stroke='white' stroke-width='1'/>"
        )
    return (
        f"<svg width='{size}' height='{size}' viewBox='0 0 {size} {size}' "
        f"xmlns='http://www.w3.org/2000/svg'>{''.join(paths)}</svg>"
    )


def _bar_svg(
    months: list[tuple[str, float, float]], width: int = 660, height: int = 170
) -> str:
    """(年月, 出荷量, 新規契約量) のリストから2本棒グラフSVGを作る"""
    if not months:
        return "<div style='color:#888;font-size:9pt;'>データなし</div>"

    max_v = max(max(s, c) for _, s, c in months) or 1
    pad_b, pad_t = 26, 16
    plot_h = height - pad_b - pad_t
    n = len(months)
    slot = width / n
    bar_w = min(slot * 0.36, 36)
    show_values = n <= 9  # 月数が多いと数値ラベルが重なるため省略
    parts = []
    for i, (ym, shipped, contracted) in enumerate(months):
        cx_slot = i * slot + slot / 2
        label = f"{int(ym[2:4])}/{int(ym[5:7])}"  # "26/4"
        for j, (v, color) in enumerate(
            ((shipped, "#3f6491"), (contracted, "#c9d8ea"))
        ):
            h = plot_h * v / max_v
            x = cx_slot - bar_w + j * bar_w
            y = pad_t + plot_h - h
            parts.append(
                f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w:.1f}' "
                f"height='{h:.1f}' fill='{color}' stroke='#777' "
                f"stroke-width='0.5'/>"
            )
            if show_values and v > 0:
                parts.append(
                    f"<text x='{x + bar_w / 2:.1f}' y='{y - 3:.1f}' "
                    f"text-anchor='middle' font-size='8' fill='#333'>"
                    f"{v:,.0f}</text>"
                )
        parts.append(
            f"<text x='{cx_slot:.1f}' y='{height - 10}' text-anchor='middle' "
            f"font-size='10' fill='#333'>{label}</text>"
        )
    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        f"xmlns='http://www.w3.org/2000/svg'>{''.join(parts)}</svg>"
    )


def _ranking_section(
    grouped: pd.DataFrame, metric: str, title: str, note: str
) -> str:
    """二次店TOP5の1セクション（円グラフ＋表）のHTMLを作る"""
    total = float(grouped[metric].sum())
    top = grouped.sort_values(metric, ascending=False).head(5)
    top = top[top[metric] > 0]
    if top.empty or total <= 0:
        return (
            f"<div class='section'><h2>{html_mod.escape(title)}</h2>"
            "<div style='color:#888;font-size:9pt;'>データなし</div></div>"
        )

    others = total - float(top[metric].sum())
    pie_items = [
        (str(name), float(row[metric]), float(row[metric]) / total * 100)
        for name, row in top.iterrows()
    ]
    if others > 0.05:
        pie_items.append(("その他", others, others / total * 100))

    rows_html = []
    for i, (name, value, share) in enumerate(pie_items):
        color = PIE_COLORS[min(i, len(PIE_COLORS) - 1)]
        if name == "その他":
            qty_s = shipped_s = rem_s = "－"
        else:
            g = grouped.loc[name]
            qty_s = f"{g['contract_qty']:,.1f}"
            shipped_s = f"{g['shipped_qty']:,.1f}"
            rem_s = f"{g['remaining_qty']:,.1f}"
        rows_html.append(
            "<tr>"
            f"<td><span class='chip' style='background:{color}'></span>"
            f"{html_mod.escape(name)}</td>"
            f"<td class='r'>{qty_s}</td>"
            f"<td class='r'>{shipped_s}</td>"
            f"<td class='r'>{rem_s}</td>"
            f"<td class='r'><b>{share:.1f}%</b></td>"
            "</tr>"
        )

    return f"""
<div class='section'>
<h2>{html_mod.escape(title)}</h2>
<div class='flexrow'>
<div>{_pie_svg(pie_items)}</div>
<table class='rank'>
<thead><tr><th>二次店</th><th>契約数量<br>(m³)</th><th>出荷実績<br>(m³)</th>
<th>契約残<br>(m³)</th><th>{html_mod.escape(note)}</th></tr></thead>
<tbody>{''.join(rows_html)}</tbody>
</table>
</div>
</div>"""


def _dashboard_component_html(
    df: pd.DataFrame, months: list[tuple[str, float]]
) -> str:
    """サマリー印刷ボタン＋A4縦ダッシュボードのHTML（全データ集計）"""
    qty = pd.to_numeric(df["contract_qty"], errors="coerce")
    shipped = pd.to_numeric(df["shipped_qty"], errors="coerce")
    rem = pd.to_numeric(df["remaining_qty"], errors="coerce")
    over = (shipped - qty).where(qty > 0).clip(lower=0)
    warn_count = int(((qty > 0) & (rem / qty >= 0.5)).sum())
    price = pd.to_numeric(df["unit_price"], errors="coerce")
    sales_total = float((price * rem).dropna().sum())
    sales_note = f"（約{sales_total / 10000:,.0f}万円）" if sales_total > 0 else ""

    kpis = [
        ("契約件数", f"{len(df):,} 件"),
        ("契約数量合計", f"{qty.sum():,.1f} m³"),
        ("出荷実績合計", f"{shipped.sum():,.1f} m³"),
        ("契約残合計", f"{rem.sum():,.1f} m³"),
        ("超過出荷合計", f"{over.sum():,.1f} m³"),
        ("要注意現場（残50%以上）", f"{warn_count:,} 件"),
        ("見込み売上合計（単価×契約残）", f"{sales_total:,.0f} 円{sales_note}"),
    ]
    kpi_html = "".join(
        f"<div class='kpi'><div class='kpi-label'>{html_mod.escape(k)}</div>"
        f"<div class='kpi-value'>{html_mod.escape(v)}</div></div>"
        for k, v in kpis
    )

    # 二次店別の集計（空欄は「（二次店なし）」に寄せる）
    g = df.copy()
    g["secondary_seller"] = (
        g["secondary_seller"].replace("", "（二次店なし）").fillna("（二次店なし）")
    )
    for c in ("contract_qty", "shipped_qty", "remaining_qty"):
        g[c] = pd.to_numeric(g[c], errors="coerce").fillna(0.0)
    grouped = g.groupby("secondary_seller")[
        ["contract_qty", "shipped_qty", "remaining_qty"]
    ].sum()

    sections = (
        _ranking_section(grouped, "remaining_qty",
                         "契約残の多い二次店 TOP5", "契約残全体に占める割合")
        + _ranking_section(grouped, "contract_qty",
                           "契約数量の多い二次店 TOP5", "契約数量全体に占める割合")
        + _ranking_section(grouped, "shipped_qty",
                           "出荷実績の多い二次店 TOP5", "出荷実績全体に占める割合")
    )

    # 月別: 出荷量（出荷日ベース・確定値）＋ 新規契約量（契約日ベース）
    cdates = pd.to_datetime(df["contract_date"], errors="coerce")
    cq = pd.to_numeric(df["contract_qty"], errors="coerce").fillna(0.0)
    contracted_by_month = (
        pd.DataFrame({"ym": cdates.dt.strftime("%Y-%m"), "q": cq})
        .dropna(subset=["ym"])
        .groupby("ym")["q"].sum().to_dict()
    )
    no_date_count = int((cdates.isna() & (cq > 0)).sum())
    shipped_map = dict(months)
    all_ym = sorted(set(shipped_map) | set(contracted_by_month))
    combined = [
        (ym, float(shipped_map.get(ym, 0.0)),
         float(contracted_by_month.get(ym, 0.0)))
        for ym in all_ym
    ]
    date_note = (
        f"※契約日未入力の契約（数量あり {no_date_count:,}件）は新規契約量に含まれません"
        if no_date_count > 0 else ""
    )

    printed_at = f"{datetime.now():%Y/%m/%d %H:%M}"

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<style>
@page {{ size: A4 portrait; margin: 12mm; }}
* {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin: 0; font-family: "Yu Gothic", "Meiryo", sans-serif; }}
#printable {{ display: none; }}
#printbtn {{
    width: 100%; padding: 0.5rem 0.75rem; cursor: pointer;
    border: 1px solid rgba(49, 51, 63, 0.2); border-radius: 0.5rem;
    background: white; font-size: 14px; font-family: inherit;
}}
#printbtn:hover {{ border-color: #1e3a5f; color: #1e3a5f; }}
@media print {{
    #printbtn {{ display: none; }}
    #printable {{ display: block; }}
}}
h1 {{ font-size: 16pt; margin: 0 0 1mm; }}
h2 {{ font-size: 11pt; margin: 0 0 2mm; border-left: 4px solid #1e3a5f; padding-left: 2mm; }}
.meta {{ font-size: 9pt; color: #555; margin-bottom: 4mm; }}
.kpis {{ display: flex; flex-wrap: wrap; gap: 2mm; margin-bottom: 5mm; }}
.kpi {{
    flex: 1 1 30%; border: 0.5pt solid #bbb; border-radius: 2mm;
    padding: 2mm 3mm; background: #f4f6f9;
}}
.kpi-label {{ font-size: 8pt; color: #555; }}
.kpi-value {{ font-size: 13pt; font-weight: bold; }}
.section {{ margin-bottom: 5mm; page-break-inside: avoid; }}
.flexrow {{ display: flex; gap: 5mm; align-items: center; }}
table.rank {{ border-collapse: collapse; font-size: 8.5pt; flex: 1; }}
table.rank th, table.rank td {{ border: 0.3pt solid #999; padding: 1mm 1.5mm; }}
table.rank th {{ background: #e8ecf0; text-align: center; }}
table.rank td.r {{ text-align: right; }}
.chip {{
    display: inline-block; width: 8pt; height: 8pt;
    border: 0.3pt solid #777; margin-right: 1.5mm; vertical-align: middle;
}}
</style></head>
<body>
<button id="printbtn" onclick="window.print()">📊 サマリー印刷</button>
<div id="printable">
<h1>生コン契約残サマリー</h1>
<div class="meta">印刷日時：{printed_at}　／　全データ集計（絞り込みの影響を受けません）</div>
<div class="kpis">{kpi_html}</div>
{sections}
<div class="section">
<h2>月別推移（m³）</h2>
<div style="font-size:8.5pt; margin-bottom:1.5mm;">
<span class='chip' style='background:#3f6491'></span>出荷量（出荷日ベース）
<span class='chip' style='background:#c9d8ea'></span>新規契約量（契約日ベース）
<span style="color:#777; margin-left:3mm;">{date_note}</span>
</div>
{_bar_svg(combined)}
</div>
</div>
</body></html>"""


def _print_component_html(
    filtered: pd.DataFrame, total_count: int, sort_label: str
) -> str:
    """印刷ボタン＋印刷用テーブルを含むHTML。
    画面上はボタンだけ見え、印刷時はテーブルだけが紙に出る。
    並び順は呼び出し元で適用済みのものをそのまま使う。"""
    df = filtered.copy()
    qty = pd.to_numeric(df["contract_qty"], errors="coerce")
    shipped = pd.to_numeric(df["shipped_qty"], errors="coerce")
    rem = pd.to_numeric(df["remaining_qty"], errors="coerce")
    df["_pct"] = (shipped / qty * 100).where(qty > 0)
    df["_over"] = (shipped - qty).where(qty > 0).clip(lower=0)
    df["_warn"] = (qty > 0) & (rem / qty >= 0.5)

    def esc(v) -> str:
        s = "" if v is None else str(v)
        return html_mod.escape("" if s in ("nan", "None") else s)

    def num(v) -> str:
        v = pd.to_numeric(v, errors="coerce")
        return "" if pd.isna(v) else f"{float(v):,.1f}"

    def over(v) -> str:
        v = pd.to_numeric(v, errors="coerce")
        return "" if pd.isna(v) or float(v) <= 0 else f"{float(v):,.1f}"

    def pct(v) -> str:
        v = pd.to_numeric(v, errors="coerce")
        return "" if pd.isna(v) else f"{float(v):.0f}%"

    body_rows = []
    for _, r in df.iterrows():
        cls = ' class="warn"' if bool(r["_warn"]) else ""
        body_rows.append(
            f"<tr{cls}>"
            f"<td>{esc(r['contract_no'])}</td>"
            f"<td>{esc(r['seller'])}</td>"
            f"<td>{esc(r['secondary_seller'])}</td>"
            f"<td>{esc(r['general_contractor'])}</td>"
            f"<td class='l'>{esc(r['field_name'])}</td>"
            f"<td class='r'>{num(r['contract_qty'])}</td>"
            f"<td class='r'>{num(r['shipped_qty'])}</td>"
            f"<td class='r'>{num(r['remaining_qty'])}</td>"
            f"<td class='r'>{over(r['_over'])}</td>"
            f"<td class='r'>{pct(r['_pct'])}</td>"
            f"<td class='l'>{esc(r.get('memo', ''))}</td>"
            "</tr>"
        )

    total_row = (
        "<tr class='total'>"
        "<td colspan='5'>合計</td>"
        f"<td class='r'>{num(qty.sum())}</td>"
        f"<td class='r'>{num(shipped.sum())}</td>"
        f"<td class='r'>{num(rem.sum())}</td>"
        f"<td class='r'>{over(df['_over'].sum())}</td>"
        "<td></td><td></td>"
        "</tr>"
    )

    printed_at = f"{datetime.now():%Y/%m/%d %H:%M}"
    count_note = f"{len(df):,} 件"
    if len(df) != total_count:
        count_note += f"（全 {total_count:,} 件から絞り込み）"
    count_note += f"　／　並び順：{html_mod.escape(sort_label)}"

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<style>
@page {{ size: A4 landscape; margin: 10mm; }}
* {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin: 0; font-family: "Yu Gothic", "Meiryo", sans-serif; }}
#printable {{ display: none; }}
#printbtn {{
    width: 100%; padding: 0.5rem 0.75rem; cursor: pointer;
    border: 1px solid rgba(49, 51, 63, 0.2); border-radius: 0.5rem;
    background: white; font-size: 14px; font-family: inherit;
}}
#printbtn:hover {{ border-color: #1e3a5f; color: #1e3a5f; }}
@media print {{
    #printbtn {{ display: none; }}
    #printable {{ display: block; }}
}}
h1 {{ font-size: 14pt; margin: 0 0 2mm; }}
.meta {{ font-size: 9pt; margin-bottom: 3mm; }}
.legend {{ background: #fff3cd; padding: 0 2mm; }}
table {{ border-collapse: collapse; width: 100%; font-size: 8.5pt; }}
thead {{ display: table-header-group; }}
th, td {{ border: 0.3pt solid #999; padding: 1mm 1.5mm; text-align: center; }}
th {{ background: #e8ecf0; font-weight: bold; }}
td.r {{ text-align: right; }}
td.l {{ text-align: left; }}
tr.warn td {{ background: #fff3cd; }}
tr.total td {{ background: #e8ecf0; font-weight: bold; text-align: right; }}
tr {{ page-break-inside: avoid; }}
</style></head>
<body>
<button id="printbtn" onclick="window.print()">🖨️ 一覧印刷</button>
<div id="printable">
<h1>生コン契約残一覧</h1>
<div class="meta">
印刷日時：{printed_at}　／　{count_note}　／
<span class="legend">黄色の行＝契約残が契約数量の50%以上（出荷が進んでいない現場）</span>
</div>
<table>
<thead><tr>
<th>契約NO</th><th>販売店</th><th>二次店</th><th>ゼネコン</th><th>現場名</th>
<th>契約数量<br>(m³)</th><th>出荷実績<br>(m³)</th><th>契約残<br>(m³)</th>
<th>超過<br>(m³)</th><th>消化率</th><th>備考</th>
</tr></thead>
<tbody>
{''.join(body_rows)}
{total_row}
</tbody>
</table>
</div>
</body></html>"""


def _render_view_table(filtered: pd.DataFrame, supabase: Client) -> None:
    """閲覧モード：色強調・消化率バー・並び替え可能なテーブル。
    行をクリックすると詳細編集ダイアログ（JV・日付・単価）が開く。"""
    view_df = filtered.copy()

    # 消化率（%）。契約数量が未設定・0 の行は計算不可なので空欄
    def _calc_pct(row):
        qty = row["contract_qty"]
        if pd.isna(qty) or float(qty) <= 0:
            return None
        return float(row["shipped_qty"]) / float(qty) * 100.0

    view_df["consumption_pct"] = view_df.apply(_calc_pct, axis=1)

    # 超過出荷量（契約数量を超えて出荷した分）。現場単位の確認用で、
    # 契約残の合計には影響させない（契約残は0で打ち止めのまま）
    _qty = pd.to_numeric(view_df["contract_qty"], errors="coerce")
    _shipped = pd.to_numeric(view_df["shipped_qty"], errors="coerce")
    view_df["overage_qty"] = (_shipped - _qty).where(_qty > 0).clip(lower=0)

    # 見込み売上 = 単価 × 契約残
    _price = pd.to_numeric(view_df["unit_price"], errors="coerce")
    _rem = pd.to_numeric(view_df["remaining_qty"], errors="coerce")
    view_df["expected_sales"] = _price * _rem

    view_cols = [
        "contract_no", "seller", "secondary_seller", "general_contractor",
        "field_name", "contract_date", "start_date", "jv",
        "contract_qty", "shipped_qty", "remaining_qty",
        "overage_qty", "consumption_pct", "unit_price", "expected_sales",
    ]
    if "memo" in view_df.columns:
        view_cols.append("memo")
    view_df = view_df[view_cols]

    # 日付は "2026/06/11" 形式の文字列に（未入力は空欄）
    def _fmt_date(v):
        s = "" if v is None else str(v)
        return "" if s in ("", "nan", "None", "NaT") else s[:10].replace("-", "/")

    for c in ("contract_date", "start_date"):
        view_df[c] = view_df[c].map(_fmt_date)

    # 金額（円・桁区切り、未入力は空欄）
    def _fmt_yen(v):
        v = pd.to_numeric(v, errors="coerce")
        return "" if pd.isna(v) else f"{float(v):,.0f}"

    view_df["unit_price"] = view_df["unit_price"].map(_fmt_yen)
    view_df["expected_sales"] = view_df["expected_sales"].map(_fmt_yen)

    view_df["consumption_pct"] = pd.to_numeric(
        view_df["consumption_pct"], errors="coerce"
    ).astype(float)

    # 残り50%以上（＝出荷が進んでいない要注意現場）を黄色でハイライト
    # ※数値列を文字列化する前に判定用の値を保持しておく
    qty_num = pd.to_numeric(view_df["contract_qty"], errors="coerce")
    rem_num = pd.to_numeric(view_df["remaining_qty"], errors="coerce")
    warn_mask = (qty_num > 0) & (rem_num / qty_num >= 0.5)

    # 数値列は文字列化する。st.dataframe は数値列の欠損を灰色の "None" と
    # 描画してしまうため、空欄にするには文字列にするしかない。
    # 右詰め固定幅・固定小数点なら、文字列の列でも見出しクリックの
    # 並び替え結果が数値順と一致する。
    def _fmt_qty(v):
        v = pd.to_numeric(v, errors="coerce")
        return "" if pd.isna(v) else f"{float(v):>10,.1f}"

    def _fmt_overage(v):
        v = pd.to_numeric(v, errors="coerce")
        if pd.isna(v) or float(v) <= 0:
            return ""  # 超過なしは空欄
        return f"{float(v):>10,.1f}"

    for c in ("contract_qty", "shipped_qty", "remaining_qty"):
        view_df[c] = view_df[c].map(_fmt_qty)
    view_df["overage_qty"] = view_df["overage_qty"].map(_fmt_overage)

    def _row_style(row):
        if warn_mask.iloc[row.name]:
            return ["background-color: #fff3cd; color: #5c4a00;"] * len(row)
        return [""] * len(row)

    styler = view_df.style.apply(_row_style, axis=1)

    # 行クリックで詳細編集を開けるよう、選択イベントを有効化。
    # 保存後に選択状態をリセットするため key にノンスを含める
    nonce = st.session_state.setdefault("view_nonce", 0)
    event = st.dataframe(
        styler,
        column_config={
            "contract_no":        st.column_config.TextColumn("契約NO", width="small"),
            "seller":             st.column_config.TextColumn("販売店", width="medium"),
            "secondary_seller":   st.column_config.TextColumn("二次店", width="small"),
            "general_contractor": st.column_config.TextColumn("ゼネコン", width="medium"),
            "field_name":         st.column_config.TextColumn("現場名", width="large"),
            "contract_date":      st.column_config.TextColumn("契約日", width="small"),
            "start_date":         st.column_config.TextColumn("着工日", width="small"),
            "jv":                 st.column_config.TextColumn("JV", width="small"),
            "contract_qty":       st.column_config.TextColumn(
                                      "契約数量（m³）", width="small", alignment="right"),
            "shipped_qty":        st.column_config.TextColumn(
                                      "出荷実績（m³）", width="small", alignment="right"),
            "remaining_qty":      st.column_config.TextColumn(
                                      "契約残（m³）", width="small", alignment="right"),
            "overage_qty":        st.column_config.TextColumn(
                                      "超過（m³）", width="small", alignment="right",
                                      help="契約数量を超えて出荷した分（請求対象）。契約残合計には影響しません"),
            "consumption_pct":    st.column_config.ProgressColumn(
                                      "消化率", format="%.0f%%",
                                      min_value=0, max_value=100),
            "unit_price":         st.column_config.TextColumn(
                                      "単価（円/m³）", width="small", alignment="right"),
            "expected_sales":     st.column_config.TextColumn(
                                      "見込み売上（円）", width="small", alignment="right",
                                      help="単価 × 契約残。単価が未入力の現場は空欄"),
            "memo":               st.column_config.TextColumn("備考", width="medium"),
        },
        use_container_width=True,
        hide_index=True,
        height=600,
        key=f"view_table_{nonce}",
        on_select="rerun",
        selection_mode="single-row",
    )
    st.caption(
        "🟨 黄色の行＝契約残が50%以上（出荷が進んでいない要注意現場）　／　"
        "行をクリックすると詳細編集（JV・契約日・着工日・単価）が開きます　／　"
        "列見出しクリックで並び替え"
    )

    # 行が選択されたら詳細編集ダイアログを開く（同じ選択での再オープンは防ぐ）
    sel_rows = event.selection.rows if event and event.selection else []
    if sel_rows:
        sel_id = (nonce, sel_rows[0])
        if st.session_state.get("detail_handled") != sel_id:
            st.session_state["detail_handled"] = sel_id
            _detail_dialog(filtered.iloc[sel_rows[0]].to_dict(), supabase)
    else:
        # 選択解除されたらガードもリセット（同じ行をもう一度開けるように）
        st.session_state.pop("detail_handled", None)


def _handle_save(
    display_df: pd.DataFrame,
    edited_rows: dict,
    supabase: Client,
) -> None:
    """契約数量・備考の変更を自動保存する"""
    to_update: list[dict] = []

    for row_idx_str, changes in edited_rows.items():
        row_idx    = int(row_idx_str)
        contract_no = str(display_df.iloc[row_idx]["contract_no"])
        payload: dict = {}

        if "contract_qty" in changes:
            new_qty = changes["contract_qty"]
            is_null = new_qty is None or (isinstance(new_qty, float) and pd.isna(new_qty))
            payload["contract_qty"] = None if is_null else float(new_qty)

        if "memo" in changes:
            payload["memo"] = changes["memo"] or ""

        if "unit_price" in changes:
            v = changes["unit_price"]
            is_null = v is None or (isinstance(v, float) and pd.isna(v))
            payload["unit_price"] = None if is_null else float(v)

        if "jv" in changes:
            v = changes["jv"]
            payload["jv"] = ",".join(v) if isinstance(v, list) else (v or "")

        for date_col in ("contract_date", "start_date"):
            if date_col in changes:
                v = changes[date_col]
                payload[date_col] = str(v)[:10] if v else None

        if payload:
            to_update.append({"contract_no": contract_no, **payload})

    if to_update:
        errors = []
        for r in to_update:
            cn      = r["contract_no"]
            payload = {k: v for k, v in r.items() if k != "contract_no"}
            try:
                supabase.table("contracts").update(payload) \
                    .eq("contract_no", cn).execute()
            except Exception as e:
                errors.append(f"契約NO {cn}: {e}")
        load_data.clear()
        if errors:
            st.error(
                "⚠️ 保存に失敗した行があります。少し時間をおいて、"
                "もう一度入力し直してください。\n\n" + "\n\n".join(errors)
            )
        else:
            st.toast("✅ 保存しました")


@st.dialog("契約の詳細編集")
def _detail_dialog(row: dict, supabase: Client) -> None:
    """JV（複数選択）・契約日・着工日・単価をまとめて編集するダイアログ"""
    st.markdown(f"**{row['field_name']}**　（契約NO: {row['contract_no']}）")

    jv_master = load_jv_options()
    current_jv = _jv_to_list(row.get("jv"))
    if jv_master is None:
        st.info(
            "JV選択肢マスタが未作成です。migration_add_contract_fields.sql を "
            "Supabase の SQL Editor で実行すると選択肢が使えます。"
        )
        jv_choices = current_jv
    else:
        # マスタ＋（マスタから消えたが設定済みの値）を重複なしで
        jv_choices = list(dict.fromkeys(jv_master + current_jv))

    new_jv = st.multiselect("JV（複数選択可）", jv_choices, default=current_jv)

    def _to_date(v):
        s = "" if v is None else str(v)
        if s in ("", "nan", "None", "NaT"):
            return None
        try:
            return dateutil_parser.parse(s).date()
        except Exception:
            return None

    new_cdate = st.date_input(
        "契約日", value=_to_date(row.get("contract_date")), format="YYYY/MM/DD"
    )
    new_sdate = st.date_input(
        "着工日", value=_to_date(row.get("start_date")), format="YYYY/MM/DD"
    )

    price_raw = pd.to_numeric(row.get("unit_price"), errors="coerce")
    new_price = st.number_input(
        "単価（円/m³）",
        min_value=0.0, step=100.0,
        value=float(price_raw) if pd.notna(price_raw) else None,
        placeholder="未入力",
    )

    if st.button("💾 保存する", type="primary", use_container_width=True):
        try:
            supabase.table("contracts").update({
                "jv":            ",".join(new_jv),
                "contract_date": str(new_cdate) if new_cdate else None,
                "start_date":    str(new_sdate) if new_sdate else None,
                "unit_price":    float(new_price) if new_price is not None else None,
            }).eq("contract_no", str(row["contract_no"])).execute()
        except Exception as e:
            st.error(
                "⚠️ 保存に失敗しました。少し時間をおいて、"
                f"もう一度お試しください。\n\n{e}"
            )
            return
        load_data.clear()
        # 行選択をリセットしてからリラン（しないと保存後にダイアログが再オープンする）
        st.session_state["view_nonce"] = st.session_state.get("view_nonce", 0) + 1
        st.session_state.pop("detail_handled", None)
        st.session_state["autosaved_count"] = 1
        st.rerun()


def _show_add_form(existing_df: pd.DataFrame, supabase: Client) -> None:
    """手動契約追加フォーム"""
    existing_nos = (
        set(existing_df["contract_no"].astype(str).tolist())
        if not existing_df.empty
        else set()
    )

    with st.expander("➕ 新規契約を手動登録"):
        a1, a2 = st.columns(2)
        with a1:
            new_no         = st.text_input("契約NO *",       key="add_no")
            new_seller     = st.text_input("販売店",         key="add_seller")
            new_contractor = st.text_input("ゼネコン",       key="add_contractor")
            new_cdate      = st.date_input("契約日", value=None,
                                           format="YYYY/MM/DD", key="add_cdate")
            new_price_str  = st.text_input("単価（円/m³）",  key="add_price")
        with a2:
            new_field      = st.text_input("現場名 *",       key="add_field")
            new_secondary  = st.text_input("二次店",         key="add_secondary")
            new_qty_str    = st.text_input("契約数量（m³）", key="add_qty")
            new_sdate      = st.date_input("着工日", value=None,
                                           format="YYYY/MM/DD", key="add_sdate")
            jv_master      = load_jv_options() or []
            new_jv         = st.multiselect("JV（複数選択可）", jv_master,
                                            key="add_jv")

        if st.button("＋ 追加する", type="primary", key="btn_add"):
            if not new_no.strip():
                st.error("契約NOは必須です")
                return
            if not new_field.strip():
                st.error("現場名は必須です")
                return
            if new_no.strip() in existing_nos:
                st.error(f"契約NO「{new_no.strip()}」は既に登録済みです")
                return

            contract_qty = None
            if new_qty_str.strip():
                try:
                    contract_qty = float(new_qty_str.strip())
                except ValueError:
                    st.error("契約数量は数値で入力してください")
                    return

            unit_price = None
            if new_price_str.strip():
                try:
                    unit_price = float(new_price_str.strip().replace(",", ""))
                except ValueError:
                    st.error("単価は数値で入力してください")
                    return

            payload = {
                "contract_no":        new_no.strip(),
                "field_name":         new_field.strip(),
                "seller":             new_seller.strip() or None,
                "secondary_seller":   new_secondary.strip() or None,
                "general_contractor": new_contractor.strip() or None,
                "contract_qty":       contract_qty,
            }
            # 新項目はマイグレーション未実行だと列が無いため、値がある時だけ送る
            if new_cdate:
                payload["contract_date"] = str(new_cdate)
            if new_sdate:
                payload["start_date"] = str(new_sdate)
            if new_jv:
                payload["jv"] = ",".join(new_jv)
            if unit_price is not None:
                payload["unit_price"] = unit_price

            supabase.table("contracts").insert(payload).execute()

            st.success(
                f"✅ 「{new_field.strip()}」を追加しました"
                f"（契約NO: {new_no.strip()}）"
            )
            load_data.clear()
            st.rerun()


# ── Page 2: CSV取込 ────────────────────────────────────────────────────────────
def page_csv_import() -> None:
    st.header("CSV取込")

    uploaded_files = st.file_uploader(
        "出荷実績CSVを選択（複数ファイル可・Shift-JIS/CP932対応）",
        type=["csv"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.caption("📎 CSVファイルをドロップ、またはクリックして選択してください")
        return

    st.write(f"**{len(uploaded_files)}ファイル** 選択済み")
    for f in uploaded_files:
        st.caption(f"　✓ {f.name}")

    if not st.button("🚀 取込実行", type="primary", key="btn_import"):
        return

    supabase = get_supabase()
    progress = st.progress(0.0, text="読込中...")
    parse_errors: list[str] = []
    all_deliveries: list[dict] = []
    contracts_info: dict[str, dict] = {}
    batch_rows: list[dict] = []  # 取込履歴（ファイル単位の取消用）

    # ── ファイル読込フェーズ ──────────────────────────────────────────────────
    for i, f in enumerate(uploaded_files):
        progress.progress(
            (i + 0.5) / len(uploaded_files),
            text=f"読込中: {f.name}",
        )
        batch_id = str(uuid.uuid4())
        rows_before = len(all_deliveries)
        raw = f.read()

        content: str | None = None
        for enc in ("cp932", "shift_jis", "utf-8"):
            try:
                content = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            parse_errors.append(
                f"{f.name}: エンコード不明（CP932/Shift-JIS/UTF-8 いずれも失敗）"
            )
            continue

        try:
            df = pd.read_csv(io.StringIO(content))
        except Exception as e:
            parse_errors.append(f"{f.name}: CSV読込失敗 ({e})")
            continue

        df.columns = df.columns.str.strip()

        required_cols = [
            "出荷日", "現場ＮＯ", "出荷伝票番号", "出荷量", "契約ＮＯ",
            "現場名", "施工者名", "販売店略名", "二次店略名",
        ]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            parse_errors.append(f"{f.name}: 列不足 → {missing}")
            continue

        for row_idx, row in df.iterrows():
            line_no = int(row_idx) + 2  # ヘッダー行を除いたCSV上の行番号
            contract_no = str(row["契約ＮＯ"]).strip()
            field_no    = str(row["現場ＮＯ"]).strip()

            # 出荷日パース（"2026/4/1" 形式など）。
            # 読めない行はDBに送らずスキップし、行番号と値を明細で報告する
            raw_date = row["出荷日"]
            try:
                if pd.isna(raw_date) or str(raw_date).strip() == "":
                    raise ValueError("空欄")
                delivery_date = dateutil_parser.parse(
                    str(raw_date)
                ).strftime("%Y-%m-%d")
            except Exception:
                parse_errors.append(
                    f"{f.name} {line_no}行目: 出荷日が読めないためスキップ"
                    f"（値:「{raw_date}」）"
                )
                continue

            # 出荷量も数値チェック（不正値で取込全体が止まるのを防ぐ）
            try:
                qty = float(row["出荷量"]) if pd.notna(row["出荷量"]) else 0.0
            except (ValueError, TypeError):
                parse_errors.append(
                    f"{f.name} {line_no}行目: 出荷量が数値でないためスキップ"
                    f"（値:「{row['出荷量']}」）"
                )
                continue

            # 出荷伝票番号（NaN安全変換）
            v = row["出荷伝票番号"]
            if pd.notna(v) and str(v).strip() not in ("", "nan"):
                try:
                    slip_no = str(int(float(v)))
                except (ValueError, OverflowError):
                    slip_no = str(v).strip()
            else:
                slip_no = ""

            all_deliveries.append({
                "delivery_date": delivery_date,
                "field_no":      field_no,
                "contract_no":   contract_no,
                "slip_no":       slip_no,
                "delivery_qty":  qty,
                "batch_id":      batch_id,
            })

            if contract_no not in contracts_info:
                seller = str(row["販売店略名"]).strip()
                secondary = str(row["二次店略名"]).strip()
                if not secondary or secondary.lower() in ("nan", "none"):
                    secondary = seller
                contracts_info[contract_no] = {
                    "contract_no":        contract_no,
                    "field_name":         str(row["現場名"]).strip(),
                    "general_contractor": str(row["施工者名"]).strip(),
                    "seller":             seller,
                    "secondary_seller":   secondary,
                }

        file_count = len(all_deliveries) - rows_before
        if file_count > 0:
            batch_rows.append({
                "id":           batch_id,
                "filename":     f.name,
                "record_count": file_count,
            })

    if not all_deliveries:
        st.error("取込可能なデータがありませんでした")
        if parse_errors:
            with st.expander(f"⚠️ エラー内容（{len(parse_errors)}件）"):
                for e in parse_errors:
                    st.write(f"- {e}")
        return

    # ── Supabase 書込フェーズ ─────────────────────────────────────────────────
    progress.progress(0.55, text="既存データ確認中...")

    # 既存 contract_no を全件取得（ページネーション）
    existing_nos: set[str] = set()
    offset = 0
    while True:
        res = (
            supabase.table("contracts")
            .select("contract_no")
            .range(offset, offset + 999)
            .execute()
        )
        for r in res.data:
            existing_nos.add(r["contract_no"])
        if len(res.data) < 1000:
            break
        offset += 1000

    # 新規契約を INSERT
    new_contracts = [
        {**info, "contract_qty": None}
        for no, info in contracts_info.items()
        if no not in existing_nos
    ]
    failed_contracts: set[str] = set()

    if new_contracts:
        progress.progress(0.60, text=f"新規契約 {len(new_contracts)}件を登録中...")
        for j in range(0, len(new_contracts), 200):
            chunk = new_contracts[j : j + 200]
            try:
                supabase.table("contracts").insert(chunk).execute()
                for c in chunk:
                    existing_nos.add(c["contract_no"])
            except Exception as e:
                for c in chunk:
                    failed_contracts.add(c["contract_no"])
                parse_errors.append(f"契約INSERT失敗 (バッチ{j // 200 + 1}): {e}")

    # 既存契約のメタ情報を UPDATE（contract_qty は変更しない）
    existing_to_update = [
        info
        for no, info in contracts_info.items()
        if no in existing_nos and no not in failed_contracts
        and no not in {c["contract_no"] for c in new_contracts}  # 新規INSERTはスキップ
    ]
    if existing_to_update:
        progress.progress(0.65, text=f"既存契約 {len(existing_to_update)}件を更新中...")
        for info in existing_to_update:
            try:
                supabase.table("contracts").update({
                    "field_name":         info["field_name"],
                    "general_contractor": info["general_contractor"],
                    "seller":             info["seller"],
                    "secondary_seller":   info["secondary_seller"],
                }).eq("contract_no", info["contract_no"]).execute()
            except Exception as e:
                parse_errors.append(
                    f"契約UPDATE失敗 {info['contract_no']}: {e}"
                )

    # 取込履歴を登録（import_batches テーブル未作成ならスキップして従来どおり動作）
    batches_supported = True
    if batch_rows:
        try:
            supabase.table("import_batches").insert(batch_rows).execute()
        except Exception:
            batches_supported = False
            for d in all_deliveries:
                d.pop("batch_id", None)

    # deliveries を UPSERT（200件/バッチ、0.3秒インターバル）
    valid_deliveries = [
        d for d in all_deliveries if d["contract_no"] not in failed_contracts
    ]
    skipped_count = len(all_deliveries) - len(valid_deliveries)

    for j in range(0, len(valid_deliveries), 200):
        done = min(j + 200, len(valid_deliveries))
        pct  = 0.70 + 0.28 * (j / max(len(valid_deliveries), 1))
        progress.progress(
            pct,
            text=f"出荷実績登録中... {done:,}/{len(valid_deliveries):,}件",
        )
        chunk = valid_deliveries[j : j + 200]
        try:
            supabase.table("deliveries").upsert(chunk).execute()
        except Exception as e:
            parse_errors.append(f"deliveries UPSERT失敗 (バッチ{j // 200 + 1}): {e}")
        time.sleep(0.3)

    progress.progress(1.0, text="完了！")

    # 結果表示
    st.success(
        f"**取込完了！**　　"
        f"新規契約：**{len(new_contracts)}件**　／　"
        f"出荷レコード：**{len(valid_deliveries):,}件**"
    )
    if skipped_count > 0:
        st.warning(
            f"⚠️ {skipped_count}件の出荷レコードがスキップされました"
            f"（契約INSERT失敗）"
        )
    if not batches_supported:
        st.info(
            "ℹ️ 取込履歴テーブルが未作成のため、ファイル単位の取消は記録されませんでした。"
            "有効にするには migration_add_import_batches.sql を "
            "Supabase の SQL Editor で実行してください。"
        )
    if parse_errors:
        with st.expander(f"⚠️ エラー・警告（{len(parse_errors)}件）"):
            for e in parse_errors:
                st.write(f"- {e}")

    load_data.clear()
    load_delivery_months.clear()
    load_import_batches.clear()


# ── データ管理ヘルパー ─────────────────────────────────────────────────────────
def _rename_jv_option(supabase: Client, old: str, new: str) -> None:
    """JV選択肢を改名し、使用中の契約のJVも書き換える"""
    supabase.table("jv_options").update({"name": new}).eq("name", old).execute()
    res = (
        supabase.table("contracts")
        .select("contract_no,jv")
        .ilike("jv", f"%{old}%")
        .execute()
    )
    for row in res.data:
        parts = _jv_to_list(row.get("jv"))
        if old in parts:
            parts = [new if p == old else p for p in parts]
            supabase.table("contracts").update(
                {"jv": ",".join(dict.fromkeys(parts))}
            ).eq("contract_no", row["contract_no"]).execute()


def _fetch_all(supabase: Client, table: str, select: str = "*") -> list[dict]:
    """テーブル全件をページネーションで取得する"""
    rows: list[dict] = []
    offset = 0
    while True:
        res = supabase.table(table).select(select).range(offset, offset + 999).execute()
        rows.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    return rows


def _build_backup_zip(supabase: Client) -> bytes:
    """全データのバックアップZIPを作成する。
    出荷実績はCSV取込画面でそのまま再取込できる列構成にする。"""
    cdf = pd.DataFrame(_fetch_all(supabase, "contracts"))
    ddf = pd.DataFrame(_fetch_all(supabase, "deliveries"))

    delivery_cols = [
        "出荷日", "現場ＮＯ", "出荷伝票番号", "出荷量", "契約ＮＯ",
        "現場名", "施工者名", "販売店略名", "二次店略名",
    ]
    if not ddf.empty and not cdf.empty:
        meta = cdf[
            ["contract_no", "field_name", "general_contractor",
             "seller", "secondary_seller"]
        ]
        merged = ddf.merge(meta, on="contract_no", how="left")
        deliveries_out = pd.DataFrame({
            "出荷日":       merged["delivery_date"],
            "現場ＮＯ":     merged["field_no"],
            "出荷伝票番号": merged["slip_no"],
            "出荷量":       merged["delivery_qty"],
            "契約ＮＯ":     merged["contract_no"],
            "現場名":       merged["field_name"],
            "施工者名":     merged["general_contractor"],
            "販売店略名":   merged["seller"],
            "二次店略名":   merged["secondary_seller"],
        })
    else:
        deliveries_out = pd.DataFrame(columns=delivery_cols)

    contract_cols = {
        "contract_no":        "契約NO",
        "seller":             "販売店",
        "secondary_seller":   "二次店",
        "general_contractor": "ゼネコン",
        "field_name":         "現場名",
        "contract_qty":       "契約数量(m3)",
        "contract_date":      "契約日",
        "start_date":         "着工日",
        "jv":                 "JV",
        "unit_price":         "単価(円/m3)",
        "memo":               "備考",
    }
    if cdf.empty:
        contracts_out = pd.DataFrame(columns=list(contract_cols.values()))
    else:
        use = [c for c in contract_cols if c in cdf.columns]
        contracts_out = cdf[use].rename(columns=contract_cols)

    readme = (
        "契約残管理アプリ バックアップ\r\n"
        f"作成日時: {datetime.now():%Y/%m/%d %H:%M}\r\n"
        "\r\n"
        "【復元方法】\r\n"
        "1. 出荷実績.csv → アプリの「CSV取込」画面でそのまま取込できます\r\n"
        "2. 契約一覧.csv → 契約数量・備考の控えです。\r\n"
        "   取込後に契約残一覧の編集モードで入力し直してください\r\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "出荷実績.csv",
            deliveries_out.to_csv(index=False).encode("cp932", errors="replace"),
        )
        z.writestr(
            "契約一覧.csv",
            contracts_out.to_csv(index=False).encode("cp932", errors="replace"),
        )
        z.writestr("はじめにお読みください.txt", readme.encode("cp932", errors="replace"))
    return buf.getvalue()


def _delete_months_and_orphans(supabase: Client, months: list[str]) -> int:
    """月別出荷実績を削除し、出荷実績のなくなった契約も削除する。削除した契約数を返す。"""
    affected: set[str] = set()

    for ym in months:
        year, month_int = int(ym[:4]), int(ym[5:7])
        next_year  = year + (1 if month_int == 12 else 0)
        next_month = 1 if month_int == 12 else month_int + 1
        next_ym    = f"{next_year:04d}-{next_month:02d}-01"

        # 削除対象月に含まれる contract_no を事前取得
        offset = 0
        while True:
            res = (
                supabase.table("deliveries").select("contract_no")
                .gte("delivery_date", f"{ym}-01").lt("delivery_date", next_ym)
                .range(offset, offset + 999).execute()
            )
            for row in res.data:
                affected.add(row["contract_no"])
            if len(res.data) < 1000:
                break
            offset += 1000

        # deliveries を削除
        supabase.table("deliveries").delete().gte(
            "delivery_date", f"{ym}-01"
        ).lt("delivery_date", next_ym).execute()

    return _delete_orphans_among(supabase, affected)


def _delete_orphans_among(supabase: Client, affected: set[str]) -> int:
    """指定契約のうち、出荷実績が残っていないものを削除する。削除件数を返す。"""
    if not affected:
        return 0

    # 削除後も残存 deliveries がある contract_no を取得
    remaining: set[str] = set()
    affected_list = list(affected)
    for i in range(0, len(affected_list), 200):
        res = supabase.table("deliveries").select("contract_no").in_(
            "contract_no", affected_list[i : i + 200]
        ).execute()
        for row in res.data:
            remaining.add(row["contract_no"])

    # 孤立契約（残存 deliveries ゼロ）を削除
    orphans = list(affected - remaining)
    for i in range(0, len(orphans), 200):
        supabase.table("contracts").delete().in_(
            "contract_no", orphans[i : i + 200]
        ).execute()

    return len(orphans)


def _delete_batch_and_orphans(supabase: Client, batch_id: str) -> tuple[int, int]:
    """取込バッチ1件分の出荷実績を削除し、孤立した契約も削除する。
    （削除した出荷レコード数, 削除した契約数）を返す。"""
    # 対象バッチの contract_no と件数を取得
    affected: set[str] = set()
    deleted_rows = 0
    offset = 0
    while True:
        res = (
            supabase.table("deliveries").select("contract_no")
            .eq("batch_id", batch_id)
            .range(offset, offset + 999).execute()
        )
        for row in res.data:
            affected.add(row["contract_no"])
        deleted_rows += len(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000

    supabase.table("deliveries").delete().eq("batch_id", batch_id).execute()
    orphan_count = _delete_orphans_among(supabase, affected)
    supabase.table("import_batches").delete().eq("id", batch_id).execute()
    return deleted_rows, orphan_count


def _list_orphan_contracts(supabase: Client) -> list[str]:
    """出荷実績のない契約NOの一覧を返す。
    DB側ビュー（orphan_contracts）があれば使い、なければ全行スキャン。"""
    try:
        res = supabase.table("orphan_contracts").select("contract_no").execute()
        return [row["contract_no"] for row in res.data]
    except Exception:
        pass

    has_delivery: set[str] = set()
    offset = 0
    while True:
        res = supabase.table("deliveries").select("contract_no").range(
            offset, offset + 999
        ).execute()
        for row in res.data:
            has_delivery.add(row["contract_no"])
        if len(res.data) < 1000:
            break
        offset += 1000

    orphans: list[str] = []
    offset = 0
    while True:
        res = supabase.table("contracts").select("contract_no").range(
            offset, offset + 999
        ).execute()
        for row in res.data:
            if row["contract_no"] not in has_delivery:
                orphans.append(row["contract_no"])
        if len(res.data) < 1000:
            break
        offset += 1000
    return orphans


def _delete_orphan_contracts(supabase: Client) -> int:
    """出荷実績のない契約をすべて削除する。削除件数を返す。"""
    orphans = _list_orphan_contracts(supabase)
    for i in range(0, len(orphans), 200):
        supabase.table("contracts").delete().in_(
            "contract_no", orphans[i : i + 200]
        ).execute()
    return len(orphans)


# ── Page 3: データ管理 ────────────────────────────────────────────────────────
def page_data_management() -> None:
    st.header("データ管理")
    supabase = get_supabase()

    # ── 取込バッチ取消確認ダイアログ（優先表示） ────────────────────────────
    if "pending_batch_delete" in st.session_state:
        batch: dict = st.session_state["pending_batch_delete"]
        st.warning(
            f"⚠️ 取込ファイル **{batch['filename']}**"
            f"（{batch['record_count']:,}件）の出荷実績を取り消します。\n\n"
            "このファイルで取り込んだ出荷実績が削除され、"
            "出荷実績がなくなった契約も契約残一覧から自動削除されます。\n"
            "この操作は取り消せません。"
        )
        col1, col2, _ = st.columns([1, 1, 5])
        with col1:
            if st.button("🗑️ 取り消す", type="primary", key="btn_confirm_batch_delete"):
                rows, orphans = _delete_batch_and_orphans(supabase, batch["id"])
                st.session_state.pop("pending_batch_delete", None)
                load_import_batches.clear()
                load_delivery_months.clear()
                load_data.clear()
                st.success(
                    f"✅ {batch['filename']} の取込を取り消しました"
                    f"（出荷実績 {rows:,}件、関連契約 {orphans:,}件 を削除）"
                )
                st.rerun()
        with col2:
            if st.button("キャンセル", key="btn_cancel_batch_delete"):
                st.session_state.pop("pending_batch_delete", None)
                st.rerun()
        return

    # ── 月削除確認ダイアログ（優先表示） ────────────────────────────────────
    if "pending_month_delete" in st.session_state:
        months: list[str] = st.session_state["pending_month_delete"]
        labels = "、".join([f"{m[:4]}年{int(m[5:7])}月" for m in months])
        st.warning(
            f"⚠️ **{labels}** の出荷実績データをすべて削除します。\n\n"
            "出荷実績がなくなった契約も自動的に契約残一覧から削除されます。\n"
            "この操作は取り消せません。"
        )
        col1, col2, _ = st.columns([1, 1, 5])
        with col1:
            if st.button("🗑️ 削除する", type="primary", key="btn_confirm_month_delete"):
                deleted_contracts = _delete_months_and_orphans(supabase, months)
                st.session_state.pop("pending_month_delete", None)
                load_delivery_months.clear()
                load_data.clear()
                st.success(
                    f"✅ {labels} の出荷実績を削除しました。"
                    f"（関連契約 {deleted_contracts:,}件 も削除）"
                )
                st.rerun()
        with col2:
            if st.button("キャンセル", key="btn_cancel_month_delete"):
                st.session_state.pop("pending_month_delete", None)
                st.rerun()
        return

    # ── 孤立契約削除確認ダイアログ ──────────────────────────────────────────
    if "pending_orphan_delete" in st.session_state:
        orphan_count = st.session_state["pending_orphan_delete"]
        st.warning(
            f"⚠️ 契約一覧に残っている **{orphan_count:,}件** をすべて削除します。\n\n"
            "出荷データと紐づいていない契約がすべて削除されます。この操作は取り消せません。"
        )
        col1, col2, _ = st.columns([1, 1, 5])
        with col1:
            if st.button("🗑️ 削除する", type="primary", key="btn_confirm_orphan_delete"):
                deleted = _delete_orphan_contracts(supabase)
                st.session_state.pop("pending_orphan_delete", None)
                load_data.clear()
                st.success(f"✅ {deleted:,}件の契約を削除しました")
                st.rerun()
        with col2:
            if st.button("キャンセル", key="btn_cancel_orphan_delete"):
                st.session_state.pop("pending_orphan_delete", None)
                st.rerun()
        return

    # ── バックアップ ──────────────────────────────────────────────────────────
    st.subheader("バックアップ")
    st.caption(
        "全データ（契約一覧・出荷実績）をCSVで保存します。"
        "出荷実績はCSV取込画面からそのまま復元できます。削除操作の前に取っておくと安心です。"
    )
    if st.button("📦 バックアップファイルを作成", key="btn_make_backup"):
        with st.spinner("バックアップ作成中..."):
            st.session_state["backup_zip"] = _build_backup_zip(supabase)
            st.session_state["backup_at"] = f"{datetime.now():%Y%m%d_%H%M}"
    if "backup_zip" in st.session_state:
        st.download_button(
            f"💾 ダウンロード（契約残バックアップ_{st.session_state['backup_at']}.zip）",
            data=st.session_state["backup_zip"],
            file_name=f"契約残バックアップ_{st.session_state['backup_at']}.zip",
            mime="application/zip",
            key="btn_dl_backup",
        )

    st.markdown("---")

    # ── 取込履歴（ファイル単位） ──────────────────────────────────────────────
    st.subheader("取込履歴（ファイル単位）")
    batches = load_import_batches()
    if batches is None:
        st.caption(
            "ファイル単位の取消を使うには、`migration_add_import_batches.sql` を "
            "Supabase の SQL Editor で1回実行してください（実行するまでは月別削除のみ使えます）。"
        )
    elif not batches:
        st.caption("取込履歴はまだありません。次回のCSV取込から記録されます。")
    else:
        st.caption("間違えて取り込んだファイルを、ファイル単位で取り消せます。")
        for b in batches:
            try:
                dt = dateutil_parser.parse(b["imported_at"]).astimezone()
                when = f"{dt:%Y/%m/%d %H:%M}"
            except Exception:
                when = str(b["imported_at"])[:16]
            c1, c2 = st.columns([5, 1])
            c1.markdown(
                f"**{b['filename']}**　　{when} 取込　　{b['record_count']:,} 件"
            )
            if c2.button("取消", key=f"btn_undo_{b['id']}"):
                st.session_state["pending_batch_delete"] = b
                st.rerun()

    st.markdown("---")

    # ── 取込済みデータ一覧 ────────────────────────────────────────────────────
    st.subheader("取込済み出荷実績（月別）")
    st.caption("間違えて取り込んだ月を選択して削除できます。出荷実績がなくなった契約も自動で一覧から削除されます。")
    st.markdown("---")

    month_counts = load_delivery_months()

    if not month_counts:
        st.info("取込済みの出荷実績データがありません。")
    else:
        selected_months: list[str] = []
        for ym, cnt in month_counts.items():
            year, mon = ym[:4], str(int(ym[5:7]))
            if st.checkbox(f"**{year}年{mon}月**　　{cnt:,} 件", key=f"chk_{ym}"):
                selected_months.append(ym)

        if selected_months:
            labels = "、".join([f"{m[:4]}年{int(m[5:7])}月" for m in selected_months])
            st.markdown("---")
            st.warning(f"⚠️ {labels} を選択中（{len(selected_months)}ヶ月）")
            if st.button(
                f"🗑️ 選択した月の出荷データを削除（{len(selected_months)}ヶ月）",
                type="primary",
                key="btn_month_delete",
            ):
                st.session_state["pending_month_delete"] = selected_months
                st.rerun()

    # ── JV選択肢の管理 ────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("JV選択肢の管理")
    jv_master = load_jv_options()
    if jv_master is None:
        st.caption(
            "JV機能を使うには `migration_add_contract_fields.sql` を "
            "Supabase の SQL Editor で1回実行してください。"
        )
    else:
        st.caption(
            "契約の詳細編集で選べるJVの選択肢です。"
            "改名すると、設定済みの契約にも自動で反映されます。"
        )
        for name in jv_master:
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"・**{name}**")
            if c2.button("削除", key=f"btn_jv_del_{name}"):
                supabase.table("jv_options").delete().eq("name", name).execute()
                load_jv_options.clear()
                st.rerun()

        a1, a2 = st.columns([4, 1], vertical_alignment="bottom")
        new_name = a1.text_input("新しい選択肢を追加", key="jv_new_name")
        if a2.button("追加", key="btn_jv_add"):
            if new_name.strip():
                if new_name.strip() in jv_master:
                    st.error("同じ名前が既にあります")
                else:
                    supabase.table("jv_options").insert({
                        "name": new_name.strip(),
                        "sort_order": len(jv_master) + 1,
                    }).execute()
                    load_jv_options.clear()
                    st.rerun()

        if jv_master:
            r1, r2, r3 = st.columns([2, 2, 1], vertical_alignment="bottom")
            ren_target = r1.selectbox("改名する選択肢", jv_master, index=None,
                                      placeholder="選択", key="jv_ren_target")
            ren_new = r2.text_input("新しい名前", key="jv_ren_new")
            if r3.button("改名", key="btn_jv_rename"):
                if ren_target and ren_new.strip():
                    _rename_jv_option(supabase, ren_target, ren_new.strip())
                    load_jv_options.clear()
                    load_data.clear()
                    st.success(
                        f"✅ 「{ren_target}」を「{ren_new.strip()}」に改名しました"
                        "（設定済みの契約にも反映）"
                    )
                    st.rerun()

    # ── クリーンアップ ────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("クリーンアップ")
    orphan_count = len(_list_orphan_contracts(supabase))
    if orphan_count > 0:
        st.caption(
            f"出荷データと紐づいていない契約が **{orphan_count:,}件** あります。"
            "取込データをすべて削除した場合など、契約一覧に残ったデータをここで削除できます。"
        )
        if st.button(
            f"🗑️ 契約一覧をリセット（{orphan_count:,}件削除）",
            key="btn_orphan_delete",
        ):
            st.session_state["pending_orphan_delete"] = orphan_count
            st.rerun()
    else:
        st.caption("契約一覧は最新の取込データと一致しています。")


# ── エントリーポイント ─────────────────────────────────────────────────────────
def main() -> None:
    # ダークモード初期化
    if "dark_mode" not in st.session_state:
        st.session_state.dark_mode = False

    # Cookie によるログイン保持（30日）
    cookie_mgr = get_cookie_manager()
    cookies = cookie_mgr.get_all(key="cookies_init") or {}
    cookie_token = cookies.get(AUTH_COOKIE)

    if not st.session_state.get("authenticated", False):
        if _verify_auth_token(cookie_token):
            st.session_state.authenticated = True

    if not st.session_state.get("authenticated", False):
        if st.session_state.dark_mode:
            st.markdown(DARK_MODE_CSS, unsafe_allow_html=True)
        show_login()
        st.stop()

    # ログイン済みなのに有効な Cookie がなければ書き込む（書込失敗時も次回実行で再試行される）
    if not _verify_auth_token(cookie_token):
        cookie_mgr.set(
            AUTH_COOKIE,
            _make_auth_token(),
            expires_at=datetime.now() + timedelta(days=AUTH_DAYS),
            key="cookie_set_auth",
        )

    with st.sidebar:
        st.markdown("## 🏗️ 生コン契約残管理\n**中央コンクリート**")
        st.markdown("---")
        page = st.radio(
            "", ["契約残一覧", "CSV取込", "データ管理"],
            label_visibility="collapsed",
            key="nav",
        )
        st.markdown("---")
        st.toggle("🌙 ダークモード", key="dark_mode")
        st.markdown("---")
        if st.button("ログアウト", key="btn_logout"):
            st.session_state.authenticated = False
            cookie_mgr.delete(AUTH_COOKIE, key="cookie_del_auth")
            time.sleep(0.4)  # Cookie削除が画面側で処理されるのを待つ
            st.rerun()

    if st.session_state.dark_mode:
        st.markdown(DARK_MODE_CSS, unsafe_allow_html=True)

    if page == "契約残一覧":
        page_contracts()
    elif page == "CSV取込":
        page_csv_import()
    else:
        page_data_management()


if __name__ == "__main__":
    main()
