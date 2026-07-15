"""
Heitek SPA/Rebate Line Item Extractor
--------------------------------------
Upload one or more .msg files (each containing a PriceSheetCreation.pdf request
form and a supplier SPA/Rebate PDF). The app extracts the line-item table from
the supplier PDF and maps it into the 41-column contract schema, populating:

    sku              <- Model Number
    list_price       <- List Price
    multiplier       <- Dist Multi
    contract_price   <- Dist Net

All other columns are left blank, matching the target header exactly.
"""

import io
import re
import tempfile
from pathlib import Path

import pandas as pd
import pdfplumber
import streamlit as st
import extract_msg

# ---------------------------------------------------------------------------
# Target output schema (order matters — must match exactly)
# ---------------------------------------------------------------------------
OUTPUT_COLUMNS = [
    "contract_id", "manufacturer", "customer_number", "customer_name",
    "customer_address", "customer_city", "customer_state", "customer_zip",
    "sic", "naics", "customer_country", "customer_duns", "latitude",
    "longitude", "line_of_business", "website", "customer_phone",
    "contract_start", "contract_end", "sku", "product_group", "list_price",
    "multiplier", "contract_price", "uom", "product_start", "product_end",
    "target_price", "target_percentage", "distributor_account_number",
    "vendor_id", "branch_code", "flag_spa", "requested_margin", "competitor",
    "max_quantity", "max_quantity_duration", "competitor_price",
    "oem_discount", "rebate_percentage", "expected_sales",
    "supplier_customer_number",
]

REQUEST_FORM_NAME_HINTS = ("pricesheetcreation",)

# ---------------------------------------------------------------------------
# Supplier list for the dropdown
# ---------------------------------------------------------------------------
SUPPLIERS = [
    "8020 INC",
    "Abb Motors And Mechanical Inc",
    "Banner Engineering Corporation",
    "Fabco-Air Inc",
    "Festo Corporation",
    "Leuze Electronic Inc",
    "Murr Elektronik",
    "PHOENIX CONTACT",
    "RITTAL NORTH AMERICA LLC",
    "Robroy Industries Inc",
    "Schmersal",
    "Siemens Industry Inc",
    "Tolomatic",
    "Turck Inc",
    "WEIDMULLER",
    "Yeager Machine Inc",
]


def money_to_float(val):
    """'$30.00' -> 30.0 ; '' or None -> None"""
    if val is None:
        return None
    s = str(val).replace("$", "").replace(",", "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pct_or_float(val):
    """'0.3500' -> 0.35 ; already-numeric strings pass through"""
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def find_supplier_pdf(attachments):
    """Pick the attachment that is NOT the PriceSheetCreation request form."""
    pdf_atts = [
        a for a in attachments
        if (a.longFilename or a.shortFilename or "").lower().endswith(".pdf")
    ]
    for a in pdf_atts:
        fname = (a.longFilename or a.shortFilename or "").lower()
        if not any(hint in fname for hint in REQUEST_FORM_NAME_HINTS):
            return a
    # Fallback: if only one PDF exists, use it
    return pdf_atts[0] if pdf_atts else None


def find_request_form_pdf(attachments):
    """Pick the PriceSheetCreation.pdf request-form attachment specifically."""
    pdf_atts = [
        a for a in attachments
        if (a.longFilename or a.shortFilename or "").lower().endswith(".pdf")
    ]
    for a in pdf_atts:
        fname = (a.longFilename or a.shortFilename or "").lower()
        if any(hint in fname for hint in REQUEST_FORM_NAME_HINTS):
            return a
    return None


def extract_requested_margin(pdf_bytes):
    """Pulls the 'Requested Margin * NN.NNNN %' value from the request form PDF."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    match = re.search(r"Requested Margin\s*\*?\s*\n?\s*([\d.]+)\s*%", text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def extract_line_items_banner(pdf_bytes):
    """
    Parser for Banner Engineering Corporation SPA/Rebate PDFs.
    Scans every table on every page for a header row containing
    'Model Number' and 'List Price', then pulls data rows beneath it.
    Column positions are fixed relative to the header (Part#, Model#,
    List Price, Dist Multi, Dist Net, ...) based on Banner's template.
    """
    rows = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table:
                    continue
                header_idx = None
                for i, row in enumerate(table):
                    joined = " ".join(str(c) for c in row if c)
                    if "Model Number" in joined and "List Price" in joined:
                        header_idx = i
                        break
                if header_idx is None:
                    continue

                for data_row in table[header_idx + 1:]:
                    if not data_row or len(data_row) < 5:
                        continue
                    part_number = data_row[0]
                    model_number = data_row[1]
                    list_price = data_row[2]
                    dist_multi = data_row[3]
                    dist_net = data_row[4]

                    # Skip blank / header-repeat rows (e.g. empty "Existing" table)
                    if not model_number or not str(model_number).strip():
                        continue

                    rows.append({
                        "sku": str(model_number).replace("\n", "").strip(),
                        "list_price": money_to_float(list_price),
                        "multiplier": pct_or_float(dist_multi),
                        "contract_price": money_to_float(dist_net),
                    })
    return rows


# ---------------------------------------------------------------------------
# Supplier -> parser registry.
# Add an entry here once a supplier's PDF layout has been mapped. Suppliers
# not yet in this dict will show a "not yet configured" message in the UI —
# send a sample .msg for that supplier to add support.
# ---------------------------------------------------------------------------
SUPPLIER_PARSERS = {
    "Banner Engineering Corporation": extract_line_items_banner,
}


def process_msg_file(file_bytes, filename, supplier):
    """Returns a list of dict rows extracted from one .msg file."""
    parser = SUPPLIER_PARSERS.get(supplier)
    if parser is None:
        return [], (
            f"{filename}: no parser configured yet for '{supplier}' — "
            f"send a sample .msg for this supplier to add support"
        )

    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        msg = extract_msg.Message(tmp_path)
        supplier_pdf_att = find_supplier_pdf(msg.attachments)

        if supplier_pdf_att is None:
            return [], f"{filename}: no supplier PDF attachment found"

        pdf_bytes = supplier_pdf_att.data
        rows = parser(pdf_bytes)

        if not rows:
            return [], f"{filename}: no line items found in supplier PDF"

        # Pull Requested Margin from the request-form PDF and stamp it onto
        # every line item row from this .msg
        requested_margin = None
        form_pdf_att = find_request_form_pdf(msg.attachments)
        if form_pdf_att is not None:
            requested_margin = extract_requested_margin(form_pdf_att.data)

        for r in rows:
            r["requested_margin"] = requested_margin
            r["manufacturer"] = supplier
            r["_source_file"] = filename

        return rows, None
    except Exception as e:
        return [], f"{filename}: error — {e}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SPA Line Item Extractor",
    page_icon="📋",
    layout="wide",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap');

html, body, [class*="css"]  { font-family: 'Inter', sans-serif; }

/* App background */
.stApp { background-color: #F4F6F9; }

/* Hide default Streamlit chrome we don't need */
#MainMenu, footer { visibility: hidden; }

/* Header banner */
.app-header {
    background: linear-gradient(135deg, #0B1F3A 0%, #14335E 100%);
    padding: 28px 36px;
    border-radius: 14px;
    margin-bottom: 28px;
    box-shadow: 0 4px 18px rgba(11,31,58,0.18);
}
.app-header h1 {
    color: #FFFFFF;
    font-size: 26px;
    font-weight: 700;
    margin: 0 0 6px 0;
}
.app-header p {
    color: #A9BBD6;
    font-size: 14px;
    margin: 0;
}
.app-header .schema-tag {
    display: inline-block;
    margin-top: 12px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: #7CD4C6;
    background: rgba(124,212,198,0.12);
    border: 1px solid rgba(124,212,198,0.35);
    padding: 4px 10px;
    border-radius: 6px;
}

/* Card container for controls */
.control-card {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(15,23,42,0.04);
}

/* Section divider labels */
.section-label {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #1D4ED8;
    border-left: 3px solid #1D4ED8;
    padding-left: 10px;
    margin: 6px 0 14px 0;
}

/* Metrics styling */
div[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-left: 4px solid #0EA5A0;
    border-radius: 10px;
    padding: 14px 18px;
    box-shadow: 0 1px 3px rgba(15,23,42,0.04);
}

/* Buttons */
.stButton > button {
    border-radius: 8px;
    font-weight: 600;
    border: 1px solid #E2E8F0;
}
.stButton > button[kind="primary"] {
    background-color: #1D4ED8;
    border: none;
}
.stButton > button[kind="primary"]:hover {
    background-color: #1640B0;
}

/* Expander (per-file dashboard cards) */
div[data-testid="stExpander"] {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
    box-shadow: 0 1px 3px rgba(15,23,42,0.04);
    margin-bottom: 10px;
}

/* Dataframe corners */
div[data-testid="stDataFrame"] {
    border-radius: 8px;
    overflow: hidden;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="app-header">
    <h1>📋 Heitek SPA / Rebate Line Item Extractor</h1>
    <p>Select a supplier, upload .msg files, and extract line items + requested margin straight from the PDFs.</p>
    <span class="schema-tag">sku · list_price · multiplier · contract_price · requested_margin · manufacturer</span>
</div>
""", unsafe_allow_html=True)

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0
if "result_df" not in st.session_state:
    st.session_state.result_df = None
if "raw_df" not in st.session_state:
    st.session_state.raw_df = None
if "errors" not in st.session_state:
    st.session_state.errors = []

st.markdown('<div class="control-card">', unsafe_allow_html=True)
st.markdown('<div class="section-label">1 · Choose supplier</div>', unsafe_allow_html=True)
supplier = st.selectbox("Supplier", SUPPLIERS, label_visibility="collapsed")

if supplier not in SUPPLIER_PARSERS:
    st.info(
        f"No parser configured yet for **{supplier}**. Upload will still "
        f"work, but files will come back as errors until a sample PDF for "
        f"this supplier is used to add support."
    )

st.markdown('<div class="section-label" style="margin-top:18px;">2 · Upload .msg files</div>', unsafe_allow_html=True)
uploaded_files = st.file_uploader(
    "Upload .msg files",
    type=["msg"],
    accept_multiple_files=True,
    key=f"uploader_{st.session_state.uploader_key}",
    label_visibility="collapsed",
)

col1, col2 = st.columns([1, 1])
with col1:
    process_clicked = st.button(
        f"Process {len(uploaded_files)} file(s)" if uploaded_files else "Process",
        type="primary",
        disabled=not uploaded_files,
        use_container_width=True,
    )
with col2:
    clear_clicked = st.button("Clear", use_container_width=True)

st.markdown('</div>', unsafe_allow_html=True)

if clear_clicked:
    st.session_state.uploader_key += 1
    st.session_state.result_df = None
    st.session_state.raw_df = None
    st.session_state.errors = []
    st.rerun()

if process_clicked and uploaded_files:
    all_rows = []
    errors = []
    progress = st.progress(0, text="Starting...")

    for i, f in enumerate(uploaded_files):
        progress.progress(
            (i) / len(uploaded_files), text=f"Processing {f.name}..."
        )
        rows, err = process_msg_file(f.read(), f.name, supplier)
        all_rows.extend(rows)
        if err:
            errors.append(err)

    progress.progress(1.0, text="Done")

    st.session_state.errors = errors
    if all_rows:
        df = pd.DataFrame(all_rows)
        st.session_state.raw_df = df
        # Reindex into full 41-column schema, blank elsewhere
        st.session_state.result_df = df.reindex(columns=OUTPUT_COLUMNS)
    else:
        st.session_state.raw_df = None
        st.session_state.result_df = None

if st.session_state.errors:
    st.warning("Some files had issues:")
    for e in st.session_state.errors:
        st.text(f"⚠ {e}")

if st.session_state.raw_df is not None:
    raw_df = st.session_state.raw_df

    st.markdown('<div class="section-label" style="margin-top:6px;">Dashboard · line items & margin from PDFs</div>', unsafe_allow_html=True)

    files = raw_df["_source_file"].unique().tolist()
    margins = raw_df.groupby("_source_file")["requested_margin"].first()

    m1, m2, m3 = st.columns(3)
    m1.metric("Files processed", len(files))
    m2.metric("Total line items", len(raw_df))
    avg_margin = margins.dropna().mean()
    m3.metric(
        "Avg requested margin",
        f"{avg_margin:.2f}%" if pd.notna(avg_margin) else "—",
    )

    summary_df = raw_df.groupby("_source_file").agg(
        line_items=("sku", "count"),
        requested_margin=("requested_margin", "first"),
    ).reset_index().rename(columns={"_source_file": "file"})
    summary_df["requested_margin"] = summary_df["requested_margin"].apply(
        lambda v: f"{v:.2f}%" if pd.notna(v) else "not found"
    )

    st.dataframe(summary_df, use_container_width=True, hide_index=True)

if st.session_state.result_df is not None:
    full_df = st.session_state.result_df
    st.markdown('<div class="section-label" style="margin-top:24px;">Export · full 41-column schema</div>', unsafe_allow_html=True)
    st.success(f"Extracted {len(full_df)} line item row(s).")
    st.dataframe(full_df, use_container_width=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        full_df.to_excel(writer, index=False, sheet_name="Line Items")
    buf.seek(0)

    st.download_button(
        "Download Excel",
        data=buf,
        file_name="spa_line_items.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
elif not uploaded_files:
    st.info("Upload one or more .msg files to begin.")

