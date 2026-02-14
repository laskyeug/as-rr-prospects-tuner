import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    [data-testid="stSidebar"] {width: 280px !important;}
    div[data-testid="stMetric"] {padding: 0px 0px 5px 0px;}
    /* Fix table alignment and fonts */
    .stDataFrame {align-items: left;}
    </style>
    """, unsafe_allow_html=True)

@st.cache_data(ttl=3600)
def load_data():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    try:
        sheet = client.open("SAMHSA_Master_Data").sheet1
        df = pd.DataFrame(sheet.get_all_records())
        df.index = df.index + 2
        df.index.name = "Master Row #"
        # Correctly split by asterisk to determine complexity
        df['complexity'] = df['service_code_info'].str.split('*').str.len().fillna(0)
        df['service_code_info'] = df['service_code_info'].fillna('').astype(str)
        return df
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

# --- 2. TUNER RIBBON (REFINED) ---
st.sidebar.title("🎯 Prospect Filters")

st.sidebar.subheader("Target Service Tiers (OR)")
inc_res = st.sidebar.checkbox("Residential", value=True)
inc_dtx = st.sidebar.checkbox("Detox (DT)")
inc_hosp = st.sidebar.checkbox("Hospital / Inpatient")

st.sidebar.divider()

st.sidebar.subheader("Maturity Filter")
min_comp = st.sidebar.slider(
    "Minimum Facility Maturity", 
    min_value=5, max_value=40, value=15,
    help="Higher maturity filters for facilities with more specialized sub-services."
)

# Intuitive Label for Maturity
if min_comp >= 30: quality_label = "High-Acuity Campus"
elif min_comp >= 20: quality_label = "Comprehensive"
elif min_comp >= 10: quality_label = "Standard"
else: quality_label = "Boutique/Specialty"

st.sidebar.info(f"Current Filter: **{quality_label}**")

st.sidebar.divider()

st.sidebar.subheader("Global Settings")
exclude_gov = st.sidebar.toggle("Exclude Govt/VAMC", value=True)
only_private = st.sidebar.toggle("Only Private For-Profit")
max_show = st.sidebar.number_input("Max Records", value=1000)

# --- 3. FILTER ENGINE ---
raw_df = load_data()
total_raw = len(raw_df)
d = raw_df.copy()

# Apply OR logic for selections
patterns = []
if inc_res: patterns.append("RES|RL|RS")
if inc_dtx: patterns.append("DT")
if inc_hosp: patterns.append("HI|PSY")

if patterns:
    combined_pattern = "|".join(patterns)
    d = d[d['service_code_info'].str.contains(combined_pattern, case=False, na=False)]
else:
    d = pd.DataFrame(columns=d.columns) # Empty if nothing selected

# Apply Maturity and Settings
d = d[d['complexity'] >= min_comp]
if exclude_gov:
    d = d[~d['service_code_info'].str.contains('STG|FED|VAMC', case=False, na=False)]
if only_private:
    d = d[d['service_code_info'].str.contains('PVTP', case=False, na=False)]

# Sort by complexity
d = d.sort_values(by=['complexity', 'name1'], ascending=[False, True])

# --- 4. MAIN OUTPUT PANE ---
st.title("📊 Scored Prospects")

# Metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Master Records", f"{total_raw:,}")
m2.metric("Qualifying", f"{len(d):,}")
m3.metric("Maturity Target", quality_label)
m4.metric("Avg. Service Count", round(d['complexity'].mean(), 1) if not d.empty else 0)

# Filters
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter State", options=sorted(d['state'].unique()) if not d.empty else [])

if search: d = d[d['name1'].str.lower().str.contains(search)]
if states: d = d[d['state'].isin(states)]

# Clean Table Display
output_df = d.head(max_show).reset_index()
output_df.index = range(1, len(output_df) + 1)
output_df.index.name = "Rank"

# Formatting Column Names for the user
output_df = output_df.rename(columns={
    'name1': 'Facility Name',
    'complexity': 'Maturity Score',
    'state': 'State',
    'phone': 'Phone'
})

st.dataframe(
    output_df[['Facility Name', 'State', 'Maturity Score', 'Phone', 'Master Row #']], 
    use_container_width=True,
    height=550
)

st.download_button("📥 Download Scored List", d.to_csv(index=True).encode('utf-8'), "Scored_Prospects.csv")
