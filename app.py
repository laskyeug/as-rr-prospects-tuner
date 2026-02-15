import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 1.5rem; padding-bottom: 0rem;}
    [data-testid="stSidebar"] {width: 310px !important;}
    [data-testid="stSidebarNav"] {display: none;}
    [data-testid="stSidebar"] h1 {margin-top: -30px !important; margin-bottom: 0.5rem !important; font-size: 1.7rem !important;}
    [data-testid="stSidebar"] h3 {margin-top: 0.4rem !important; margin-bottom: 0.1rem !important; font-size: 1.05rem !important; font-weight: 600;}
    [data-testid="stSidebar"] hr {margin: 0.3rem 0px !important;}
    .slider-label-row {display: flex; justify-content: space-between; font-size: 11px; color: #808495; margin-top: -22px; margin-bottom: 12px; padding: 0 5px;}
    </style>
    """, unsafe_allow_html=True)

# --- 2. SESSION STATE ---
if 'max_show' not in st.session_state:
    st.session_state.max_show = 100

# --- 3. DATA LOADING ---
# Defining the codes based on what we saw in your DEBUG column
INFRA_CODES = {'HI', 'PSYH', 'RES', 'RL', 'RS', 'DT', 'ADTX', 'ODTX', 'BDTX', 'CDTX', 'MDTX', 'SUMH', 'MH', 'SA', 'OTP'}
CLINICAL_CODES = {'METH', 'NXN', 'VTRL', 'LABT', 'MM', 'MSRV', 'UB', 'BERI', 'GH', 'CO', 'VET', 'ADM', 'PW', 'SE'}
PRIVATE_CODES = {'PVTP'} # For-Profit
GOVT_CODES = {'FED', 'STG', 'VAMC', 'LCLG', 'GVT', 'STLG'} # Government
NON_PROFIT_CODES = {'PVTN'} # Private Non-Profit (if present)
STANDARD_CODES = {'OP', 'IOP', 'PH', 'CBT', 'DBT', 'MI', 'ANG', 'REL', 'TRC', 'SAE', 'TCC', 'CM', 'SS', 'TA'}

def count_category(tag_string, category_set):
    tags = set([t.strip() for t in str(tag_string).split('*') if t.strip()])
    return len(tags.intersection(category_set))

def merge_tags(series):
    all_tags = "*".join(series.astype(str)).split('*')
    unique_tags = sorted(list(set([t.strip() for t in all_tags if t.strip()])))
    return " * ".join(unique_tags)

@st.cache_data(ttl=3600)
def load_data():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    try:
        sheet = client.open("SAMHSA_Master_Data").sheet1
        df = pd.DataFrame(sheet.get_all_records())
        df['orig_row'] = df.index + 2
        df['city_clean'] = df['city'].fillna('').astype(str).str.title()
        df['state_clean'] = df['state'].fillna('').astype(str).str.upper()
        
        rollup = df.groupby(['name1', 'city_clean', 'state_clean']).agg({
            'service_code_info': merge_tags,
            'phone': 'first',
            'orig_row': lambda x: ", ".join(x.astype(str))
        }).reset_index()
        
        rollup['Location'] = rollup.apply(lambda x: f"{x['city_clean']}, {x['state_clean']}" if x['city_clean'] else x['state_clean'], axis=1)
        rollup['n_infra'] = rollup['service_code_info'].apply(lambda x: count_category(x, INFRA_CODES))
        rollup['n_clinical'] = rollup['service_code_info'].apply(lambda x: count_category(x, CLINICAL_CODES))
        rollup['n_private'] = rollup['service_code_info'].apply(lambda x: count_category(x, PRIVATE_CODES))
        rollup['n_standard'] = rollup['service_code_info'].apply(lambda x: count_category(x, STANDARD_CODES))
        
        return rollup, len(df)
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

d, total_raw = load_data()

# --- 4. SCORING CONTROL BOARD ---
st.sidebar.title("🎛️ Scoring Controls")

with st.sidebar.expander("Care Types (Include)", expanded=True):
    inc_res = st.checkbox("Residential", value=True)
    inc_dtx = st.checkbox("Detox (DT)")
    inc_hosp = st.checkbox("Hospital / Inpatient")

st.sidebar.subheader("Propensity Settings")
w_infra = st.sidebar.slider("Infrastructure Breadth", 1, 10, 5)
st.sidebar.markdown('<div class="slider-label-row"><span>Less (-)</span><span>More (+)</span></div>', unsafe_allow_html=True)

w_clin = st.sidebar.slider("Clinical Depth", 1, 10, 3)
st.sidebar.markdown('<div class="slider-label-row"><span>Less (-)</span><span>More (+)</span></div>', unsafe_allow_html=True)

w_std = st.sidebar.slider("Services Offered", 0, 5, 1)
st.sidebar.markdown('<div class="slider-label-row"><span>Less (-)</span><span>More (+)</span></div>', unsafe_allow_html=True)

st.sidebar.divider()
exclude_gov = st.sidebar.toggle("Exclude Govt / VAMC", value=True)
exclude_np = st.sidebar.toggle("Exclude Non-Profits", value=False)

st.sidebar.divider()
min_propensity = st.sidebar.slider("Min. Score Threshold", 0, 100, 40)
st.sidebar.number_input("Download Size (Rows)", key="max_show", min_value=1, step=1)

# --- 5. SCORING ENGINE ---
# Kicker is 25 if PVTP is present, otherwise 0
d['Raw_Score'] = (d['n_infra'] * w_infra) + (d['n_clinical'] * w_clin) + (d['n_private'] * 25) + (d['n_standard'] * w_std)
current_max = d['Raw_Score'].max() if d['Raw_Score'].max() > 0 else 1
d['Propensity Score'] = ((d['Raw_Score'] / current_max) * 100).round(0).astype(int)

# --- 6. FILTERING ENGINE (The "Negative Logic" Fix) ---
count_unique = len(d)
patterns = []
if inc_res: patterns.append("RES|RL|RS")
if inc_dtx: patterns.append("DT")
if inc_hosp: patterns.append("HI|PSY")

# Filter 1: Care Type
d_work = d[d['service_code_info'].str.contains("|".join(patterns), case=False, na=False)] if patterns else d
count_services = len(d_work)

# Filter 2: Score Fit
d_scored = d_work[d_work['Propensity Score'] >= min_propensity]
count_scored = len(d_scored)

# Filter 3: Ownership
d_final = d_scored.copy()

if exclude_gov:
    # Remove anything with a Govt tag
    d_final = d_final[~d_final['service_code_info'].str.contains('|'.join(GOVT_CODES), case=False, na=False)]

if exclude_np:
    # Remove anything with a Non-Profit tag
    d_final = d_final[~d_final['service_code_info'].str.contains('|'.join(NON_PROFIT_CODES), case=False, na=False)]

count_final = len(d_final)
d_final = d_final.sort_values(by=['Propensity Score', 'Location', 'name1'], ascending=[False, True, True])

# --- 7. TIE DETECTION ---
current_limit = int(st.session_state.max_show)
count_ties = 0
if count_final > current_limit:
    cutoff_score = d_final.iloc[current_limit - 1]['Propensity Score']
    count_ties = len(d_final.iloc[current_limit:][d_final.iloc[current_limit:]['Propensity Score'] == cutoff_score])
    display_df = d_final.head(current_limit).copy()
else:
    display_df = d_final.copy()

# --- 8. MAIN VIEW ---
st.title("📊 Scored Prospects")

# WATERFALL METRICS
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("1. Universe", f"{total_raw:,}")
c2.metric("2. Unique Locs", f"{count_unique:,}", delta=f"-{total_raw - count_unique:,}", delta_color="off")
c3.metric("3. Care Type Fit", f"{count_services:,}", delta=f"-{count_unique - count_services:,}", delta_color="off")
c4.metric("4. Score Fit", f"{count_scored:,}", delta=f"-{count_services - count_scored:,}", delta_color="off")
c5.metric("5. Total Qualified", f"{count_final:,}", delta=f"-{count_scored - count_final:,}", delta_color="off")

if count_ties > 0:
    c6.metric("6. Hidden Ties", f"{count_ties:,}")
    if c6.button("➕ Include Ties"):
        st.session_state.max_show += count_ties
        st.rerun()

# SEARCH AND TABLE
c_search, c_state = st.columns([2, 1])
search = c_search.text_input("🔍 Search Name").lower()
states = c_state.multiselect("📍 State", options=sorted(d['state_clean'].unique()))

if search: display_df = display_df[display_df['name1'].str.lower().str.contains(search)]
if states: display_df = display_df[display_df['state_clean'].isin(states)]

display_df = display_df.reset_index(drop=True)
display_df.insert(0, 'Rank', display_df.index + 1)

# REMOVED DEBUG COLUMN FOR PRODUCTION CLEANLINESS
st.dataframe(
    display_df[['Rank', 'name1', 'Location', 'phone', 'Propensity Score', 'orig_row']].rename(
        columns={'name1': 'Facility Name', 'phone': 'Phone', 'orig_row': 'Source Row(s)'}
    ), 
    use_container_width=True, 
    height=550, 
    hide_index=True, 
    column_config={
        "Rank": st.column_config.NumberColumn("Rank", width=40),
        "Propensity Score": st.column_config.ProgressColumn("Propensity", format="%d", min_value=0, max_value=100, width=80),
        "Source Row(s)": st.column_config.TextColumn("Source Row(s)", width=200)
    }
)

st.download_button("📥 Download (CSV)", d_final.to_csv(index=False).encode('utf-8'), "Scored_Prospects.csv")
