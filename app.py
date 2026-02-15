import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    /* MAIN LAYOUT */
    .block-container {padding-top: 1.5rem; padding-bottom: 0rem;}
    [data-testid="stSidebar"] {width: 310px !important;}
    
    /* SIDEBAR LIFT & GLOBAL SPACING */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0.6rem !important;
        padding-top: 0rem !important;
    }
    [data-testid="stSidebarNav"] {display: none;}
    [data-testid="stSidebar"] h1 {
        margin-top: -30px !important; 
        margin-bottom: 0.5rem !important;
        font-size: 1.7rem !important;
    }
    [data-testid="stSidebar"] h3 {
        margin-top: 0.4rem !important;
        margin-bottom: 0.1rem !important;
        font-size: 1.05rem !important;
        font-weight: 600;
    }
    [data-testid="stSidebar"] hr {margin: 0.3rem 0px !important;}

    /* TIGHT SLIDER LABELS BUT SPACED BLOCKS */
    .slider-label-row {
        display: flex;
        justify-content: space-between;
        font-size: 11px;
        color: #808495;
        margin-top: -22px; 
        margin-bottom: 12px; 
        padding: 0 5px;
    }

    /* CLEAN BUTTON STYLING (Column 6) */
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

# --- 3. DATA LOADING & PROPENSITY DEFINITIONS ---
# Category A: Asset Intensity (Beds and Facilities)
ASSET_CODES = {'HI', 'PSYH', 'RES', 'RL', 'RS', 'DT', 'ADTX', 'ODTX', 'BDTX', 'CDTX', 'MDTX'}
# Category B: Medical Intensity (Acute Services)
MEDICAL_CODES = {'METH', 'NXN', 'VTRL', 'LABT', 'MM', 'MSRV', 'UB', 'BERI', 'GH'}
# Category C: Operational Density (Continuum Services)
OPS_CODES = {'PH', 'IOP', 'CBT', 'DBT', 'MI', 'ANG', 'REL', 'TRC', 'SAE', 'TCC', 'CM', 'SS', 'TA'}

GOVT_CODES = {'FED', 'STG', 'VAMC', 'LCLG', 'GVT', 'STLG'}
NP_CODES = {'PVTN'}

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
        
        # Pre-Calculate Propensity Raw Counts
        rollup['n_assets'] = rollup['service_code_info'].apply(lambda x: count_category(x, ASSET_CODES))
        rollup['n_medical'] = rollup['service_code_info'].apply(lambda x: count_category(x, MEDICAL_CODES))
        rollup['n_ops'] = rollup['service_code_info'].apply(lambda x: count_category(x, OPS_CODES))
        
        rollup['is_govt'] = rollup['service_code_info'].str.contains('|'.join(GOVT_CODES), case=False, na=False)
        rollup['is_np'] = rollup['service_code_info'].str.contains('|'.join(NP_CODES), case=False, na=False)
        
        return rollup, len(df)
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

d, total_raw = load_data()

# --- 4. SCORING CONTROL BOARD (Sidebar) ---
st.sidebar.title("🎛️ Propensity Engine")

with st.sidebar.expander("Care Funnel Filters", expanded=True):
    inc_res = st.checkbox("Residential / Beds", value=True)
    inc_dtx = st.checkbox("Detox Capability", value=True)
    inc_hosp = st.checkbox("Hospital / Inpatient", value=True)

st.sidebar.subheader("Hurdle Intensity (%)")
# Sliders act as the MINIMUM percentage of categorical tags required to clear the hurdle
h_assets = st.sidebar.slider("Asset Intensity", 0, 100, 30)
st.sidebar.markdown('<div class="slider-label-row"><span>Lower Req.</span><span>Industrial</span></div>', unsafe_allow_html=True)

h_medical = st.sidebar.slider("Medical Intensity", 0, 100, 25)
st.sidebar.markdown('<div class="slider-label-row"><span>Lower Req.</span><span>Acute Medical</span></div>', unsafe_allow_html=True)

h_ops = st.sidebar.slider("Operational Density", 0, 100, 15)
st.sidebar.markdown('<div class="slider-label-row"><span>Lower Req.</span><span>Full Continuum</span></div>', unsafe_allow_html=True)

st.sidebar.divider()
exclude_explicit = st.sidebar.toggle("Exclude Explicit Govt / NP", value=True)

st.sidebar.divider()
min_floor = st.sidebar.slider("Minimum Propensity Floor", 0, 100, 45)
st.sidebar.number_input("Display Row Count", key="max_show", min_value=1, step=1)

# --- 5. SCORING & FILTERING ENGINE (The Logic Layer) ---
# Normalizing signals against the highest count in the dataset
a_max = d['n_assets'].max() if d['n_assets'].max() > 0 else 1
m_max = d['n_medical'].max() if d['n_medical'].max() > 0 else 1
o_max = d['n_ops'].max() if d['n_ops'].max() > 0 else 1

d['pct_assets'] = (d['n_assets'] / a_max) * 100
d['pct_medical'] = (d['n_medical'] / m_max) * 100
d['pct_ops'] = (d['n_ops'] / o_max) * 100

# The Propensity Score is the mean of the three signatures
d['Propensity Score'] = ((d['pct_assets'] + d['pct_medical'] + d['pct_ops']) / 3).round(0).astype(int)

# THE WATERFALL FILTERING
# Step 1: Unique Locations (Handled by Rollup)
count_unique = len(d)

# Step 2: Care Types Fit
patterns = []
if inc_res: patterns.append("RES|RL|RS")
if inc_dtx: patterns.append("DT|ADTX")
if inc_hosp: patterns.append("HI|PSY")
d_work = d[d['service_code_info'].str.contains("|".join(patterns), case=False, na=False)] if patterns else d
count_fit = len(d_work)

# Step 3: Score Fit (Propensity Hurdles + Floor)
d_scored = d_work[
    (d_work['pct_assets'] >= h_assets) &
    (d_work['pct_medical'] >= h_medical) &
    (d_work['pct_ops'] >= h_ops) &
    (d_work['Propensity Score'] >= min_floor)
]
count_scored = len(d_scored)

# Step 4: Total Qualified (Ownership Cut)
d_final = d_scored.copy()
if exclude_explicit:
    d_final = d_final[~(d_final['is_govt'] | d_final['is_np'])]
count_final = len(d_final)

# Sort for Display
d_final = d_final.sort_values(by=['Propensity Score', 'name1'], ascending=[False, True])

# --- 6. TIE DETECTION ---
current_limit = int(st.session_state.max_show)
count_ties = 0
if count_final > current_limit:
    cutoff_score = d_final.iloc[current_limit - 1]['Propensity Score']
    count_ties = len(d_final.iloc[current_limit:][d_final.iloc[current_limit:]['Propensity Score'] == cutoff_score])
    display_df = d_final.head(current_limit).copy()
else:
    display_df = d_final.copy()

# --- 7. MAIN VIEW ---
st.title("📊 Commercial Propensity Targets")

# WATERFALL METRICS
m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("1. Universe", f"{total_raw:,}")
m2.metric("2. Unique Locs", f"{count_unique:,}", delta=f"-{total_raw - count_unique:,}", delta_color="off")
m3.metric("3. Care Fit", f"{count_fit:,}", delta=f"-{count_unique - count_fit:,}", delta_color="off")
m4.metric("4. Score Fit", f"{count_scored:,}", delta=f"-{count_fit - count_scored:,}", delta_color="off")
m5.metric("5. Qualified", f"{count_final:,}", delta=f"-{count_scored - count_final:,}", delta_color="off")

if count_ties > 0:
    m6.metric("6. Ties Hidden", f"{count_ties:,}")
    if m6.button("➕ Include Ties"):
        st.session_state.max_show += count_ties
        st.rerun()

# SEARCH AND FILTERS
c_search, c_state = st.columns([2, 1])
search = c_search.text_input("🔍 Search Name").lower()
states = c_state.multiselect("📍 State", options=sorted(d['state_clean'].unique()))

if search: display_df = display_df[display_df['name1'].str.lower().str.contains(search)]
if states: display_df = display_df[display_df['state_clean'].isin(states)]

# Rank Inject
display_df = display_df.reset_index(drop=True)
display_df.insert(0, 'Rank', display_df.index + 1)

# DATA TABLE
st.dataframe(
    display_df[['Rank', 'name1', 'Location', 'phone', 'Propensity Score', 'orig_row']].rename(
        columns={
            'name1': 'Facility Name', 
            'phone': 'Phone', 
            'orig_row': 'Source Row(s)'
        }
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

st.download_button("📥 Download (CSV)", d_final.to_csv(index=False).encode('utf-8'), "Propensity_Targets.csv")
