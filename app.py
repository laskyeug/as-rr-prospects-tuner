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
    .stExpander {margin-top: -15px; margin-bottom: 10px;}
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
        
        # Calculate Propensity relative to the best facility in the data
        df['service_code_info'] = df['service_code_info'].fillna('').astype(str)
        df['raw_comp'] = df['service_code_info'].str.split('*').str.len().fillna(0)
        
        universe_max = df['raw_comp'].max() if not df.empty else 1
        df['Propensity Score'] = ((df['raw_comp'] / universe_max) * 100).round(1)
        
        # Combine City and State
        df['Location'] = df['city'].astype(str).str.title() + ", " + df['state'].astype(str).str.upper()
        
        return df, universe_max
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
min_propensity = st.sidebar.slider(
    "Min. Propensity Score", 
    0.0, 100.0, 40.0,
    help="Higher scores reflect more complex, institutional facilities."
)

st.sidebar.divider()

st.sidebar.subheader("Settings")
exclude_gov = st.sidebar.toggle("Exclude Govt/VAMC", value=True)
only_private = st.sidebar.toggle("Only Private For-Profit")
max_show = st.sidebar.number_input("Max Rows to Display", value=1000)

# --- 3. FILTER ENGINE ---
raw_df, u_max = load_data()
total_raw = len(raw_df)
d = raw_df.copy()

patterns = []
if inc_res: patterns.append("RES|RL|RS")
if inc_dtx: patterns.append("DT")
if inc_hosp: patterns.append("HI|PSY")

if patterns:
    combined_pattern = "|".join(patterns)
    d = d[d['service_code_info'].str.contains(combined_pattern, case=False, na=False)]
else:
    d = pd.DataFrame(columns=d.columns)

d = d[d['Propensity Score'] >= min_propensity]
if exclude_gov:
    d = d[~d['service_code_info'].str.contains('STG|FED|VAMC', case=False, na=False)]
if only_private:
    d = d[d['service_code_info'].str.contains('PVTP', case=False, na=False)]

# TIE-BREAKER: Sort by Score, then alphabetically by Location/Name to ensure consistent ranking
d = d.sort_values(by=['Propensity Score', 'Location', 'name1'], ascending=[False, True, True])

# --- 4. MAIN OUTPUT PANE ---
st.title("📊 Scored Prospects")

# New Score Legend for the Junior Resource
with st.expander("ℹ️ How to read Propensity Scores"):
    st.markdown(f"""
    **Score Breakdown (Relative to Universe Max of {u_max} services):**
    * **100.0:** Absolute Top Tier. These are 'Super Campuses' offering almost every service in the dataset.
    * **90.0+:** Highly Institutional. Multi-program facilities with high acuity capabilities.
    * **80.0+:** Comprehensive. Standard professional facilities with diverse specialized sub-programs.
    * **Under 70:** Specialized/Boutique. Smaller facilities that focus on a narrower range of care.
    
    *Note: When scores are tied, prospects are ranked by location density and name.*
    """)

# Metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Universe", f"{total_raw:,}")
m2.metric("Qualifying", f"{len(d):,}")
m3.metric("Avg Propensity", f"{round(d['Propensity Score'].mean(), 1) if not d.empty else 0}%")
m4.metric("Propensity Floor", f"{min_propensity}%")

# Search and State
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter by State", options=sorted(raw_df['state'].unique()))

if search: d = d[d['name1'].str.lower().str.contains(search)]
if states: d = d[d['state'].isin(states)]

# Table Display
output_df = d.head(max_show).reset_index()
output_df.index = range(1, len(output_df) + 1)
output_df.index.name = "Rank"

output_df = output_df.rename(columns={'name1': 'Facility Name', 'phone': 'Phone'})

st.dataframe(
    output_df[['Facility Name', 'Location', 'Phone', 'Propensity Score', 'Master Row #']], 
    use_container_width=True,
    height=500
)

st.download_button("📥 Download Scored List (CSV)", d.to_csv(index=True).encode('utf-8'), "Scored_Prospects.csv")
