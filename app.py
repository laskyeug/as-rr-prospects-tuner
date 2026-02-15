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
    /* Button Styling to fit Metric Column */
    div.stButton > button {
        width: 100%;
        margin-top: -15px; 
    }
    </style>
    """, unsafe_allow_html=True)

# --- 2. SESSION STATE INIT ---
if 'max_show' not in st.session_state:
    st.session_state.max_show = 1000

# --- 3. CODE CATEGORIZATION ---
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
        
        # Pre-calculate category counts
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

with st.sidebar.expander("1. Filter Care Types (Include)", expanded=True):
    inc_res = st.checkbox("Residential", value=True)
    inc_dtx = st.checkbox("Detox (DT)")
    inc_hosp = st.checkbox("Hospital / Inpatient")

st.sidebar.divider()

st.sidebar.subheader("2. Define 'Propensity'")
# Factor Sliders
w_infra = st.sidebar.slider("Infrastructure Weight", 1, 10, 5)
w_priv = st.sidebar.slider("Private Ownership Bonus", 0, 20, 10)
w_clin = st.sidebar.slider("Clinical Depth Weight", 1, 10, 3)
w_std = st.sidebar.slider("Standard Services Weight", 0, 5, 1)

st.sidebar.divider()

st.sidebar.subheader("3. Cutoff & Limits")
min_propensity = st.sidebar.slider("Min. Score Threshold", 0, 100, 40)

# SYNC LOGIC: Function to sync widget -> state
def update_max_show():
    st.session_state.max_show = st.session_state.max_show_input

# Max Rows Input (Linked to session state)
st.sidebar.number_input(
    "Download Size (Rows)", 
    value=st.session_state.max_show,
    key="max_show_input",
    on_change=update_max_show
)

st.sidebar.divider()
exclude_gov = st.sidebar.toggle("Exclude Govt/VAMC", value=True)

# --- 5. SCORING & WATERFALL ---

# Steps 1-2
count_unique = len(d)

# Step 3: Care Type Fit
d['Raw_Score'] = (
    (d['n_infra'] * w_infra) + 
    (d['n_clinical'] * w_clin) + 
    (d['n_private'] * w_priv) + 
    (d['n_standard'] * w_std)
)
current_max = d['Raw_Score'].max()
if current_max == 0: current_max = 1
d['Propensity Score'] = ((d['Raw_Score'] / current_max) * 100).round(0).astype(int)

d_services = d.copy()
patterns = []
if inc_res: patterns.append("RES|RL|RS")
if inc_dtx: patterns.append("DT")
if inc_hosp: patterns.append("HI|PSY")

if patterns:
    combined_pattern = "|".join(patterns)
    d_services = d_services[d_services['service_code_info'].str.contains(combined_pattern, case=False, na=False)]
else:
    pass 
count_services = len(d_services)

# Step 4: Score Fit
d_scored = d_services[d_services['Propensity Score'] >= min_propensity]
count_scored = len(d_scored)

# Step 5: Final Qualified
d_final = d_scored.copy()
if exclude_gov:
    d_final = d_final[~d_final['service_code_info'].str.contains('STG|FED|VAMC', case=False, na=False)]
count_final = len(d_final)

# Sort
d_final = d_final.sort_values(by=['Propensity Score', 'Location', 'name1'], ascending=[False, True, True])

# --- 6. TIE DETECTION LOGIC ---
count_ties = 0
cutoff_score = 0
display_df = d_final.copy()
has_hidden_ties = False

if count_final > st.session_state.max_show:
    # Check the last visible record
    cutoff_record = d_final.iloc[st.session_state.max_show - 1] 
    cutoff_score = cutoff_record['Propensity Score']
    
    # Check hidden records
    hidden_records = d_final.iloc[st.session_state.max_show:]
    tie_matches = hidden_records[hidden_records['Propensity Score'] == cutoff_score]
    count_ties = len(tie_matches)
    
    if count_ties > 0:
        has_hidden_ties = True
    
    # Apply strict cut for display
    display_df = d_final.head(st.session_state.max_show)
else:
    display_df = d_final

# --- 7. OUTPUT ---
st.title("📊 Scored Prospects")

# METRICS
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("1. Universe", f"{total_raw:,}")
c2.metric("2. Unique Locs", f"{count_unique:,}", delta=f"-{total_raw - count_unique:,}", delta_color="off")
c3.metric("3. Care Type Fit", f"{count_services:,}", delta=f"-{count_unique - count_services:,}", delta_color="off")
c4.metric("4. Score Fit", f"{count_scored:,}", delta=f"-{count_services - count_scored:,}", delta_color="off")
c5.metric("5. Total Qualified", f"{count_final:,}", delta=f"-{count_scored - count_final:,}", delta_color="off")

# 6th Metric: ONLY APPEARS IF TIES EXIST
if has_hidden_ties:
    c6.metric("6. Hidden Ties", f"{count_ties:,}")
    if c6.button("➕ Adjust Download Size"):
        new_total = st.session_state.max_show + count_ties
        # Update BOTH state variables to force sync
        st.session_state.max_show = new_total
        st.session_state.max_show_input = new_total
        st.rerun()

# Search
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter by State", options=sorted(d['state_clean'].unique()))

if search: display_df = display_df[display_df['name1'].str.lower().str.contains(search)]
if states: display_df = display_df[display_df['state_clean'].isin(states)]

# Table
if display_df.empty:
    st.warning("⚠️ No prospects found.")
else:
    output_df = display_df.reset_index(drop=True)
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

st.download_button("📥 Download Scored List (CSV)", d_final.to_csv(index=False).encode('utf-8'), "Scored_Prospects.csv")
