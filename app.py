import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

# Custom CSS to tighten vertical spacing
st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    div[data-testid="stMetric"] {padding: 0px 0px 10px 0px;}
    div[data-testid="stExpander"] {margin-top: -20px;}
    </style>
    """, unsafe_allow_value=True)

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
        df['service_code_info'] = df['service_code_info'].fillna('').astype(str)
        return df
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

# --- 2. SIDEBAR TUNER ---
st.sidebar.title("🎛️ Scoring Tuner")
s_hosp = st.sidebar.slider("Hospital (PSY/HI)", 0, 100, 50)
s_res = st.sidebar.slider("Residential (RES)", 0, 100, 30)
s_detox = st.sidebar.slider("Detox (DT)", 0, 100, 25)
s_otp = st.sidebar.slider("Opioid Tx (OTP)", 0, 100, 10)
s_priv = st.sidebar.slider("Private For-Profit", 0, 100, 20)
s_gov = st.sidebar.slider("Govt Penalty", -50, 0, -15)

st.sidebar.divider()
st.sidebar.subheader("Output Constraints")
max_records = st.sidebar.slider("Max Prospects to Display", 100, 5000, 1000)
t1_cut = st.sidebar.number_input("Tier 1 Cutoff (Score >=)", value=95)
t2_cut = st.sidebar.number_input("Tier 2 Cutoff (Score >=)", value=75)

# --- 3. SCORING ENGINE ---
raw_df = load_data()
total_raw = len(raw_df)

def score_row(codes):
    score = 0; tags = []
    if any(c in codes for c in ['PSY', 'HI', 'GH']): score += s_hosp; tags.append("HOSPITAL")
    elif any(c in codes for c in ['RES', 'RL', 'RS']): score += s_res; tags.append("RESIDENTIAL")
    if 'DT' in codes: score += s_detox; tags.append("DETOX")
    if 'OTP' in codes: score += s_otp; tags.append("OTP")
    if 'PVTP' in codes: score += s_priv; tags.append("PRIVATE")
    if any(c in codes for c in ['STG', 'FED', 'VAMC']): score += s_gov; tags.append("GOV")
    return score, ", ".join(tags)

overnight_codes = ['HI', 'PSY', 'GH', 'RES', 'RL', 'RS']
active_df = raw_df[raw_df['service_code_info'].apply(lambda x: any(c in x for c in overnight_codes))].copy()
total_qualifying = len(active_df)

res = active_df['service_code_info'].apply(score_row)
active_df['Score'], active_df['Tags'] = [x[0] for x in res], [x[1] for x in res]
active_df['Tier'] = active_df['Score'].apply(lambda s: "Tier 1" if s >= t1_cut else ("Tier 2" if s >= t2_cut else "Tier 3"))
active_df = active_df.sort_values(by=['Score', 'name1'], ascending=[False, True])

# --- 4. MAIN OUTPUT PANE ---
st.title("📊 Scored Prospects")

# Reconciliation & Stats Row (Tightened)
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Master Records", f"{total_raw:,}")
m2.metric("Qualifying", f"{total_qualifying:,}")
m3.metric("Current Set", f"{len(active_df):,}")
m4.metric("Tier 1", f"{len(active_df[active_df['Tier']=='Tier 1']):,}")
m5.metric("Avg Score", round(active_df['Score'].mean(), 1))

# Search & Filters (Side-by-side)
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter by State", options=sorted(active_df['state'].unique()))

display_df = active_df.copy()
if search: display_df = display_df[display_df['name1'].str.lower().str.contains(search)]
if states: display_df = display_df[display_df['state'].isin(states)]

# Table with Rank
output_df = display_df.head(max_records).reset_index()
output_df.index = range(1, len(output_df) + 1)
output_df.index.name = "Rank"

st.dataframe(
    output_df[['name1', 'Tier', 'Score', 'Tags', 'state', 'Master Row #']], 
    use_container_width=True,
    height=500  # Fixed height helps avoid whole-page scrolling
)

st.download_button("📥 Download Scored CSV", display_df.to_csv(index=True).encode('utf-8'), "Scored_Prospects.csv")
