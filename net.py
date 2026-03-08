import streamlit as st
import pandas as pd
import io

# Allow large dataframes to be styled without hitting the cell limit
pd.set_option("styler.render.max_elements", 2_000_000)

st.set_page_config(page_title="Net Sale Analyzer", page_icon="📦", layout="wide")

st.title("📦 Amazon Net Sale Analyzer")
st.markdown("Upload your **Unified Transaction CSV** and **Product Master Excel** to generate the sales report.")

# ── File Uploaders ────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    txn_file = st.file_uploader(
        "Upload Unified Transaction CSV",
        type=["csv"],
        help="The monthly transaction file (header starts at row 12, i.e. header=11)",
    )
with col2:
    pm_file = st.file_uploader(
        "Upload Product Master Excel (PM.xlsx)",
        type=["xlsx", "xls"],
    )

# ── Processing ────────────────────────────────────────────────────────────────
if txn_file and pm_file:
    with st.spinner("Processing data…"):

        # Load transaction CSV
        df = pd.read_csv(txn_file, header=11)
        netsale = df.copy()

        # Keep only Orders
        netsale = netsale[netsale["type"] == "Order"]

        # Clean product sales
        netsale["product sales"] = pd.to_numeric(netsale["product sales"], errors="coerce")
        netsale = netsale[netsale["product sales"] != 0]

        # Sort by order id
        netsale = netsale.sort_values(by="order id", ascending=True).reset_index(drop=True)

        # Load PM
        pm = pd.read_excel(pm_file)

        # Clean SKU columns
        netsale["Sku"] = netsale["Sku"].astype(str).str.strip()
        pm["Amazon Sku Name"] = pm["Amazon Sku Name"].astype(str).str.strip()

        # Map ASIN
        asin_map = dict(zip(pm["Amazon Sku Name"], pm["ASIN"]))
        netsale["ASIN"] = netsale["Sku"].map(asin_map)

        # Lowercase columns
        netsale.columns = netsale.columns.str.lower()
        pm.columns = pm.columns.str.lower()

        # Merge PM lookup
        pm_lookup = pm.iloc[:, [0, 4, 6, 9]]
        pm_lookup.columns = ["asin", "brand manager", "brand", "cp"]
        netsale = netsale.merge(pm_lookup, on="asin", how="left")

        # Clean total column
        netsale["total"] = (
            netsale["total"]
            .astype(str)
            .str.replace(",", "", regex=False)
            .astype(float)
        )

        # Build pivot (per SKU x Order ID)
        pivot = pd.pivot_table(
            netsale,
            index=["sku", "order id", "asin", "brand"],
            values=[
                "quantity",
                "product sales",
                "total sales tax liable(gst before adjusting tcs)",
                "total",
                "cp",
            ],
            aggfunc="sum",
        ).reset_index()

        # Sales Amount (Turn Over)
        pivot["product sales"] = pd.to_numeric(pivot["product sales"], errors="coerce")
        pivot["total sales tax liable(gst before adjusting tcs)"] = pd.to_numeric(
            pivot["total sales tax liable(gst before adjusting tcs)"], errors="coerce"
        )
        pivot["Sales Amount (Turn Over)"] = (
            pivot["product sales"]
            + pivot["total sales tax liable(gst before adjusting tcs)"]
        )

        # Amazon Total Deduction
        pivot["Amazon Total Deducation"] = (
            pivot["Sales Amount (Turn Over)"] - pivot["total"]
        )

        # Deduction %
        pivot["Amazon Total Deducation %"] = (
            pivot["Amazon Total Deducation"] / pivot["Sales Amount (Turn Over)"] * 100
        ).round(2)

        # CP as per Qty
        pivot["cp"] = pd.to_numeric(pivot["cp"], errors="coerce")
        pivot["quantity"] = pd.to_numeric(pivot["quantity"], errors="coerce")
        pivot["CP as per Qty"] = pivot["cp"] * pivot["quantity"]

        # Profit
        pivot["Profit"] = pivot["total"] - pivot["CP as per Qty"]

        # Refund flag
        netsale_refund = df.copy()
        netsale_refund = netsale_refund[netsale_refund["type"] == "Refund"]
        pivot["refund"] = pivot["order id"].where(
            pivot["order id"].isin(netsale_refund["order id"])
        )

        # Exclude refunded orders
        netsale_refund_nan = pivot[pivot["refund"].isna()].copy()

        # ── Brand pivot with exact requested column order ──────────────────────
        brand_raw = pd.pivot_table(
            netsale_refund_nan,
            index="brand",
            values=["quantity", "Sales Amount (Turn Over)", "total", "CP as per Qty", "Profit"],
            aggfunc="sum",
        )
        brand_raw.loc["Grand Total"] = brand_raw.sum()
        brand_raw = brand_raw.reset_index()

        # Exact order: Brand, Quantity, Sales Amount (Turn Over), Transferred Price, CP as per Qty, Profit
        brand_pivot = brand_raw[
            ["brand", "quantity", "Sales Amount (Turn Over)", "total", "CP as per Qty", "Profit"]
        ].copy()
        brand_pivot.columns = [
            "Brand", "Quantity", "Sales Amount (Turn Over)",
            "Transferred Price", "CP as per Qty", "Profit"
        ]

        # ── Pivot report (order-level) ─────────────────────────────────────────
        pivot_cols_available = [c for c in [
            "sku", "order id", "asin", "brand", "quantity",
            "Sales Amount (Turn Over)", "total",
            "CP as per Qty", "Profit",
            "Amazon Total Deducation", "Amazon Total Deducation %",
        ] if c in netsale_refund_nan.columns]

        pivot_report = netsale_refund_nan[pivot_cols_available].copy()
        # Rename 'total' to 'Transferred Price' in pivot report too
        pivot_report = pivot_report.rename(columns={"total": "Transferred Price"})

    st.success("✅ Data processed successfully!")

    # ── KPI Summary ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📊 Key Metrics (Excluding Refunded Orders)")

    grand = brand_pivot[brand_pivot["Brand"] == "Grand Total"].iloc[0]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Qty", f"{int(grand['Quantity']):,}")
    k2.metric("Sales Turnover", f"₹{grand['Sales Amount (Turn Over)']:,.0f}")
    k3.metric("Transferred Price", f"₹{grand['Transferred Price']:,.0f}")
    k4.metric("CP as per Qty", f"₹{grand['CP as per Qty']:,.0f}")
    profit_val = grand["Profit"]
    k5.metric("Profit / Loss", f"₹{profit_val:,.0f}")

    # ── Tabs ──────────────────────────────────────────────────────────────────
    st.markdown("---")
    tab1, tab2 = st.tabs(["🏷️ Brand-wise Summary", "🔍 Pivot Report"])

    # ═══════════════════════════════════════════════════════════════════════
    # TAB 1 — Brand-wise Summary
    # ═══════════════════════════════════════════════════════════════════════
    with tab1:
        st.subheader("Brand-wise Summary")
        st.caption("Brand  ·  Quantity  ·  Sales Amount (Turn Over)  ·  Transferred Price  ·  CP as per Qty  ·  Profit")

        brand_display = brand_pivot.copy()
        brand_display["Quantity"] = brand_display["Quantity"].apply(lambda x: f"{x:,.0f}")
        for col in ["Sales Amount (Turn Over)", "Transferred Price", "CP as per Qty", "Profit"]:
            brand_display[col] = brand_display[col].apply(lambda x: f"₹{x:,.2f}")

        def highlight_grand(row):
            if row["Brand"] == "Grand Total":
                return ["background-color: #1e3a5f; color: white; font-weight: bold"] * len(row)
            return [""] * len(row)

        def color_profit(val):
            try:
                num = float(str(val).replace("₹", "").replace(",", ""))
                if num < 0:
                    return "color: #ef4444; font-weight: bold"
                elif num > 0:
                    return "color: #22c55e; font-weight: bold"
            except Exception:
                pass
            return ""

        styled_brand = (
            brand_display.style
            .apply(highlight_grand, axis=1)
            .applymap(color_profit, subset=["Profit"])
        )
        st.dataframe(styled_brand, use_container_width=True, hide_index=True)

        buf_b = io.BytesIO()
        with pd.ExcelWriter(buf_b, engine="openpyxl") as writer:
            brand_pivot.to_excel(writer, sheet_name="Brand Summary", index=False)
        st.download_button(
            "📥 Download Brand Summary (Excel)",
            buf_b.getvalue(),
            file_name="brand_summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ═══════════════════════════════════════════════════════════════════════
    # TAB 2 — Pivot Report
    # ═══════════════════════════════════════════════════════════════════════
    with tab2:
        st.subheader("Pivot Report (Order-level, Refunds Excluded)")

        f1, f2 = st.columns([2, 3])
        with f1:
            brands_list = sorted(netsale_refund_nan["brand"].dropna().unique().tolist())
            selected_brands = st.multiselect("Filter by Brand", brands_list, default=[])
        with f2:
            search_val = st.text_input("Search by Order ID / SKU / ASIN")

        filtered = pivot_report.copy()
        if selected_brands:
            filtered = filtered[filtered["brand"].isin(selected_brands)]
        if search_val:
            mask = (
                filtered["order id"].astype(str).str.contains(search_val, case=False, na=False)
                | filtered["sku"].astype(str).str.contains(search_val, case=False, na=False)
                | filtered["asin"].astype(str).str.contains(search_val, case=False, na=False)
            )
            filtered = filtered[mask]

        total_rows = len(filtered)
        STYLE_ROW_LIMIT = 2000   # only apply Styler when rows are manageable

        st.write(f"Showing **{total_rows:,}** orders")

        fmt = {
            "quantity": "{:,.0f}",
            "Sales Amount (Turn Over)": "₹{:,.2f}",
            "Transferred Price": "₹{:,.2f}",
            "CP as per Qty": "₹{:,.2f}",
            "Profit": "₹{:,.2f}",
        }
        if "Amazon Total Deducation" in filtered.columns:
            fmt["Amazon Total Deducation"] = "₹{:,.2f}"
        if "Amazon Total Deducation %" in filtered.columns:
            fmt["Amazon Total Deducation %"] = "{:.2f}%"

        def color_profit_num(val):
            try:
                if float(val) < 0:
                    return "color: #ef4444; font-weight: bold"
                elif float(val) > 0:
                    return "color: #22c55e; font-weight: bold"
            except Exception:
                pass
            return ""

        if total_rows <= STYLE_ROW_LIMIT:
            # Small result — full styling is safe
            display_df = (
                filtered.reset_index(drop=True).style
                .applymap(color_profit_num, subset=["Profit"])
                .format(fmt, na_rep="-")
            )
        else:
            # Large result — skip Styler to avoid cell-limit crash; show plain table
            st.info(
                f"ℹ️ {total_rows:,} rows loaded. Use the Brand filter or search above to narrow "
                f"results — colour highlighting activates automatically for ≤ {STYLE_ROW_LIMIT:,} rows."
            )
            display_df = filtered.reset_index(drop=True)

        st.dataframe(display_df, use_container_width=True, height=450, hide_index=True)

        buf_p = io.BytesIO()
        with pd.ExcelWriter(buf_p, engine="openpyxl") as writer:
            pivot_report.to_excel(writer, sheet_name="Pivot Report", index=False)
        st.download_button(
            "📥 Download Pivot Report (Excel)",
            buf_p.getvalue(),
            file_name="pivot_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ── Full Excel Export ─────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("⬇️ Download Full Report (Both Sheets)")
    buf_full = io.BytesIO()
    with pd.ExcelWriter(buf_full, engine="openpyxl") as writer:
        brand_pivot.to_excel(writer, sheet_name="Brand Summary", index=False)
        pivot_report.to_excel(writer, sheet_name="Pivot Report", index=False)
    st.download_button(
        "📥 Download Full Report (Excel)",
        buf_full.getvalue(),
        file_name="netsale_full_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

else:
    st.info("👆 Please upload both files above to get started.")
    with st.expander("ℹ️ What does this app do?"):
        st.markdown("""
        This app replicates your Jupyter notebook workflow end-to-end:

        1. **Loads** the Amazon Unified Transaction CSV (skips the first 11 metadata rows)
        2. **Filters** to `Order` type rows with non-zero product sales
        3. **Maps ASINs** from the Product Master Excel using SKU
        4. **Merges** Brand Manager, Brand, and Cost Price (CP) from PM
        5. **Builds a pivot** per SKU × Order ID with turnover, deductions, and profit
        6. **Flags refunded orders** and excludes them from the final summary
        7. **Brand-wise Summary** — Brand · Quantity · Sales Amount (Turn Over) · Transferred Price · CP as per Qty · Profit
        8. **Pivot Report** — order-level detail with filters by Brand / Order ID / SKU / ASIN
        9. Lets you **download** both reports individually or as a full Excel workbook
        """)