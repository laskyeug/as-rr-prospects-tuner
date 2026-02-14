import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    [data-testid="stSidebar"] {width: 300px !important;}
    div[data-testid="stMetric"] {padding: 0px 0px 5px 0px;}
    .stCheckbox {margin-bottom: -15px;}
    .stSlider {padding-top: 10px; padding-bottom: 20px;}
    </style>
    """, unsafe_allow_html=True)

# --- 2. CODE CATEGORIZATION ---
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

def merge_rows(series):
    return ", ".join(series.astype(str))

@st.cache_data(ttl=3600)
def load_data():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    try:
        sheet = client.open("SAMHSA_Master_Data").sheet1
        df = pd.DataFrame(sheet.get_all_records())
        df['orig_row'] = df.index + 2
        
        # Pre-processing
        df['city'] = df['city'].fillna('')
        df['state'] = df['state'].fillna('')
        df['city_clean'] = df['city'].astype(str).str.title()
        df['state_clean'] = df['state'].astype(str).str.upper()
        
        # Rollup
        rollup = df.groupby(['name1', 'city_clean', 'state_clean']).agg({
            'service_code_info': merge_tags,
            'phone': 'first',
            'orig_row': merge_rows
        }).reset_index()
        
        rollup['Location'] = rollup.apply(
            lambda x: f"{x['city_clean']}, {x['state_clean']}" if x['city_clean'] else x['state_clean'], 
            axis=1
        )
        
        # Pre-calculate category counts for speed
        rollup['n_infra'] = rollup['service_code_info'].apply(lambda x: count_category(x, INFRA_CODES))
        rollup['n_clinical'] = rollup['service_code_info'].apply(lambda x: count_category(x, CLINICAL_CODES))
        rollup['n_private'] = rollup['service_code_info'].apply(lambda x: count_category(x, PRIVATE_CODES))
        rollup['n_standard'] = rollup['service_code_info'].apply(lambda x: count_category(x, STANDARD_CODES))
        
        return rollup, len(df)
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

d, total_raw = load_data()

# --- 3. SCORING CONTROL BOARD ---
st.sidebar.title("🎛️ Scoring Controls")

with st.sidebar.expander("1. Filter Care Types (Include)", expanded=True):
    inc_res = st.checkbox("Residential", value=True)
    inc_dtx = st.checkbox("Detox (DT)")
    inc_hosp = st.checkbox("Hospital / Inpatient")

st.sidebar.divider()

st.sidebar.subheader("2. Define 'Propensity'")
st.sidebar.caption("Adjust the sliders to change how facilities are ranked:")

# Factor Sliders
w_infra = st.sidebar.slider("Infrastructure Weight", 1, 10, 5, help="Value of 'Big Iron' licenses (Inpatient, Res, Detox)")
w_priv = st.sidebar.slider("Private Ownership Bonus", 0, 20, 10, help="Bonus for Private For-Profit status")
w_clin = st.sidebar.slider("Clinical Depth Weight", 1, 10, 3, help="Value of Medical capabilities (MAT, Dual-Diag)")
w_std = st.sidebar.slider("Standard Services Weight", 0, 5, 1, help="Value of generic Outpatient/Therapy codes")

st.sidebar.divider()

st.sidebar.subheader("3. Cutoff")
min_propensity = st.sidebar.slider("Min. Score Threshold", 0, 100, 40)

# Settings
st.sidebar.divider()
exclude_gov = st.sidebar.toggle("Exclude Govt/VAMC", value=True)
max_show = st.sidebar.number_input("Max Rows", value=1000)

# --- 4. SCORING ENGINE ---
# Calculate Score based on current slider positions
d['Raw_Score'] = (
    (d['n_infra'] * w_infra) + 
    (d['n_clinical'] * w_clin) + 
    (d['n_private'] * w_priv) + 
    (d['n_standard'] * w_std)
)

# Normalize
current_max = d['Raw_Score'].max()
if current_max == 0: current_max = 1
d['Propensity Score'] = ((d['Raw_Score'] / current_max) * 100).round(0).astype(int)

# --- 5. FILTERING ---
d_filtered = d.copy()

patterns = []
if inc_res: patterns.append("RES|RL|RS")
if inc_dtx: patterns.append("DT")
if inc_hosp: patterns.append("HI|PSY")

if patterns:
    combined_pattern = "|".join(patterns)
    d_filtered = d_filtered[d_filtered['service_code_info'].str.contains(combined_pattern, case=False, na=False)]
else:
    pass 

d_filtered = d_filtered[d_filtered['Propensity Score'] >= min_propensity]

if exclude_gov:
    d_filtered = d_filtered[~d_filtered['service_code_info'].str.contains('STG|FED|VAMC', case=False, na=False)]

d_filtered = d_filtered.sort_values(by=['Propensity Score', 'Location', 'name1'], ascending=[False, True, True])

# --- 6. OUTPUT ---
st.title("📊 Scored Prospects")

# Clean 3-Column Layout
m1, m2, m3 = st.columns(3)
m1.metric("Universe Total", f"{total_raw:,}")
m2.metric("Qualifying Facilities", f"{len(d_filtered):,}")
m3.metric("Avg Propensity", f"{int(d_filtered['Propensity Score'].mean()) if not d_filtered.empty else 0}%")

# Search
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter by State", options=sorted(d['state_clean'].unique()))

if search: d_filtered = d_filtered[d_filtered['name1'].str.lower().str.contains(search)]
if states: d_filtered = d_filtered[d_filtered['state_clean'].isin(states)]

# Table
if d_filtered.empty:
    st.warning("⚠️ No prospects found. Try adjusting your sliders or filters.")
else:
    output_df = d_filtered.head(max_show).reset_index(drop=True)
    output_df.index = output_df.index + 1
    output_df.index.name = "Rank"

    output_df = output_df.rename(columns={
        'name1': 'Facility Name', 
        'phone': 'Phone',
        'orig_row': 'Source'
    })

    st.dataframe(
        output_df[['Facility Name', 'Location', 'Phone', 'Propensity Score', 'Source']], 
        use_container_width=True,
        height=550,
        column_config={
            "Source": st.column_config.TextColumn("Source Row(s)", width="small"),
            "Propensity Score": st.column_config.ProgressColumn(
                "Propensity",
                format="%d",
                min_value=0,
                max_value=100,
            ),
        }
    )

st.download_button("📥 Download Scored List (CSV)", d_filtered.to_csv(index=False).encode('utf-8'), "Scored_Prospects.csv")
