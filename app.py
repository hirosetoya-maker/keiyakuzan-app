import io
import time

import pandas as pd
import streamlit as st
from dateutil import parser as dateutil_parser
from supabase import Client, create_client

# ── ページ設定 ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="生コン契約残管理",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="auto",
)

# ── カスタムCSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* 全デバイス共通：不要UI非表示 */
#MainMenu        {display: none !important;}
footer           {display: none !important;}
[data-testid="stDeployButton"]   {display: none !important;}
[data-testid="stStatusWidget"]   {display: none !important;}
[data-testid="stToolbar"]        {display: none !important;}

/* デスクトップ：ヘッダー非表示、サイドバー常時表示 */
@media (min-width: 641px) {
    header {display: none !important;}
    [data-testid="stSidebar"] {
        transform: translateX(0) !important;
        min-width: 244px !important;
        width: 244px !important;
        display: block !important;
    }
    [data-testid="stSidebarCollapseButton"]   {display: none !important;}
    [data-testid="stSidebarCollapsedControl"] {display: none !important;}
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
                "general_contractor", "contract_qty", "memo", "shipped_qty", "remaining_qty",
            ]
        )

    df = pd.DataFrame(all_rows)
    df["contract_qty"] = pd.to_numeric(df["contract_qty"], errors="coerce")
    df["shipped_qty"] = pd.to_numeric(df["shipped_qty"], errors="coerce").fillna(0.0)

    # "nan" 文字列を空欄に統一
    for col in ("seller", "secondary_seller", "general_contractor", "field_name", "memo"):
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

    # ── KPIカード ──────────────────────────────────────────────────────────────
    total_count = len(df)
    qty_series = df["contract_qty"].dropna() if total_count > 0 else pd.Series(dtype=float)
    rem_series  = df["remaining_qty"].dropna() if total_count > 0 else pd.Series(dtype=float)

    c1, c2, c3 = st.columns(3)
    c1.metric("契約件数", f"{total_count:,} 件")
    c2.metric(
        "契約数量合計",
        f"{qty_series.sum():,.1f} m³" if len(qty_series) > 0 else "－",
    )
    c3.metric(
        "契約残合計",
        f"{rem_series.sum():,.1f} m³" if len(rem_series) > 0 else "－",
    )

    st.markdown("---")

    # ── フィルター（2×2グリッド） ─────────────────────────────────────────────
    fc1, fc2 = st.columns(2)
    with fc1:
        f_seller     = st.text_input("🔍 販売店",  key="f_seller")
        f_contractor = st.text_input("🔍 ゼネコン", key="f_contractor")
    with fc2:
        f_secondary  = st.text_input("🔍 二次店",  key="f_secondary")
        f_field      = st.text_input("🔍 現場名",  key="f_field")

    # フィルタ適用
    filtered = df.copy()
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

    # ── テーブル表示用 DataFrame ─────────────────────────────────────────────
    memo_col = "memo" if "memo" in filtered.columns else None
    display_cols = [
        "contract_no", "seller", "secondary_seller", "general_contractor",
        "field_name", "contract_qty", "shipped_qty", "remaining_qty",
    ]
    if memo_col:
        display_cols.append("memo")
    display_df = filtered[display_cols].copy()
    # NaN を pandas nullable Float64 に変換（Arrow シリアライズ時の "None" 表示を防ぐ）
    for col in ("contract_qty", "remaining_qty"):
        if col in display_df.columns:
            display_df[col] = display_df[col].astype("Float64")
    display_df["is_completed"] = False

    col_config = {
        "contract_no":        st.column_config.TextColumn(
                                  "契約NO", disabled=True, width="small"),
        "seller":             st.column_config.TextColumn("販売店", disabled=True),
        "secondary_seller":   st.column_config.TextColumn("二次店", disabled=True),
        "general_contractor": st.column_config.TextColumn("ゼネコン", disabled=True),
        "field_name":         st.column_config.TextColumn(
                                  "現場名", disabled=True, width="large"),
        "contract_qty":       st.column_config.NumberColumn(
                                  "契約数量（m³）", min_value=0, step=0.5),
        "shipped_qty":        st.column_config.NumberColumn(
                                  "出荷実績（m³）", format="%g", disabled=True),
        "remaining_qty":      st.column_config.NumberColumn(
                                  "契約残（m³）", disabled=True),
        "memo":               st.column_config.TextColumn("備考", width="medium"),
        "is_completed":       st.column_config.CheckboxColumn("✅ 完了"),
    }

    editor_key = f"editor_{f_seller}_{f_secondary}_{f_contractor}_{f_field}"

    st.caption(f"表示中 **{len(filtered):,} 件** ／ 全 {total_count:,} 件")

    if df.empty:
        st.info("データがありません。まずCSV取込を行ってください。")
        st.markdown("---")
        _show_add_form(df, supabase)
        return

    edit_mode = st.toggle("✏️ 編集モード（契約数量・備考の修正、完了削除）", key="edit_mode")

    if not edit_mode:
        _render_view_table(filtered)
        st.markdown("---")
        _show_add_form(df, supabase)
        return

    # ── 編集モード ────────────────────────────────────────────────────────────
    # 自動保存済みメッセージ
    if st.session_state.pop("autosaved_count", None):
        st.success("✅ 自動保存しました")

    # データエディタ
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

    # 数量・備考の自動保存
    save_edits = {
        idx: {k: v for k, v in changes.items() if k in ("contract_qty", "memo")}
        for idx, changes in edited_rows.items()
        if any(k in ("contract_qty", "memo") for k in changes)
    }
    if save_edits:
        _handle_save(display_df, save_edits, supabase)

    # ── 手動追加フォーム ──────────────────────────────────────────────────────
    st.markdown("---")
    _show_add_form(df, supabase)


def _render_view_table(filtered: pd.DataFrame) -> None:
    """閲覧モード：色強調・消化率バー・並び替え可能な読み取り専用テーブル"""
    view_df = filtered.copy()

    # 消化率（%）。契約数量が未設定・0 の行は計算不可なので空欄
    def _calc_pct(row):
        qty = row["contract_qty"]
        if pd.isna(qty) or float(qty) <= 0:
            return None
        return float(row["shipped_qty"]) / float(qty) * 100.0

    view_df["consumption_pct"] = view_df.apply(_calc_pct, axis=1)

    # 初期表示は契約残が多い順（列見出しクリックでいつでも並び替え可）
    view_df = (
        view_df
        .sort_values("remaining_qty", ascending=False, na_position="last")
        .reset_index(drop=True)
    )

    view_cols = [
        "contract_no", "seller", "secondary_seller", "general_contractor",
        "field_name", "contract_qty", "shipped_qty", "remaining_qty",
        "consumption_pct",
    ]
    if "memo" in view_df.columns:
        view_cols.append("memo")
    view_df = view_df[view_cols]

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

    for c in ("contract_qty", "shipped_qty", "remaining_qty"):
        view_df[c] = view_df[c].map(_fmt_qty)

    def _row_style(row):
        if warn_mask.iloc[row.name]:
            return ["background-color: #fff3cd; color: #5c4a00;"] * len(row)
        return [""] * len(row)

    styler = view_df.style.apply(_row_style, axis=1)

    st.dataframe(
        styler,
        column_config={
            "contract_no":        st.column_config.TextColumn("契約NO", width="small"),
            "seller":             st.column_config.TextColumn("販売店", width="medium"),
            "secondary_seller":   st.column_config.TextColumn("二次店", width="small"),
            "general_contractor": st.column_config.TextColumn("ゼネコン", width="medium"),
            "field_name":         st.column_config.TextColumn("現場名", width="large"),
            "contract_qty":       st.column_config.TextColumn(
                                      "契約数量（m³）", width="small", alignment="right"),
            "shipped_qty":        st.column_config.TextColumn(
                                      "出荷実績（m³）", width="small", alignment="right"),
            "remaining_qty":      st.column_config.TextColumn(
                                      "契約残（m³）", width="small", alignment="right"),
            "consumption_pct":    st.column_config.ProgressColumn(
                                      "消化率", format="%.0f%%",
                                      min_value=0, max_value=100),
            "memo":               st.column_config.TextColumn("備考", width="medium"),
        },
        use_container_width=True,
        hide_index=True,
        height=600,
    )
    st.caption("🟨 黄色の行＝契約残が50%以上（出荷が進んでいない要注意現場）　／　列見出しをクリックすると並び替えできます")


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

        if payload:
            to_update.append({"contract_no": contract_no, **payload})

    if to_update:
        for r in to_update:
            cn      = r["contract_no"]
            payload = {k: v for k, v in r.items() if k != "contract_no"}
            supabase.table("contracts").update(payload).eq("contract_no", cn).execute()
        load_data.clear()
        st.session_state["autosaved_count"] = len(to_update)
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
        with a2:
            new_field      = st.text_input("現場名 *",       key="add_field")
            new_secondary  = st.text_input("二次店",         key="add_secondary")
            new_qty_str    = st.text_input("契約数量（m³）", key="add_qty")

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

            supabase.table("contracts").insert({
                "contract_no":        new_no.strip(),
                "field_name":         new_field.strip(),
                "seller":             new_seller.strip() or None,
                "secondary_seller":   new_secondary.strip() or None,
                "general_contractor": new_contractor.strip() or None,
                "contract_qty":       contract_qty,
            }).execute()

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

    # ── ファイル読込フェーズ ──────────────────────────────────────────────────
    for i, f in enumerate(uploaded_files):
        progress.progress(
            (i + 0.5) / len(uploaded_files),
            text=f"読込中: {f.name}",
        )
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

        for _, row in df.iterrows():
            contract_no = str(row["契約ＮＯ"]).strip()
            field_no    = str(row["現場ＮＯ"]).strip()

            # 出荷日パース（"2026/4/1" 形式など）
            try:
                delivery_date = dateutil_parser.parse(
                    str(row["出荷日"])
                ).strftime("%Y-%m-%d")
            except Exception:
                delivery_date = str(row["出荷日"]).strip()

            # 出荷伝票番号（NaN安全変換）
            v = row["出荷伝票番号"]
            if pd.notna(v) and str(v).strip() not in ("", "nan"):
                try:
                    slip_no = str(int(float(v)))
                except (ValueError, OverflowError):
                    slip_no = str(v).strip()
            else:
                slip_no = ""

            qty = float(row["出荷量"]) if pd.notna(row["出荷量"]) else 0.0

            all_deliveries.append({
                "delivery_date": delivery_date,
                "field_no":      field_no,
                "contract_no":   contract_no,
                "slip_no":       slip_no,
                "delivery_qty":  qty,
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
    if parse_errors:
        with st.expander(f"⚠️ エラー・警告（{len(parse_errors)}件）"):
            for e in parse_errors:
                st.write(f"- {e}")

    load_data.clear()


# ── データ管理ヘルパー ─────────────────────────────────────────────────────────
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


def _count_orphan_contracts(supabase: Client) -> int:
    """出荷実績のない契約の件数を返す"""
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

    count = 0
    offset = 0
    while True:
        res = supabase.table("contracts").select("contract_no").range(
            offset, offset + 999
        ).execute()
        for row in res.data:
            if row["contract_no"] not in has_delivery:
                count += 1
        if len(res.data) < 1000:
            break
        offset += 1000
    return count


def _delete_orphan_contracts(supabase: Client) -> int:
    """出荷実績のない契約をすべて削除する。削除件数を返す。"""
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

    for i in range(0, len(orphans), 200):
        supabase.table("contracts").delete().in_(
            "contract_no", orphans[i : i + 200]
        ).execute()
    return len(orphans)


# ── Page 3: データ管理 ────────────────────────────────────────────────────────
def page_data_management() -> None:
    st.header("データ管理")
    supabase = get_supabase()

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

    # ── クリーンアップ ────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("クリーンアップ")
    orphan_count = _count_orphan_contracts(supabase)
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

    if not st.session_state.get("authenticated", False):
        if st.session_state.dark_mode:
            st.markdown(DARK_MODE_CSS, unsafe_allow_html=True)
        show_login()
        st.stop()

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
