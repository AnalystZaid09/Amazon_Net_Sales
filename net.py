import streamlit as st
import pandas as pd
import io

# Allow large dataframes to be styled without hitting the default 262144-cell cap
pd.set_option("styler.render.max_elements", 10_000_000)

st.set_page_config(page_title="Net Sale Dashboard", page_icon="📦", layout="wide")

st.title("📦 Amazon Net Sale Dashboard")
st.markdown("Upload your transaction CSV and PM Excel file to generate the sales report.")

# ── Sidebar: File Uploads ────────────────────────────────────────────────────
with st.sidebar:
    st.header("📂 Upload Files")
    csv_file = st.file_uploader(
        "Transaction CSV (header at row 12)",
        type=["csv"],
        help="Amazon Unified Transaction report CSV"
    )
    pm_file = st.file_uploader(
        "PM Excel (Purchase Master)",
        type=["xlsx", "xls"],
        help="Purchase Master with SKU → ASIN mapping"
    )
    refund_file = st.file_uploader(
        "Refund CSV (header at row 12)",
        type=["csv"],
        help="Refund / full-period transaction CSV"
    )

# ── Helper ───────────────────────────────────────────────────────────────────
def fmt(val):
    try:
        return f"₹{val:,.2f}"
    except Exception:
        return val


def highlight_profit(val):
    color = "#d4edda" if val >= 0 else "#f8d7da"
    return f"background-color: {color}"


# ── Main Pipeline ────────────────────────────────────────────────────────────
if csv_file and pm_file and refund_file:

    # ── Step 1-6: Load & filter orders ──────────────────────────────────────
    with st.spinner("Loading transaction data…"):
        df = pd.read_csv(csv_file, header=11, low_memory=False)
        netsale = df.copy()
        netsale = netsale[netsale["type"] == "Order"]
        netsale["product sales"] = pd.to_numeric(netsale["product sales"], errors="coerce")
        netsale = netsale[netsale["product sales"] != 0]
        netsale = netsale.sort_values(by="order id", ascending=True).reset_index(drop=True)

    # ── Step 7-10: Load PM & map ASIN ───────────────────────────────────────
    with st.spinner("Loading purchase master…"):
        pm = pd.read_excel(pm_file)
        netsale["Sku"] = netsale["Sku"].astype(str).str.strip()
        pm["Amazon Sku Name"] = pm["Amazon Sku Name"].astype(str).str.strip()
        asin_map = dict(zip(pm["Amazon Sku Name"], pm["ASIN"]))
        netsale["ASIN"] = netsale["Sku"].map(asin_map)

    # ── Step 11: Lower-case columns ──────────────────────────────────────────
    netsale.columns = netsale.columns.str.lower()
    pm.columns = pm.columns.str.lower()

    # ── Step 12: Map brand manager, brand, cp ────────────────────────────────
    pm_lookup = pm.iloc[:, [0, 4, 6, 9]].copy()
    pm_lookup.columns = ["asin", "brand manager", "brand", "cp"]
    # Drop duplicate ASINs so set_index produces a unique index
    pm_lookup = pm_lookup.drop_duplicates(subset="asin")
    brand_manager_map = pm_lookup.set_index("asin")["brand manager"]
    brand_map         = pm_lookup.set_index("asin")["brand"]
    cp_map            = pm_lookup.set_index("asin")["cp"]
    netsale["brand manager"] = netsale["asin"].map(brand_manager_map)
    netsale["brand"]         = netsale["asin"].map(brand_map)
    netsale["cp"]            = netsale["asin"].map(cp_map)

    # ── Step 14: Clean 'total' ────────────────────────────────────────────────
    netsale["total"] = (
        netsale["total"]
        .astype(str)
        .str.replace(",", "", regex=False)
        .astype(float)
    )

    # ── Step 16: Pivot table ──────────────────────────────────────────────────
    with st.spinner("Building pivot table…"):
        pivot = pd.pivot_table(
            netsale,
            index=["sku", "order id", "asin", "brand"],
            values=[
                "quantity",
                "product sales",
                "total sales tax liable(gst before adjusting tcs)",
                "total",
                "cp"
            ],
            aggfunc="sum"
        ).reset_index()

    # ── Steps 18-23: Derived columns ─────────────────────────────────────────
    pivot["product sales"] = pd.to_numeric(pivot["product sales"], errors="coerce")
    pivot["total sales tax liable(gst before adjusting tcs)"] = pd.to_numeric(
        pivot["total sales tax liable(gst before adjusting tcs)"], errors="coerce"
    )
    # Sales Amount (Turn Over) = product sales + GST
    # If product sales col is 0 (common in Amazon reports where it rolls into GST col),
    # fall back to using `total` (net transferred) as the turnover basis
    raw_turnover = (
        pivot["product sales"] +
        pivot["total sales tax liable(gst before adjusting tcs)"]
    )
    pivot["Sales Amount (Turn Over)"] = raw_turnover.where(raw_turnover != 0, pivot["total"])
    pivot["Amazon Total Deducation"] = pivot["Sales Amount (Turn Over)"] - pivot["total"]
    pivot["Amazon Total Deducation %"] = (
        pivot["Amazon Total Deducation"] / pivot["Sales Amount (Turn Over)"] * 100
    ).round(2)
    pivot["cp"]       = pd.to_numeric(pivot["cp"], errors="coerce")
    pivot["quantity"] = pd.to_numeric(pivot["quantity"], errors="coerce")
    pivot["CP as per Qty"] = pivot["cp"] * pivot["quantity"]
    pivot["Profit"]        = pivot["total"] - pivot["CP as per Qty"]

    # ── Step 24-28: Load refund & flag ───────────────────────────────────────
    with st.spinner("Loading refund data…"):
        netsale_refund = pd.read_csv(refund_file, header=11, low_memory=False)
        netsale_refund = netsale_refund[netsale_refund["type"] == "Refund"]
    pivot["refund"] = pivot["order id"].where(
        pivot["order id"].isin(netsale_refund["order id"])
    )

    # ── Step 30: Non-refund rows ──────────────────────────────────────────────
    netsale_refund_nan = pivot[pivot["refund"].isna() | (pivot["refund"] == "")]

    # ── Step 32-33: Brand pivot ───────────────────────────────────────────────
    brand_pivot = pd.pivot_table(
        netsale_refund_nan,
        index="brand",
        values=["quantity", "Sales Amount (Turn Over)", "total", "CP as per Qty", "Profit"],
        aggfunc="sum"
    )
    brand_pivot.loc["Grand Total"] = brand_pivot.sum()
    brand_pivot = brand_pivot.reset_index()
    brand_pivot["quantity"] = brand_pivot["quantity"].astype(int)
    # Apply correct column order: Brand, quantity, Sales Amount (Turn Over), total, CP as per Qty, Profit
    brand_pivot = brand_pivot[["brand", "quantity", "Sales Amount (Turn Over)", "total", "CP as per Qty", "Profit"]]

    # ════════════════════════════════════════════════════════════════════════
    # DISPLAY
    # ════════════════════════════════════════════════════════════════════════

    st.success(f"✅ Data loaded — {len(netsale):,} orders | {len(netsale_refund):,} refunds")

    # ── KPI Cards ────────────────────────────────────────────────────────────
    grand = brand_pivot[brand_pivot["brand"] == "Grand Total"].iloc[0]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Quantity", f"{int(grand['quantity']):,}")
    c2.metric("Sales Amount (Turn Over)", fmt(grand["Sales Amount (Turn Over)"]))
    c3.metric("Transferred Price", fmt(grand["total"]))
    c4.metric("CP as per Qty", fmt(grand["CP as per Qty"]))
    profit_val = grand["Profit"]
    c5.metric("P&L", fmt(profit_val), delta=None)

    st.divider()

    # ── Tabs ─────────────────────────────────────────────────────────────────
    tab1, tab2, tab3 = st.tabs(["📊 Brand Summary", "🔍 Order Detail", "↩️ Refund Orders"])

    # ── Tab 1: Brand Summary ─────────────────────────────────────────────────
    with tab1:
        st.subheader("Brand-wise Summary (excluding refunded orders)")

        # Filter controls
        col_f1, col_f2 = st.columns([3, 1])
        with col_f1:
            brands_list = brand_pivot[brand_pivot["brand"] != "Grand Total"]["brand"].tolist()
            selected_brands = st.multiselect("Filter by Brand", brands_list, default=brands_list)
        with col_f2:
            show_grand = st.checkbox("Show Grand Total row", value=True)

        display_bp = brand_pivot[
            (brand_pivot["brand"].isin(selected_brands)) |
            (brand_pivot["brand"] == "Grand Total" if show_grand else False)
        ].copy()

        # Rename for display — exact column order: Brand, quantity, Sales Amount (Turn Over), Transferred Price, CP as per Qty, P&L
        display_bp = display_bp.rename(columns={
            "brand": "Brand",
            "quantity": "quantity",
            "Sales Amount (Turn Over)": "Sales Amount (Turn Over)",
            "total": "Transferred Price",
            "CP as per Qty": "CP as per Qty",
            "Profit": "P&L"
        })
        # Enforce column order
        display_bp = display_bp[["Brand", "quantity", "Sales Amount (Turn Over)", "Transferred Price", "CP as per Qty", "P&L"]]

        styled = (
            display_bp.style
            .map(highlight_profit, subset=["P&L"])
            .format({
                "Sales Amount (Turn Over)": "₹{:,.2f}",
                "Transferred Price": "₹{:,.2f}",
                "CP as per Qty": "₹{:,.2f}",
                "P&L": "₹{:,.2f}",
                "quantity": "{:,}"
            })
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)

        # Download
        buf = io.BytesIO()
        display_bp.to_excel(buf, index=False)
        st.download_button(
            "⬇️ Download Brand Summary (Excel)",
            data=buf.getvalue(),
            file_name="brand_summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # ── Tab 2: Order Detail ──────────────────────────────────────────────────
    with tab2:
        st.subheader("Order-level Detail (pivot, no refunds)")

        col_s1, col_s2, col_s3 = st.columns(3)
        with col_s1:
            brand_filter = st.multiselect(
                "Brand",
                options=sorted(netsale_refund_nan["brand"].dropna().unique()),
                key="detail_brand"
            )
        with col_s2:
            sku_filter = st.text_input("SKU contains", key="detail_sku")
        with col_s3:
            asin_filter = st.text_input("ASIN contains", key="detail_asin")

        detail = netsale_refund_nan.copy()
        if brand_filter:
            detail = detail[detail["brand"].isin(brand_filter)]
        if sku_filter:
            detail = detail[detail["sku"].str.contains(sku_filter, case=False, na=False)]
        if asin_filter:
            detail = detail[detail["asin"].str.contains(asin_filter, case=False, na=False)]

        cols_show = ["sku", "order id", "asin", "brand", "quantity",
                     "total", "CP as per Qty", "Profit", "Sales Amount (Turn Over)",
                     "Amazon Total Deducation %"]
        detail_show = detail[[c for c in cols_show if c in detail.columns]]

        MAX_STYLE_CELLS = 2_000_000
        if detail_show.size <= MAX_STYLE_CELLS:
            rendered = (
                detail_show.style
                .map(highlight_profit, subset=["Profit"])
                .format({
                    "total": "₹{:,.2f}",
                    "CP as per Qty": "₹{:,.2f}",
                    "Profit": "₹{:,.2f}",
                    "Sales Amount (Turn Over)": "₹{:,.2f}",
                    "Amazon Total Deducation %": "{:.2f}%"
                }, na_rep="—")
            )
        else:
            st.warning(
                f"Table has {detail_show.size:,} cells — showing without colour styling. "
                "Use the filters above to narrow results."
            )
            rendered = detail_show
        st.dataframe(rendered, use_container_width=True, height=450)
        st.caption(f"Showing {len(detail_show):,} rows")

        buf2 = io.BytesIO()
        detail_show.to_excel(buf2, index=False)
        st.download_button(
            "⬇️ Download Filtered Orders (Excel)",
            data=buf2.getvalue(),
            file_name="order_detail.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # ── Tab 3: Refund Orders ─────────────────────────────────────────────────
    with tab3:
        st.subheader("Orders with Refunds")

        refunded = pivot[pivot["refund"].notna() & (pivot["refund"] != "")].copy()
        refunded_show = refunded[["sku", "order id", "asin", "brand",
                                   "quantity", "total", "Profit", "refund"]].copy()
        refunded_show = refunded_show.rename(columns={"refund": "Refund Order ID"})

        st.dataframe(
            refunded_show.style.format({
                "total": "₹{:,.2f}",
                "Profit": "₹{:,.2f}"
            }, na_rep="—"),
            use_container_width=True,
            height=400
        )
        st.caption(f"{len(refunded_show):,} orders have matching refunds")

        buf3 = io.BytesIO()
        refunded_show.to_excel(buf3, index=False)
        st.download_button(
            "⬇️ Download Refund Orders (Excel)",
            data=buf3.getvalue(),
            file_name="refund_orders.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

else:
    st.info("👈 Please upload all three files in the sidebar to get started.")

    with st.expander("ℹ️ Expected file formats"):
        st.markdown("""
| File | Format | Notes |
|---|---|---|
| **Transaction CSV** | `.csv` | Amazon Unified Transaction report; actual data starts at row 12 |
| **PM Excel** | `.xlsx` | Columns needed: col 0 = Amazon SKU Name, col 4 = Brand Manager, col 6 = Brand, col 9 = CP |
| **Refund CSV** | `.csv` | Same format as Transaction CSV; can be a wider date-range file |
        """)
