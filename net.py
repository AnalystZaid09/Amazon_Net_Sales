import streamlit as st
import pandas as pd
import io

st.set_page_config(page_title="Net Sale Dashboard", page_icon="📦", layout="wide")
st.title("📦 Amazon Net Sale Dashboard")
st.markdown("Upload your transaction CSV and PM Excel file to generate the sales report.")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📂 Upload Files")
    csv_file    = st.file_uploader("Transaction CSV (header at row 12)", type=["csv"])
    pm_file     = st.file_uploader("PM Excel (Purchase Master)",         type=["xlsx","xls"])
    refund_file = st.file_uploader("Refund CSV (header at row 12)",      type=["csv"])

# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt(val):
    try:    return f"₹{val:,.2f}"
    except: return val

def clean_sku(s):
    return (s.astype(str).str.upper().str.strip()
             .str.replace(",","",regex=False)
             .str.replace(r"\s+"," ",regex=True))

@st.cache_data(show_spinner=False)
def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()

# ── All heavy work in ONE cached function, keyed on raw bytes ────────────────
# Using bytes as keys is the correct pattern — pandas DataFrames are NOT
# reliable cache keys (Streamlit hashes them by id, not content).
@st.cache_data(show_spinner="Processing data…")
def run_pipeline(csv_bytes: bytes, pm_bytes: bytes, refund_bytes: bytes):

    # 1. Load transaction CSV
    ns = pd.read_csv(io.BytesIO(csv_bytes), header=11, low_memory=False)
    ns.columns = ns.columns.str.lower()

    num_cols = ["quantity","product sales",
                "total sales tax liable(gst before adjusting tcs)","total"]
    ns[num_cols] = ns[num_cols].replace(",","",regex=True)
    ns[num_cols] = ns[num_cols].apply(pd.to_numeric, errors="coerce")
    ns = ns[ns["type"] == "Order"]
    ns = ns[ns["product sales"] != 0]
    ns = ns.sort_values("order id").reset_index(drop=True)

    # 2. Load PM
    pm = pd.read_excel(io.BytesIO(pm_bytes))
    pm.columns = pm.columns.str.lower()
    pm["amazon sku name"] = clean_sku(pm["amazon sku name"])
    pm_lk = pm[["amazon sku name","asin","brand manager","brand","cp"]].drop_duplicates("amazon sku name")

    # 3. Enrich netsale
    ns["sku"] = clean_sku(ns["sku"])
    ns = ns.merge(pm_lk, left_on="sku", right_on="amazon sku name", how="left")
    ns["cp"] = pd.to_numeric(
        ns["cp"].astype(str).str.replace(",","",regex=False), errors="coerce")
    ns["cp as per qty"] = ns["cp"] * ns["quantity"]

    # 4. Groupby pivot
    pivot = (
        ns.groupby(["sku","order id","asin","brand"], dropna=False)[
            ["quantity","product sales",
             "total sales tax liable(gst before adjusting tcs)",
             "total","cp","cp as per qty"]
        ].sum().reset_index()
    )
    pivot["Sales Amount (Turn Over)"] = (
        pivot["product sales"] +
        pivot["total sales tax liable(gst before adjusting tcs)"]
    )
    pivot["Amazon Total Deducation"]   = pivot["Sales Amount (Turn Over)"] - pivot["total"]
    pivot["Amazon Total Deducation %"] = (
        pivot["Amazon Total Deducation"] / pivot["Sales Amount (Turn Over)"] * 100
    ).round(2)
    pivot["profit"] = pivot["total"] - pivot["cp as per qty"]

    # 5. Refund IDs (only read 2 cols)
    ref_ids_df = pd.read_csv(io.BytesIO(refund_bytes), header=11,
                             usecols=["type","order id"], low_memory=False)
    refund_ids = set(ref_ids_df.loc[ref_ids_df["type"]=="Refund","order id"].dropna())

    # 6. Full refund rows (for display tab)
    ref_full = pd.read_csv(io.BytesIO(refund_bytes), header=11, low_memory=False)
    ref_full = ref_full[ref_full["type"]=="Refund"].reset_index(drop=True)

    # 7. Split pivot
    mask               = pivot["order id"].isin(refund_ids)
    netsale_refund_nan = pivot[~mask].copy()
    refunded           = pivot[mask].copy()

    # 8. Brand pivot
    bp = (
        netsale_refund_nan
        .groupby("brand", dropna=False)[
            ["quantity","Sales Amount (Turn Over)","total","cp as per qty","profit"]
        ].sum()
    )
    bp.loc["Grand Total"] = bp.sum()
    bp = bp.reset_index()
    bp["quantity"] = bp["quantity"].astype(int)
    bp = bp[["brand","quantity","Sales Amount (Turn Over)","total","cp as per qty","profit"]]

    return ns, ref_full, netsale_refund_nan, refunded, bp


# ── Main ──────────────────────────────────────────────────────────────────────
if csv_file and pm_file and refund_file:

    csv_bytes    = csv_file.read()
    pm_bytes     = pm_file.read()
    refund_bytes = refund_file.read()

    netsale, netsale_refund, netsale_refund_nan, refunded, brand_pivot = run_pipeline(
        csv_bytes, pm_bytes, refund_bytes
    )

    st.success(
        f"✅ {len(netsale):,} orders | "
        f"{len(netsale_refund):,} refund rows | "
        f"{len(netsale_refund_nan):,} clean orders"
    )

    # ── KPI Cards ─────────────────────────────────────────────────────────────
    grand = brand_pivot[brand_pivot["brand"]=="Grand Total"].iloc[0]
    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Total Quantity",    f"{int(grand['quantity']):,}")
    c2.metric("Sales (Turn Over)", fmt(grand["Sales Amount (Turn Over)"]))
    c3.metric("Transferred Price", fmt(grand["total"]))
    c4.metric("CP as per Qty",     fmt(grand["cp as per qty"]))
    c5.metric("P&L",               fmt(grand["profit"]))
    st.divider()

    # STYLE THRESHOLD: only apply Styler when rows are small enough to be fast
    STYLE_ROW_LIMIT = 5_000

    def show_table(df, fmt_dict=None, profit_col=None, height=450):
        """Display df — with styling only if small, raw dataframe if large."""
        if len(df) <= STYLE_ROW_LIMIT and fmt_dict:
            s = df.style.format(fmt_dict, na_rep="—")
            if profit_col and profit_col in df.columns:
                s = s.map(
                    lambda v: f"background-color: {'#d4edda' if v>=0 else '#f8d7da'}",
                    subset=[profit_col]
                )
            st.dataframe(s, use_container_width=True, height=height, hide_index=True)
        else:
            if len(df) > STYLE_ROW_LIMIT and fmt_dict:
                st.caption(f"ℹ️ Styling skipped for speed ({len(df):,} rows). Download for formatted view.")
            st.dataframe(df, use_container_width=True, height=height, hide_index=True)

    MONEY = "₹{:,.2f}"

    tab1,tab2,tab3,tab4,tab5,tab6 = st.tabs([
        "📊 Brand Summary","🔍 Order Detail","↩️ Refund Orders",
        "📋 netsale","🔄 netsale_refund","✅ netsale_refund_nan",
    ])

    # ── Tab 1: Brand Summary ──────────────────────────────────────────────────
    with tab1:
        st.subheader("Brand-wise Summary (excluding refunded orders)")
        col_f1, col_f2 = st.columns([3,1])
        with col_f1:
            brands_list = brand_pivot[brand_pivot["brand"]!="Grand Total"]["brand"].tolist()
            sel_brands  = st.multiselect("Filter by Brand", brands_list, default=brands_list)
        with col_f2:
            show_grand = st.checkbox("Show Grand Total", value=True)

        disp = brand_pivot[
            brand_pivot["brand"].isin(sel_brands) |
            ((brand_pivot["brand"]=="Grand Total") & show_grand)
        ].rename(columns={
            "brand":"Brand","quantity":"Quantity","total":"Transferred Price",
            "cp as per qty":"CP as per Qty","profit":"P&L"
        })[["Brand","Quantity","Sales Amount (Turn Over)","Transferred Price","CP as per Qty","P&L"]]

        show_table(disp,
            fmt_dict={"Sales Amount (Turn Over)":MONEY,"Transferred Price":MONEY,
                      "CP as per Qty":MONEY,"P&L":MONEY,"Quantity":"{:,}"},
            profit_col="P&L", height=600)

        st.download_button("⬇️ Download Brand Summary (Excel)",
            data=to_excel_bytes(disp), file_name="brand_summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_brand")

    # ── Tab 2: Order Detail ───────────────────────────────────────────────────
    with tab2:
        st.subheader("Order-level Detail (pivot, no refunds)")
        col_s1,col_s2,col_s3 = st.columns(3)
        with col_s1:
            bf = st.multiselect("Brand",
                options=sorted(netsale_refund_nan["brand"].dropna().unique()), key="det_brand")
        with col_s2:
            sf = st.text_input("SKU contains",  key="det_sku")
        with col_s3:
            af = st.text_input("ASIN contains", key="det_asin")

        det = netsale_refund_nan
        if bf: det = det[det["brand"].isin(bf)]
        if sf: det = det[det["sku"].str.contains(sf, case=False, na=False)]
        if af: det = det[det["asin"].str.contains(af, case=False, na=False)]

        cols = ["sku","order id","asin","brand","quantity","total",
                "cp as per qty","profit","Sales Amount (Turn Over)","Amazon Total Deducation %"]
        det = det[[c for c in cols if c in det.columns]]

        show_table(det,
            fmt_dict={"total":MONEY,"cp as per qty":MONEY,"profit":MONEY,
                      "Sales Amount (Turn Over)":MONEY,"Amazon Total Deducation %":"{:.2f}%"},
            profit_col="profit")
        st.caption(f"{len(det):,} rows")
        st.download_button("⬇️ Download Filtered Orders (Excel)",
            data=to_excel_bytes(det), file_name="order_detail.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_order")

    # ── Tab 3: Refund Orders ──────────────────────────────────────────────────
    with tab3:
        st.subheader("Orders with Refunds")
        ref = refunded[["sku","order id","asin","brand","quantity","total","profit"]].copy()
        show_table(ref, fmt_dict={"total":MONEY,"profit":MONEY}, profit_col="profit", height=400)
        st.caption(f"{len(ref):,} refunded orders")
        st.download_button("⬇️ Download Refund Orders (Excel)",
            data=to_excel_bytes(ref), file_name="refund_orders.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_refund")

    # ── Tab 4: netsale ────────────────────────────────────────────────────────
    with tab4:
        st.subheader("netsale — Enriched Order rows")
        st.caption(f"{len(netsale):,} rows × {netsale.shape[1]} cols")
        ns_q = st.text_input("Search SKU or Order ID", key="ns_q")
        ns_v = netsale
        if ns_q:
            ns_v = netsale[
                netsale["sku"].str.contains(ns_q,case=False,na=False) |
                netsale["order id"].astype(str).str.contains(ns_q,case=False,na=False)
            ]
        show_table(ns_v,
            fmt_dict={"product sales":MONEY,
                      "total sales tax liable(gst before adjusting tcs)":MONEY,
                      "total":MONEY,"cp":MONEY,"cp as per qty":MONEY})
        st.caption(f"Showing {len(ns_v):,} rows")
        st.download_button("⬇️ Download netsale (Excel)",
            data=to_excel_bytes(netsale), file_name="netsale_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_ns")

    # ── Tab 5: netsale_refund ─────────────────────────────────────────────────
    with tab5:
        st.subheader("netsale_refund — Refund-type rows from Refund CSV")
        st.caption(f"{len(netsale_refund):,} rows × {netsale_refund.shape[1]} cols")
        nr_q = st.text_input("Search SKU or Order ID", key="nr_q")
        nr_v = netsale_refund
        if nr_q:
            sc = "Sku" if "Sku" in nr_v.columns else "sku"
            oc = "order id" if "order id" in nr_v.columns else nr_v.columns[3]
            nr_v = netsale_refund[
                netsale_refund[sc].astype(str).str.contains(nr_q,case=False,na=False) |
                netsale_refund[oc].astype(str).str.contains(nr_q,case=False,na=False)
            ]
        show_table(nr_v)
        st.caption(f"Showing {len(nr_v):,} rows")
        st.download_button("⬇️ Download netsale_refund (Excel)",
            data=to_excel_bytes(netsale_refund), file_name="netsale_refund_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_nr")

    # ── Tab 6: netsale_refund_nan ─────────────────────────────────────────────
    with tab6:
        st.subheader("netsale_refund_nan — Pivot rows excluding refunded Order IDs")
        st.caption(f"{len(netsale_refund_nan):,} rows × {netsale_refund_nan.shape[1]} cols")
        col_n1,col_n2,col_n3 = st.columns(3)
        with col_n1:
            nb = st.multiselect("Brand",
                options=sorted(netsale_refund_nan["brand"].dropna().unique()), key="nan_b")
        with col_n2:
            ns2 = st.text_input("SKU contains",  key="nan_s")
        with col_n3:
            na2 = st.text_input("ASIN contains", key="nan_a")

        nv = netsale_refund_nan
        if nb:  nv = nv[nv["brand"].isin(nb)]
        if ns2: nv = nv[nv["sku"].str.contains(ns2,case=False,na=False)]
        if na2: nv = nv[nv["asin"].str.contains(na2,case=False,na=False)]

        show_table(nv,
            fmt_dict={"product sales":MONEY,
                      "total sales tax liable(gst before adjusting tcs)":MONEY,
                      "total":MONEY,"cp":MONEY,"cp as per qty":MONEY,
                      "Sales Amount (Turn Over)":MONEY,
                      "Amazon Total Deducation":MONEY,
                      "Amazon Total Deducation %":"{:.2f}%","profit":MONEY},
            profit_col="profit")
        st.caption(f"Showing {len(nv):,} rows")
        st.download_button("⬇️ Download netsale_refund_nan (Excel)",
            data=to_excel_bytes(netsale_refund_nan), file_name="netsale_refund_nan_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_nan")

else:
    st.info("👈 Please upload all three files in the sidebar to get started.")
    with st.expander("ℹ️ Expected file formats"):
        st.markdown("""
| File | Format | Notes |
|---|---|---|
| **Transaction CSV** | `.csv` | Amazon Unified Transaction report; data starts at row 12 |
| **PM Excel** | `.xlsx` | Columns: `amazon sku name`, `asin`, `brand manager`, `brand`, `cp` |
| **Refund CSV** | `.csv` | Same format as Transaction CSV; can cover a wider date range |
        """)
