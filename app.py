import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    [data-testid="stSidebar"] {width: 260px !important;}
    div[data-testid="stMetric"] {padding: 0px 0px 5px 0px;}
    .stMultiSelect {margin-top: -15px;}
    .stSlider {margin-top: -15px;}
    </style>
    """, unsafe_allow_html=True)

@st.cache_data(ttl=3600)
def load_data():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    try:
        sheet = client.open("SAMHSA_Master_Data").sheet1
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        df.index = df.index + 2
        df.index.name = "Master Row #"
        
        # CLEANING: Ensure column exists and is string-based
        if 'service_code_info' in df.columns:
            df['service_code_info'] = df['service_code_info'].fillna('').astype(str)
            df['complexity'] = df['service_code_info'].str.split(',').str.len()
        else:
            st.error("Column 'service_code_info' not found in your sheet!")
            st.stop()
            
        return df
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

# --- 2. TUNER RIBBON ---
st.sidebar.title("🎯 Prospect Filters")

st.sidebar.subheader("Include Service Types (OR)")
selected_services = st.sidebar.multiselect(
    "Select any to include:",
    options=["Residential", "Detox", "Hospital Inpatient"],
    default=["Residential"]
)

st.sidebar.divider()

st.sidebar.subheader("Quality Filter")
min_complexity = st.sidebar.slider("Min. Services Offered", 1, 40, 5)

st.sidebar.divider()

st.sidebar.subheader("Settings")
exclude_gov = st.sidebar.toggle("Exclude Govt/VAMC", value=True)
only_private = st.sidebar.toggle("Only Private For-Profit")
max_show = st.sidebar.number_input("Max Rows Shown", value=1000)

# --- 3. FILTER ENGINE (Case-Insensitive & OR Logic) ---
raw_df = load_data()
total_raw = len(raw_df)
d = raw_df.copy()

# Step 1: Filter by Service Type (OR Logic)
if selected_services:
    patterns = []
    if "Hospital Inpatient" in selected_services: patterns.append("HI|PSY")
    if "Detox" in selected_services: patterns.append("DT")
    if "Residential" in selected_services: patterns.append("RES|RL|RS")
    
    combined_pattern = "|".join(patterns)
    # Added case=False to handle "res", "RES", or "Res"
    d = d[d['service_code_info'].str.contains(combined_pattern, case=False, na=False)]

# Step 2: Quality Filter
d = d[d['complexity'] >= min_complexity]

# Step 3: Preferences
if exclude_gov:
    d = d[~d['service_code_info'].str.contains('STG|FED|VAMC', case=False, na=False)]
if only_private:
    d = d[d['service_code_info'].str.contains('PVTP', case=False, na=False)]

# Final Sort
d = d.sort_values(by=['complexity', 'name1'], ascending=[False, True])

# --- 4. MAIN OUTPUT PANE ---
st.title("📊 Scored Prospects")

# Reconciliation Metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Master Records", f"{total_raw:,}")
m2.metric("Qualifying", f"{len(d):,}")
m3.metric("Avg Complexity", round(d['complexity'].mean(), 1) if not d.empty else 0)
m4.metric("Strictness", f"{min_complexity}/40")

# DATA HEALTH CHECK (Hidden by default, expand if list is 0)
if len(d) == 0:
    with st.expander("⚠️ Troubleshooting: Why is the list empty?", expanded=True):
        st.write("Current Filter Pattern:", combined_pattern if selected_services else "None")
        st.write("Sample Service Codes from your Sheet:")
        st.write(raw_df['service_code_info'].head(10).tolist())

# Search & State
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter State", options=sorted(d['state'].unique()) if not d.empty else [])

if search: d = d[d['name1'].str.lower().str.contains(search)]
if states: d = d[d['state'].isin(states)]

# Final Table
output_df = d.head(max_show).reset_index()
output_df.index = range(1, len(output_df) + 1)
output_df.index.name = "Rank"
output_df = output_df.rename(columns={'complexity': 'Service Count'})

st.dataframe(
    output_df[['name1', 'Service Count', 'state', 'phone', 'Master Row #']], 
    use_container_width=True,
    height=450
)

st.download_button("📥 Download Scored CSV", d.to_csv(index=True).encode('utf-8'), "Scored_Prospects.csv")
