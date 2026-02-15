import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING (Balanced Spacing) ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    /* MAIN LAYOUT */
    .block-container {padding-top: 1.5rem; padding-bottom: 0rem;}
    [data-testid="stSidebar"] {width: 310px !important;}
    
    /* SIDEBAR SPACING (Balanced 10-15% expansion from scrunched version) */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0.6rem !important; 
    }
    [data-testid="stSidebar"] h1 {
        margin-top: 0rem !important;
        margin-bottom: 0.8rem !important;
        font-size: 1.7rem !important;
    }
    [data-testid="stSidebar"] h3 {
        margin-top: 0.4rem !important;
        margin-bottom: 0.2rem !important;
        font-size: 1.05rem !important;
        font-weight: 600;
    }
    [data-testid="stSidebar"] hr {
        margin: 0.8rem 0px !important;
    }
    [data-testid="stSidebar"] .stCheckbox {
        margin-bottom: -5px !important;
    }
    [data-testid="stSidebar"] .stSlider {
        padding-bottom: 12px !important;
    }
    [data-testid="stSidebar"] .stNumberInput {
        margin-top: 5px !important;
    }

    /* METRIC BUTTON STYLING (Centered & Integrated) */
    div[data-testid="stMetric"] {
        padding-bottom: 5px !important;
    }
    div[data-testid="column"] button {
        width: 90%;
        margin-left: 5%;
        margin-top: -12px; 
        border: none;
        border-radius: 6px;
        background-color: #262730; 
        color: #ff4b4b; 
        font-weight: 600;
        font-size: 13px;
        transition: all 0.2s;
        padding: 4px 0px;
    }
    div[data-testid="column"] button:hover {
        background-color: #ff4b4b;
        color: white;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 2. SESSION STATE ---
if 'max_show' not in st.session_state:
    st.session_state.max_show = 100

# --- 3. DATA LOADING ---
INFRA_CODES = {'HI', 'PSYH', 'RES', 'RL', 'RS', 'DT', 'ADTX', 'ODTX', 'BDTX', 'CDTX', 'MDTX', 'SUMH', 'MH', 'SA', 'OTP'}
CLINICAL_CODES = {'UB', 'MM', 'VTRL', 'BERI', 'GH', 'CO', 'VET', 'ADM', 'PW', 'SE', 'LABT', 'MSRV', 'METH', 'NXN'}
PRIVATE_CODES = {'PVTP'}
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

# --- 4. SCORING CONTROL BOARD (Sidebar) ---
st.sidebar.title("🎛️ Scoring Controls")

with st.sidebar.expander("Care Types (Include)", expanded=True):
    inc_res = st.checkbox("Residential", value=True)
    inc_dtx = st.checkbox("Detox (DT)")
    inc_hosp = st.checkbox("Hospital / Inpatient")

st.sidebar.subheader("Define 'Propensity'")
w_infra = st.sidebar.slider("Infrastructure Weight", 1, 10, 5)
w_priv = st.sidebar.slider("Private Ownership Bonus", 0, 20, 10)
w_clin = st.sidebar.slider("Clinical Depth Weight", 1, 10, 3)
w_std = st.sidebar.slider("Standard Services Weight", 0, 5, 1)

st.sidebar.divider()
st.sidebar.subheader("Cutoff & Limits")
min_propensity = st.sidebar.slider("Min. Score Threshold", 0, 100, 40)
st.sidebar.number_input("Download Size (Rows)", key="max_show", min_value=1, step=1)
exclude_gov = st.sidebar.toggle("Exclude Govt/VAMC", value=True)

# --- 5. SCORING ---
d['Raw_Score'] = (d['n_infra'] * w_infra) + (d['n_clinical'] * w_clin) + (d['n_private'] * w_priv) + (d['n_standard'] * w_std)
current_max = d['Raw_Score'].max() if d['Raw_Score'].max() > 0 else 1
d['Propensity Score'] = ((d['Raw_Score'] / current_max) * 100).round(0).astype(int)

# --- 6. FILTERING ---
count_unique = len(d)
patterns = []
if inc_res: patterns.append("RES|RL|RS")
if inc_dtx: patterns.append("DT")
if inc_hosp: patterns.append("HI|PSY")

d_services = d[d['service_code_info'].str.contains("|".join(patterns), case=False, na=False)] if patterns else d
count_services = len(d_services)
d_scored = d_services[d_services['Propensity Score'] >= min_propensity]
count_scored = len(d_scored)
d_final = d_scored[~d_scored['service_code_info'].str.contains('STG|FED|VAMC', case=False, na=False)] if exclude_gov else d_scored
count_final = len(d_final)
d_final = d_final.sort_values(by=['Propensity Score', 'Location', 'name1'], ascending=[False, True, True])

# --- 7. TIE DETECTION ---
current_limit = int(st.session_state.max_show)
count_ties = 0
if count_final > current_limit:
    cutoff_score = d_final.iloc[current_limit - 1]['Propensity Score']
    count_ties = len(d_final.iloc[current_limit:][d_final.iloc[current_limit:]['Propensity Score'] == cutoff_score])
    display_df = d_final.head(current_limit)
else:
    display_df = d_final

# --- 8. MAIN VIEW ---
st.title("📊 Scored Prospects")

# Metrics Waterfall (Wider spacing)
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("1. Universe", f"{total_raw:,}")
c2.metric("2. Unique Locs", f"{count_unique:,}")
c3.metric("3. Care Type Fit", f"{count_services:,}")
c4.metric("4. Score Fit", f"{count_scored:,}")
c5.metric("5. Total Qualified", f"{count_final:,}")

if count_ties > 0:
    c6.metric("6. Hidden Ties", f"{count_ties:,}")
    if c6.button("➕ Include Ties"):
        st.session_state.max_show += count_ties
        st.rerun()
else:
    c6.empty()

# Search and Table
c_search, c_state = st.columns([2, 1])
search = c_search.text_input("🔍 Search Name").lower()
states = c_state.multiselect("📍 State", options=sorted(d['state_clean'].unique()))

if search: display_df = display_df[display_df['name1'].str.lower().str.contains(search)]
if states: display_df = display_df[display_df['state_clean'].isin(states)]

st.dataframe(
    display_df[['name1', 'Location', 'phone', 'Propensity Score', 'orig_row']].rename(
        columns={'name1': 'Facility', 'phone': 'Phone', 'orig_row': 'Source'}
    ), 
    use_container_width=True, 
    height=550, 
    column_config={"Propensity Score": st.column_config.ProgressColumn("Propensity", format="%d", min_value=0, max_value=100)}
)

st.download_button("📥 Download (CSV)", d_final.to_csv(index=False).encode('utf-8'), "Scored_Prospects.csv")
