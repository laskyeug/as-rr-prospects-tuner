import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

# --- 2. DATA CONNECTION ---
@st.cache_data(ttl=3600)
def load_data():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    try:
        sheet = client.open("SAMHSA_Master_Data").sheet1
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        # Set Index to match Google Sheets Row Numbers
        df.index = df.index + 2
        df.index.name = "Master Row #"
        df['service_code_info'] = df['service_code_info'].fillna('').astype(str)
        return df
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}")
        st.stop()

# --- 3. SIDEBAR DIALS ---
st.sidebar.title("🎛️ Scoring Tuner")
s_hosp = st.sidebar.slider("Hospital (PSY/HI)", 0, 100, 50)
s_res = st.sidebar.slider("Residential (RES)", 0, 100, 30)
s_detox = st.sidebar.slider("Detox (DT)", 0, 100, 25)
s_otp = st.sidebar.slider("Opioid Tx (OTP)", 0, 100, 10)
s_priv = st.sidebar.slider("Private For-Profit", 0, 100, 20)
s_gov = st.sidebar.slider("Govt Penalty", -50, 0, -15)
t1_cut = st.sidebar.number_input("Tier 1 Cutoff", value=95)

# --- 4. ENGINE ---
df = load_data()

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
active_df = df[df['service_code_info'].apply(lambda x: any(c in x for c in overnight_codes))].copy()
results = active_df['service_code_info'].apply(score_row)
active_df['Score'] = [x[0] for x in results]
active_df['Tags'] = [x[1] for x in results]
active_df['Tier'] = active_df['Score'].apply(lambda s: "Tier 1" if s >= t1_cut else "Tier 2")
active_df = active_df.sort_values(by='Score', ascending=False)

# --- 5. SEARCH & FILTERS ---
st.title("A/S RR Prospects - Scoring Tuner")
col_a, col_b = st.columns(2)
search_query = col_a.text_input("🔍 Search by Facility Name").lower()
state_filter = col_b.multiselect("📍 Filter by State", options=sorted(active_df['state'].unique()))

display_df = active_df.copy()
if search_query:
    display_df = display_df[display_df['name1'].str.lower().str.contains(search_query)]
if state_filter:
    display_df = display_df[display_df['state'].isin(state_filter)]

# Table Display
st.dataframe(display_df[['name1', 'Tier', 'Score', 'Tags', 'state', 'phone']], use_container_width=True)

# Export
csv = display_df.to_csv(index=True).encode('utf-8')
st.download_button(label="📥 Download This View", data=csv, file_name="Scored_Prospects.csv", mime="text/csv")
