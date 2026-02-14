import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLE ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 2rem;}
    h1 {color: #2c3e50;}
    div[data-testid="stMetricValue"] {font-size: 1.6rem;}
    </style>
""", unsafe_allow_html=True)

# --- 2. DATA CONNECTION ---
@st.cache_data(ttl=3600)
def load_data():
    # Load secrets
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    
    # CONNECT TO SHEET
    # Use the EXACT name of the Google Sheet you created in Drive
    SHEET_NAME = "SAMHSA_Master_Data"
    
    try:
        sheet = client.open(SHEET_NAME).sheet1
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        # Fast Cleaning
        df.columns = df.columns.str.strip()
        df['service_code_info'] = df['service_code_info'].fillna('').astype(str)
        return df
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}")
        st.stop()

# --- 3. SIDEBAR DIALS ---
st.sidebar.title("🎛️ Scoring Tuner")

with st.sidebar.expander("1. Base Scores (Venue)", expanded=True):
    s_hosp = st.slider("Hospital (PSY/HI)", 0, 100, 50)
    s_res = st.slider("Residential (RES)", 0, 100, 30)

with st.sidebar.expander("2. Acuity Multipliers", expanded=True):
    s_detox = st.slider("Detox (DT)", 0, 100, 25)
    s_otp = st.slider("Opioid Tx (OTP)", 0, 100, 10)

with st.sidebar.expander("3. Business Modifiers", expanded=False):
    s_priv = st.slider("Private For-Profit", 0, 100, 20)
    s_gov = st.slider("Govt Penalty", -50, 0, -15)

with st.sidebar.expander("4. Tier Thresholds", expanded=True):
    t1_cut = st.number_input("Tier 1 Cutoff", value=95)
    t2_cut = st.number_input("Tier 2 Cutoff", value=75)

# --- 4. SCORING ENGINE ---
df = load_data()

# Dynamic Scoring Function
def score_row(codes):
    score = 0
    tags = []
    
    # Venue
    if any(c in codes for c in ['PSY', 'HI', 'GH']):
        score += s_hosp
        tags.append("HOSPITAL")
    elif any(c in codes for c in ['RES', 'RL', 'RS']):
        score += s_res
        tags.append("RESIDENTIAL")
        
    # Acuity
    if 'DT' in codes:
        score += s_detox
        tags.append("DETOX")
    if 'OTP' in codes:
        score += s_otp
        tags.append("OTP")
        
    # Business
    if 'PVTP' in codes:
        score += s_priv
        tags.append("PRIVATE")
    if any(c in codes for c in ['STG', 'FED', 'VAMC']):
        score += s_gov
        tags.append("GOV")
        
    return score, ", ".join(tags)

# Run Logic
overnight_codes = ['HI', 'PSY', 'GH', 'RES', 'RL', 'RS']
mask = df['service_code_info'].apply(lambda x: any(c in x for c in overnight_codes))
active_df = df[mask].copy()

results = active_df['service_code_info'].apply(score_row)
active_df['Score'] = [x[0] for x in results]
active_df['Tags'] = [x[1] for x in results]

# Tiering
def get_tier(s):
    if s >= t1_cut: return "Tier 1"
    elif s >= t2_cut: return "Tier 2"
    else: return "Tier 3"
active_df['Tier'] = active_df['Score'].apply(get_tier)

active_df = active_df.sort_values(by='Score', ascending=False)

# --- 5. DASHBOARD ---
st.title("A/S RR Prospects - Scoring Tuner")

# Metrics
c1, c2, c3 = st.columns(3)
c1.metric("Tier 1 Targets", len(active_df[active_df['Tier']=='Tier 1']))
c2.metric("Tier 2 Targets", len(active_df[active_df['Tier']=='Tier 2']))
c3.metric("Total Prospects", len(active_df))

# Preview
st.dataframe(active_df[['name1', 'Tier', 'Score', 'Tags', 'state', 'phone']].head(50), use_container_width=True)

# Export
output_cols = ['name1', 'Tier', 'Score', 'Tags', 'phone', 'city', 'state', 'street1', 'zip']
csv = active_df[output_cols].to_csv(index=False).encode('utf-8')

st.download_button(
    label="📥 Download Scored List",
    data=csv,
    file_name="AllSober_Prospects.csv",
    mime="text/csv"
)
