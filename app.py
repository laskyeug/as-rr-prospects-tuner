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
    .stCheckbox {margin-bottom: -10px;}
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
        # Pre-calculate complexity (how many unique services they offer)
        df['complexity'] = df['service_code_info'].str.split(',').str.len().fillna(0)
        df['service_code_info'] = df['service_code_info'].fillna('').astype(str)
        return df
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

# --- 2. THE SIMPLIFIED TUNER RIBBON ---
st.sidebar.title("🎯 Prospect Filters")

# Section 1: Must-Have Services
st.sidebar.subheader("Required Services")
req_hosp = st.sidebar.checkbox("Hospital / Inpatient")
req_detox = st.sidebar.checkbox("Detox (DT)")
req_res = st.sidebar.checkbox("Residential", value=True)

st.sidebar.divider()

# Section 2: The "Intuitive" Quality Slider
st.sidebar.subheader("Quality Filter")
# Moving this right makes the list SMALLER (stricter)
min_complexity = st.sidebar.slider(
    "Min. Services Offered", 
    min_value=1, max_value=40, value=5,
    help="Slide right to filter for more 'complex/mature' facilities. This will decrease the number of results."
)

st.sidebar.divider()

# Section 3: Firm Preferences
st.sidebar.subheader("Preferences")
only_private = st.sidebar.toggle("Only Private For-Profit")
exclude_gov = st.sidebar.toggle("Exclude Govt/VAMC", value=True)
max_show = st.sidebar.number_input("Max Rows to Show", value=1000)

# --- 3. THE INTUITIVE FILTER ENGINE ---
raw_df = load_data()
total_raw = len(raw_df)

# Start with the Master List
d = raw_df.copy()

# Apply Hard Requirements (The "Checkboxes")
if req_hosp:
    d = d[d['service_code_info'].str.contains('HI|PSY', na=False)]
if req_detox:
    d = d[d['service_code_info'].str.contains('DT', na=False)]
if req_res:
    d = d[d['service_code_info'].str.contains('RES|RL|RS', na=False)]

# Apply Quality Filter (The "Slider")
d = d[d['complexity'] >= min_complexity]

# Apply Preferences
if only_private:
    d = d[d['service_code_info'].str.contains('PVTP', na=False)]
if exclude_gov:
    d = d[~d['service_code_info'].str.contains('STG|FED|VAMC', na=False)]

# Sorting: Rank by complexity (biggest campuses first)
d = d.sort_values(by=['complexity', 'name1'], ascending=[False, True])

# --- 4. MAIN OUTPUT PANE ---
st.title("📊 Scored Prospects")

# Reconciliation Row
m1, m2, m3, m4 = st.columns(4)
m1.metric("Master Records", f"{total_raw:,}")
m2.metric("Qualifying", f"{len(d):,}")
m3.metric("Avg Complexity", round(d['complexity'].mean(), 1))
m4.metric("Strictness Level", f"{min_complexity}/40")

# Filters
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter State", options=sorted(d['state'].unique()))

if search: d = d[d['name1'].str.lower().str.contains(search)]
if states: d = d[d['state'].isin(states)]

# Final Table
output_df = d.head(max_show).reset_index()
output_df.index = range(1, len(output_df) + 1)
output_df.index.name = "Rank"

# Rename 'complexity' to 'Score' for the UI
output_df = output_df.rename(columns={'complexity': 'Service Count'})

st.dataframe(
    output_df[['name1', 'Service Count', 'state', 'phone', 'Master Row #']], 
    use_container_width=True,
    height=500
)

st.download_button("📥 Download This Scored View", d.to_csv(index=True).encode('utf-8'), "Scored_Prospects.csv")
