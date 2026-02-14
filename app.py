import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    /* Tighten Sidebar */
    [data-testid="stSidebar"] {width: 300px !important;}
    div[data-testid="stMetric"] {padding: 0px 0px 5px 0px;}
    .stSlider {margin-top: -15px; padding-bottom: 10px;}
    .stNumberInput {margin-top: -15px;}
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
        # Count total codes for complexity scoring
        df['service_count'] = df['service_code_info'].str.split(',').str.len().fillna(0)
        df['service_code_info'] = df['service_code_info'].fillna('').astype(str)
        return df
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

# --- 2. SIDEBAR TUNER (COMPACT) ---
st.sidebar.title("🎛️ Tuner Ribbon")

with st.sidebar.expander("⭐ Core Service Weights", expanded=True):
    s_hi = st.slider("Hospital Inpatient", 0, 100, 50)
    s_psy = st.slider("Psych Unit", 0, 100, 40)
    s_res = st.slider("Residential", 0, 100, 30)
    s_detox = st.slider("Detox (DT)", 0, 100, 25)

with st.sidebar.expander("📊 Complexity & Bonus"):
    s_comp = st.slider("Complexity Bonus (>25 codes)", 0, 50, 20)
    s_std = st.slider("Standard Bonus (10-25 codes)", 0, 50, 10)
    s_combo = st.slider("Res + Detox Combo", 0, 50, 15)
    s_priv = st.slider("Private For-Profit", 0, 50, 10)

with st.sidebar.expander("🚫 Penalties & Tiers"):
    s_gov = st.slider("Govt/VAMC Penalty", -50, 0, -15)
    t1_cut = st.number_input("Tier 1 Threshold", value=95)
    max_records = st.number_input("Max Prospects Shown", value=1000)

# --- 3. SCORING ENGINE ---
raw_df = load_data()
total_raw = len(raw_df)

def score_row(row):
    codes = row['service_code_info']
    count = row['service_count']
    score = 0; tags = []
    
    # Core Service Logic
    if 'HI' in codes: score += s_hi; tags.append("HI")
    if 'PSY' in codes: score += s_psy; tags.append("PSY")
    if any(c in codes for c in ['RES', 'RL', 'RS']): score += s_res; tags.append("RES")
    if 'DT' in codes: score += s_detox; tags.append("DETOX")
    
    # Complexity Logic (The Tie-Breaker)
    if count > 25: score += s_comp; tags.append("COMPLEX")
    elif count >= 10: score += s_std; tags.append("STANDARD")
    
    # Combo & Ownership Logic
    if 'DT' in codes and any(c in codes for c in ['RES', 'RL', 'RS']):
        score += s_combo; tags.append("INTEGRATED")
    if 'PVTP' in codes: score += s_priv; tags.append("PRIVATE")
    if any(c in codes for c in ['STG', 'FED', 'VAMC']): score += s_gov; tags.append("GOV")
    
    return score, ", ".join(tags)

overnight_codes = ['HI', 'PSY', 'GH', 'RES', 'RL', 'RS']
active_df = raw_df[raw_df['service_code_info'].apply(lambda x: any(c in x for c in overnight_codes))].copy()

# Run the scoring
scoring_results = active_df.apply(score_row, axis=1)
active_df['Score'] = [x[0] for x in scoring_results]
active_df['Tags'] = [x[1] for x in scoring_results]
active_df['Tier'] = active_df['Score'].apply(lambda s: "Tier 1" if s >= t1_cut else "Tier 2")

# Sort by Score first, then name
active_df = active_df.sort_values(by=['Score', 'name1'], ascending=[False, True])

# --- 4. MAIN OUTPUT PANE ---
st.title("📊 Scored Prospects")

# Reconciliation Row (Comma Formatted)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Master", f"{total_raw:,}")
m2.metric("Qualifying", f"{len(active_df):,}")
m3.metric("Tier 1", f"{len(active_df[active_df['Tier']=='Tier 1']):,}")
m4.metric("Avg Score", round(active_df['Score'].mean(), 1))

# Filters
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter by State", options=sorted(active_df['state'].unique()))

display_df = active_df.copy()
if search: display_df = display_df[display_df['name1'].str.lower().str.contains(search)]
if states: display_df = display_df[display_df['state'].isin(states)]

# Table
output_df = display_df.head(max_records).reset_index()
output_df.index = range(1, len(output_df) + 1)
output_df.index.name = "Rank"

st.dataframe(
    output_df[['name1', 'Tier', 'Score', 'Tags', 'state', 'Master Row #']], 
    use_container_width=True,
    height=550
)

st.download_button("📥 Download Scored CSV", display_df.to_csv(index=True).encode('utf-8'), "Scored_Prospects.csv")
