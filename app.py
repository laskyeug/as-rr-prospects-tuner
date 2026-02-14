import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    [data-testid="stSidebar"] {width: 250px !important;}
    div[data-testid="stMetric"] {padding: 0px 0px 5px 0px;}
    .stCheckbox {margin-bottom: -15px;}
    </style>
    """, unsafe_allow_html=True)

def merge_tags(series):
    all_tags = "*".join(series.astype(str)).split('*')
    unique_tags = sorted(list(set([t.strip() for t in all_tags if t.strip()])))
    return " * ".join(unique_tags)

def merge_rows(series):
    # Combines original row numbers into a string for traceability
    return ", ".join(series.astype(str))

@st.cache_data(ttl=3600)
def load_data():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    try:
        sheet = client.open("SAMHSA_Master_Data").sheet1
        df = pd.DataFrame(sheet.get_all_records())
        # Store original row number (Sheets starts at 1, header is 1, so data starts at 2)
        df['orig_row'] = df.index + 2
        
        # --- DEDUPLICATION ---
        df['city_clean'] = df['city'].astype(str).str.title()
        df['state_clean'] = df['state'].astype(str).str.upper()
        
        rollup = df.groupby(['name1', 'city_clean', 'state_clean']).agg({
            'service_code_info': merge_tags,
            'phone': 'first',
            'orig_row': merge_rows
        }).reset_index()
        
        rollup['Location'] = rollup['city_clean'] + ", " + rollup['state_clean']
        rollup['raw_comp'] = rollup['service_code_info'].str.split('*').str.len()
        
        u_max = rollup['raw_comp'].max()
        rollup['Propensity Score'] = ((rollup['raw_comp'] / u_max) * 100).round(0).astype(int)
        
        return rollup, u_max, len(df)
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

# --- 2. TUNER RIBBON ---
st.sidebar.title("🎯 Prospect Filters")

st.sidebar.subheader("Care Type(s) To Include")
inc_res = st.sidebar.checkbox("Residential", value=True)
inc_dtx = st.sidebar.checkbox("Detox (DT)")
inc_hosp = st.sidebar.checkbox("Hospital / Inpatient")

st.sidebar.divider()

st.sidebar.subheader("Propensity Threshold")
min_propensity = st.sidebar.slider("Min. Propensity Score", 0, 100, 40, step=1)

# New Legend of Inputs
st.sidebar.info(f"""
**Score Drivers:**
* **Scale:** Based on {u_max} total service tags.
* **100:** Merged SUD/MH licenses + Full Continuum.
* **80:** High-density specialty programs.
""")

st.sidebar.divider()

st.sidebar.subheader("Settings")
exclude_gov = st.sidebar.toggle("Exclude Govt/VAMC", value=True)
only_private = st.sidebar.toggle("Only Private For-Profit")
max_show = st.sidebar.number_input("Max Rows Shown", value=1000)

# --- 3. FILTER ENGINE ---
d, u_max, total_raw = load_data()
d_filtered = d.copy()

patterns = []
if inc_res: patterns.append("RES|RL|RS")
if inc_dtx: patterns.append("DT")
if inc_hosp: patterns.append("HI|PSY")

if patterns:
    combined_pattern = "|".join(patterns)
    d_filtered = d_filtered[d_filtered['service_code_info'].str.contains(combined_pattern, case=False, na=False)]
else:
    d_filtered = pd.DataFrame(columns=d.columns)

d_filtered = d_filtered[d_filtered['Propensity Score'] >= min_propensity]
if exclude_gov:
    d_filtered = d_filtered[~d_filtered['service_code_info'].str.contains('STG|FED|VAMC', case=False, na=False)]
if only_private:
    d_filtered = d_filtered[d_filtered['service_code_info'].str.contains('PVTP', case=False, na=False)]

d_filtered = d_filtered.sort_values(by=['Propensity Score', 'name1'], ascending=[False, True])

# --- 4. MAIN OUTPUT PANE ---
st.title("📊 Scored Prospects")

# Metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Universe Total", f"{total_raw:,}")
m2.metric("Qualifying Facilities", f"{len(d_filtered):,}")
m3.metric("Avg Propensity", f"{int(d_filtered['Propensity Score'].mean()) if not d_filtered.empty else 0}%")
m4.metric("Score Ceiling", f"{u_max} Tags")

# Filters
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter by State", options=sorted(d['state_clean'].unique()))

if search: d_filtered = d_filtered[d_filtered['name1'].str.lower().str.contains(search)]
if states: d_filtered = d_filtered[d_filtered['state_clean'].isin(states)]

# Table Display
output_df = d_filtered.head(max_show).reset_index(drop=True)
output_df.index = output_df.index + 1
output_df.index.name = "Rank"

output_df = output_df.rename(columns={
    'name1': 'Facility Name', 
    'phone': 'Phone',
    'orig_row': 'Source Row(s)'
})

st.dataframe(
    output_df[['Facility Name', 'Location', 'Phone', 'Propensity Score', 'Source Row(s)']], 
    use_container_width=True,
    height=550
)

st.download_button("📥 Download Scored List (CSV)", d_filtered.to_csv(index=False).encode('utf-8'), "Scored_Prospects.csv")
