import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="A/S RR Tuner", layout="wide")

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

# --- SIDEBAR TUNER ---
st.sidebar.title("🎛️ Scoring Tuner")
s_hosp = st.sidebar.slider("Hospital (PSY/HI)", 0, 100, 50)
s_res = st.sidebar.slider("Residential (RES)", 0, 100, 30)
s_detox = st.sidebar.slider("Detox (DT)", 0, 100, 25)
s_otp = st.sidebar.slider("Opioid Tx (OTP)", 0, 100, 10)
s_priv = st.sidebar.slider("Private For-Profit", 0, 100, 20)
s_gov = st.sidebar.slider("Govt Penalty", -50, 0, -15)

st.sidebar.divider()
st.sidebar.subheader("Tier Thresholds")
t1_cut = st.sidebar.number_input("Tier 1 Cutoff (Score >=)", value=95)
t2_cut = st.sidebar.number_input("Tier 2 Cutoff (Score >=)", value=75)

# --- SCORING ENGINE ---
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
res = active_df['service_code_info'].apply(score_row)
active_df['Score'], active_df['Tags'] = [x[0] for x in res], [x[1] for x in res]
active_df['Tier'] = active_df['Score'].apply(lambda s: "Tier 1" if s >= t1_cut else ("Tier 2" if s >= t2_cut else "Tier 3"))
active_df = active_df.sort_values(by='Score', ascending=False)

# --- MAIN OUTPUT PANE ---
st.title("📊 Tuner Results: Scored Prospects & Tiers")

# 1. Search & Filters
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter by State", options=sorted(active_df['state'].unique()))

display_df = active_df.copy()
if search: display_df = display_df[display_df['name1'].str.lower().str.contains(search)]
if states: display_df = display_df[display_df['state'].isin(states)]

# 2. Dynamic Feedback Metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Prospects", len(display_df))
m2.metric("Tier 1", len(display_df[display_df['Tier']=='Tier 1']))
m3.metric("Tier 2", len(display_df[display_df['Tier']=='Tier 2']))
m4.metric("Avg Score", round(display_df['Score'].mean(), 1))

# 3. Output Table with Ranking
# Add a simple numbering column
output_df = display_df.reset_index()
output_df.index = range(1, len(output_df) + 1)
output_df.index.name = "#"

st.dataframe(
    output_df[['name1', 'Tier', 'Score', 'Tags', 'state', 'Master Row #']], 
    use_container_width=True
)

st.download_button("📥 Download This Scored View", display_df.to_csv(index=True).encode('utf-8'), "Scored_Prospects.csv")
