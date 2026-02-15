import streamlit as st
import pandas as pd
import numpy as np
import gspread
import re
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="Rounding Solution Targets", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 1.5rem; padding-bottom: 0rem;}
    [data-testid="stSidebar"] {width: 320px !important;}
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

# --- 3. SCORING DEFINITIONS ---

# Service code categories for scoring
LOC_INDICATORS = {
    'PSYCH_HOSPITAL': ['PSYH'],
    'HOSPITAL_INPATIENT': ['HI'],  # Will use word boundary regex
    'RESIDENTIAL': ['RES', 'RL', 'RS'],
    'DETOX': ['DT', 'ADTX', 'BDTX', 'CDTX', 'MDTX', 'ODTX']
}

CLINICAL_SOPHISTICATION = {
    'MAT_MEDICATIONS': ['METH', 'BERI', 'NXN', 'VTRL', 'BWN', 'BWON', 'BSDM'],  # 4 pts each
    'PSYCH_SERVICES': ['MMD', 'ANTPYCH', 'LABT', 'MHPA'],  # 3 pts each
    'MEDICAL_SERVICES': ['MSRV', 'LABT', 'MM', 'UB', 'MHPA', 'IPC']  # 2 pts each
}

ACUITY_INDICATORS = {
    'COOCCURRING': ['SUMH'],  # 10 pts
    'SMI_PROGRAMS': ['SMI'],  # 8 pts
    'DETOX': ['DT', 'ADTX', 'BDTX', 'CDTX', 'MDTX', 'ODTX'],  # 7 pts
    'CRISIS_SERVICES': ['CIT', 'PEON', 'PEOFF', 'WI']  # 5 pts
}

GOVT_CODES = ['FED', 'STG', 'VAMC', 'LCCG', 'GVT', 'STLG', 'TBG']

# --- 4. SCORING FUNCTIONS ---

def has_code(codes_str, code_list):
    """Check if any code from list exists in codes string"""
    codes_upper = str(codes_str).upper()
    return any(code in codes_upper for code in code_list)

def count_codes(codes_str, code_list):
    """Count how many codes from list exist in codes string"""
    codes_upper = str(codes_str).upper()
    return sum(1 for code in code_list if code in codes_upper)

def calculate_loc_score(row, weights=None):
    """Calculate Level of Care score (0-40 points)"""
    if weights is None:
        weights = {'psych': 40, 'hosp': 35, 'res_dtx': 30, 'res': 25}
    
    codes = str(row['service_code_info']).upper()
    
    # Check for psychiatric hospital
    if 'PSYH' in codes:
        return weights['psych']
    
    # Check for hospital inpatient (need word boundary to avoid HID, HIT, etc.)
    # Service codes are separated by spaces or asterisks like "SA * HI * OP"
    if re.search(r'\bHI\b', codes):
        return weights['hosp']
    
    # Check for residential
    has_residential = has_code(codes, LOC_INDICATORS['RESIDENTIAL'])
    has_detox = has_code(codes, LOC_INDICATORS['DETOX'])
    
    if has_residential:
        return weights['res_dtx'] if has_detox else weights['res']
    elif has_detox:
        return 20
    
    return 0  # Outpatient only

def calculate_sophistication_score(row, weights=None):
    """Calculate Clinical Sophistication score (0-30 points)"""
    if weights is None:
        weights = {'mat': 4, 'psych': 3, 'med': 2}
    
    codes = str(row['service_code_info']).upper()
    
    mat_count = count_codes(codes, CLINICAL_SOPHISTICATION['MAT_MEDICATIONS'])
    psych_count = count_codes(codes, CLINICAL_SOPHISTICATION['PSYCH_SERVICES'])
    medical_count = count_codes(codes, CLINICAL_SOPHISTICATION['MEDICAL_SERVICES'])
    
    score = (mat_count * weights['mat']) + (psych_count * weights['psych']) + (medical_count * weights['med'])
    
    return min(30, score)  # Cap at 30

def calculate_acuity_score(row, weights=None):
    """Calculate Risk/Acuity score (0-30 points)"""
    if weights is None:
        weights = {'cooccur': 10, 'smi': 8, 'detox': 7, 'crisis': 5}
    
    codes = str(row['service_code_info']).upper()
    
    score = 0
    
    if has_code(codes, ACUITY_INDICATORS['COOCCURRING']):
        score += weights['cooccur']
    
    if has_code(codes, ACUITY_INDICATORS['SMI_PROGRAMS']):
        score += weights['smi']
    
    if has_code(codes, ACUITY_INDICATORS['DETOX']):
        score += weights['detox']
    
    if has_code(codes, ACUITY_INDICATORS['CRISIS_SERVICES']):
        score += weights['crisis']
    
    return min(30, score)  # Cap at 30

def get_setting_type(row):
    """Determine primary setting type for display"""
    codes = str(row['service_code_info']).upper()
    
    if 'PSYH' in codes:
        return 'Psychiatric Hospital'
    
    # Use regex for HI detection (word boundary)
    if re.search(r'\bHI\b', codes):
        return 'Hospital Inpatient'
    
    has_residential = has_code(codes, LOC_INDICATORS['RESIDENTIAL'])
    has_detox = has_code(codes, LOC_INDICATORS['DETOX'])
    
    if has_residential and has_detox:
        return 'Residential + Detox'
    elif has_residential:
        return 'Residential'
    elif has_detox:
        return 'Detox Only'
    
    return 'Outpatient'

def merge_tags(series):
    """Merge service code tags from multiple rows"""
    all_tags = " * ".join(series.astype(str)).split('*')
    unique_tags = sorted(list(set([t.strip() for t in all_tags if t.strip()])))
    return " * ".join(unique_tags)

# --- 5. DATA LOADING ---

@st.cache_data(ttl=3600)
def load_data():
    """Load data from Google Sheet and calculate propensity scores"""
    
    # Connect to Google Sheets
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)
    
    try:
        sheet = client.open("SAMHSA_Master_Data").sheet1
        df = pd.DataFrame(sheet.get_all_records())
        df['orig_row'] = df.index + 2
        
        total_raw = len(df)
        
        # Clean location fields
        df['city_clean'] = df['city'].fillna('').astype(str).str.title()
        df['state_clean'] = df['state'].fillna('').astype(str).str.upper()
        
        # Calculate scores for each row
        df['loc_score'] = df.apply(calculate_loc_score, axis=1)
        df['sophistication_score'] = df.apply(calculate_sophistication_score, axis=1)
        df['acuity_score'] = df.apply(calculate_acuity_score, axis=1)
        df['setting_type'] = df.apply(get_setting_type, axis=1)
        
        # Check if government facility
        df['is_govt'] = df['service_code_info'].apply(lambda x: has_code(x, GOVT_CODES))
        
        # Calculate total propensity score
        df['score'] = (
            df['loc_score'] + 
            df['sophistication_score'] + 
            df['acuity_score']
        ).round(0).astype(int)
        
        # Get key indicators for display
        df['has_cooccurring'] = df['service_code_info'].apply(
            lambda x: has_code(x, ACUITY_INDICATORS['COOCCURRING'])
        )
        df['has_smi'] = df['service_code_info'].apply(
            lambda x: has_code(x, ACUITY_INDICATORS['SMI_PROGRAMS'])
        )
        df['has_detox'] = df['service_code_info'].apply(
            lambda x: has_code(x, ACUITY_INDICATORS['DETOX'])
        )
        df['has_mat'] = df['service_code_info'].apply(
            lambda x: count_codes(x, CLINICAL_SOPHISTICATION['MAT_MEDICATIONS']) > 0
        )
        
        # Roll up by location (keep highest scoring record for each name+city+state)
        rollup = df.sort_values('score', ascending=False).groupby(
            ['name1', 'city_clean', 'state_clean'], 
            as_index=False
        ).agg({
            'service_code_info': merge_tags,
            'phone': 'first',
            'orig_row': lambda x: ", ".join(x.astype(str)),
            'score': 'max',
            'loc_score': 'max',
            'sophistication_score': 'max',
            'acuity_score': 'max',
            'setting_type': 'first',
            'Facility_Type': lambda x: ' & '.join(sorted(set(x))) if 'Facility_Type' in df.columns else 'Unknown',
            'is_govt': 'max',
            'has_cooccurring': 'max',
            'has_smi': 'max',
            'has_detox': 'max',
            'has_mat': 'max'
        })
        
        # Create location field
        rollup['Location'] = rollup.apply(
            lambda x: f"{x['city_clean']}, {x['state_clean']}" if x['city_clean'] else x['state_clean'], 
            axis=1
        )
        
        return rollup, total_raw
        
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}")
        st.stop()

# Load data
d, total_raw = load_data()
count_unique = len(d)

# --- 6. SIDEBAR CONTROL BOARD ---

st.sidebar.title("🎯 Target Filters")

with st.sidebar.expander("🏥 Care Settings", expanded=True):
    inc_psych_hosp = st.checkbox("Psychiatric Hospitals", value=True)
    inc_hosp_inpt = st.checkbox("Hospital Inpatient", value=True)
    inc_residential = st.checkbox("Residential (24hr)", value=True)
    inc_detox = st.checkbox("Detox Settings", value=False)

st.sidebar.divider()

with st.sidebar.expander("🎚️ Minimum Scores", expanded=False):
    min_loc = st.slider("Level of Care", 0, 40, 25, 5)
    min_clinical = st.slider("Clinical Capabilities", 0, 30, 0, 5)
    min_acuity = st.slider("Risk/Acuity", 0, 30, 0, 5)
    min_total_score = st.slider("Total Score", 0, 100, 50, 5)

st.sidebar.divider()

with st.sidebar.expander("💊 Other Treatments", expanded=False):
    require_cooccurring = st.checkbox("Co-Occurring Disorders (COD)", value=False)
    require_mat = st.checkbox("Medication-Assisted Therapy (MAT)", value=False)
    require_smi = st.checkbox("Severe Mental Illness (SMI)", value=False)

st.sidebar.divider()

exclude_govt = st.sidebar.toggle("Exclude Government Facilities", value=True)

st.sidebar.divider()

st.sidebar.number_input("Display Row Count", key="max_show", min_value=1, step=1)

# Advanced Settings (collapsed)
with st.sidebar.expander("⚙️ Advanced - Adjust Scoring Weights", expanded=False):
    st.markdown("**Level of Care Weights**")
    w_psych = st.number_input("Psychiatric Hospital", 0, 100, 40, 5, key="w_psych")
    w_hosp = st.number_input("Hospital Inpatient", 0, 100, 35, 5, key="w_hosp")
    w_res_dtx = st.number_input("Residential + Detox", 0, 100, 30, 5, key="w_res_dtx")
    w_res = st.number_input("Residential", 0, 100, 25, 5, key="w_res")
    
    st.markdown("**Clinical Sophistication Weights**")
    w_mat = st.number_input("MAT Medications (each)", 0, 10, 4, 1, key="w_mat")
    w_psych_svc = st.number_input("Psych Services (each)", 0, 10, 3, 1, key="w_psych_svc")
    w_med = st.number_input("Medical Services (each)", 0, 10, 2, 1, key="w_med")
    
    st.markdown("**Risk/Acuity Weights**")
    w_cooccur = st.number_input("Co-occurring", 0, 30, 10, 1, key="w_cooccur")
    w_smi = st.number_input("SMI Programs", 0, 30, 8, 1, key="w_smi")
    w_detox = st.number_input("Detox Capability", 0, 30, 7, 1, key="w_detox")
    w_crisis = st.number_input("Crisis Services", 0, 30, 5, 1, key="w_crisis")

# --- 7. FILTERING ENGINE ---

# Start with all data
d_work = d.copy()

# Build list of allowed setting types (OR logic)
allowed_settings = set()
if inc_psych_hosp:
    allowed_settings.add('Psychiatric Hospital')
if inc_hosp_inpt:
    allowed_settings.add('Hospital Inpatient')
if inc_residential:
    allowed_settings.add('Residential')
    allowed_settings.add('Residential + Detox')
if inc_detox:
    allowed_settings.add('Detox Only')
    allowed_settings.add('Residential + Detox')

# Filter by setting type
if allowed_settings:
    d_work = d_work[d_work['setting_type'].isin(allowed_settings)]
else:
    # If no settings selected, show all non-outpatient
    d_work = d_work[d_work['setting_type'] != 'Outpatient']

count_setting = len(d_work)

# Apply score thresholds
d_work = d_work[
    (d_work['loc_score'] >= min_loc) &
    (d_work['sophistication_score'] >= min_clinical) &
    (d_work['acuity_score'] >= min_acuity) &
    (d_work['score'] >= min_total_score)
]

count_scored = len(d_work)

# Apply capability filters
if require_cooccurring:
    d_work = d_work[d_work['has_cooccurring']]
if require_mat:
    d_work = d_work[d_work['has_mat']]
if require_smi:
    d_work = d_work[d_work['has_smi']]

count_capability = len(d_work)

# Apply ownership filter
if exclude_govt:
    d_work = d_work[~d_work['is_govt']]

count_final = len(d_work)

# Sort by propensity score
d_final = d_work.sort_values(by=['score', 'name1'], ascending=[False, True]).copy()

# --- 8. TIE DETECTION ---

current_limit = int(st.session_state.max_show)
count_ties = 0

if count_final > current_limit:
    cutoff_score = d_final.iloc[current_limit - 1]['score']
    count_ties = len(d_final.iloc[current_limit:][d_final.iloc[current_limit:]['score'] == cutoff_score])
    display_df = d_final.head(current_limit).copy()
else:
    display_df = d_final.copy()

# --- 9. MAIN VIEW ---

st.title("🎯 Rounding Solution Target Facilities")

st.markdown("""
**Scoring:** Facilities ranked 0-100 based on care setting, clinical capabilities, and risk indicators  
**Filters:** Exclude outpatient-only facilities and government entities (when enabled)
""")

# Metrics
c1, c2, c3, c4, c5, c6 = st.columns(6)

c1.metric("1. Universe", f"{total_raw:,}")
c2.metric("2. Locations", f"{count_unique:,}", delta=f"-{total_raw - count_unique:,}", delta_color="off")
c3.metric("3. Setting Fit", f"{count_setting:,}", delta=f"-{count_unique - count_setting:,}", delta_color="off")
c4.metric("4. Score Fit", f"{count_scored:,}", delta=f"-{count_setting - count_scored:,}", delta_color="off")
c5.metric("5. Qualified", f"{count_final:,}", delta=f"-{count_scored - count_final:,}", delta_color="off")

if count_ties > 0:
    c6.metric("6. Ties", f"{count_ties:,}")
    if c6.button("➕ Include Ties"):
        st.session_state.max_show += count_ties
        st.rerun()

st.divider()

# Search and filter
c_search, c_state = st.columns([2, 1])
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 State Filter", options=sorted(d['state_clean'].unique()))

if search:
    display_df = display_df[display_df['name1'].str.lower().str.contains(search)]
if states:
    display_df = display_df[display_df['state_clean'].isin(states)]

# Add indicators column
display_df['Indicators'] = (
    display_df['has_cooccurring'].map({True: '🔀', False: ''}) + 
    display_df['has_detox'].map({True: '💊', False: ''}) +
    display_df['has_smi'].map({True: '🧠', False: ''}) +
    display_df['has_mat'].map({True: '💉', False: ''})
).str.strip()

# Prepare display
display_df = display_df.reset_index(drop=True)
display_df.insert(0, 'Rank', display_df.index + 1)

# Display dataframe
st.dataframe(
    display_df[[
        'Rank', 'name1', 'Location', 'setting_type', 
        'score', 'loc_score', 'sophistication_score', 'acuity_score',
        'Indicators', 'orig_row'
    ]].rename(columns={
        'name1': 'Facility Name',
        'setting_type': 'Setting Type',
        'score': 'Total Score',
        'loc_score': 'LOC',
        'sophistication_score': 'Clinical',
        'acuity_score': 'Acuity',
        'orig_row': 'Source Row(s)'
    }), 
    use_container_width=True, 
    height=550, 
    hide_index=True, 
    column_config={
        "Rank": st.column_config.NumberColumn("Rank", width=60),
        "Facility Name": st.column_config.TextColumn("Facility Name", width=250),
        "Location": st.column_config.TextColumn("Location", width=180),
        "Setting Type": st.column_config.TextColumn("Setting", width=150),
        "Total Score": st.column_config.ProgressColumn("Score", format="%d", min_value=0, max_value=100, width=80),
        "LOC": st.column_config.NumberColumn("LOC", width=50),
        "Clinical": st.column_config.NumberColumn("Clin", width=50),
        "Acuity": st.column_config.NumberColumn("Acuity", width=50),
        "Indicators": st.column_config.TextColumn("Flags", width=80),
        "Source Row(s)": st.column_config.TextColumn("Source Row(s)", width=150)
    }
)

# Legend
st.markdown("""
**Indicators:** 🔀 Co-occurring | 💊 Detox | 🧠 SMI Programs | 💉 MAT Capable
""")

# Download
st.download_button(
    "📥 Download Current View (CSV)", 
    display_df.to_csv(index=False).encode('utf-8'), 
    "rounding_targets.csv",
    mime="text/csv"
)

# Summary statistics
with st.expander("📊 View Summary Statistics"):
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Setting Type Distribution**")
        if len(display_df) > 0:
            st.dataframe(
                display_df['setting_type'].value_counts().reset_index()
                .rename(columns={'setting_type': 'Setting Type', 'count': 'Count'}),
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("No facilities to display")
    
    with col2:
        st.markdown("**Top States**")
        if len(display_df) > 0:
            st.dataframe(
                display_df['state_clean'].value_counts().head(10).reset_index()
                .rename(columns={'state_clean': 'State', 'count': 'Count'}),
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("No facilities to display")
    
    if len(display_df) > 0:
        st.markdown("**Score Distribution**")
        col3, col4, col5 = st.columns(3)
        col3.metric("Mean Score", f"{display_df['score'].mean():.1f}")
        col4.metric("Median Score", f"{display_df['score'].median():.1f}")
        col5.metric("Score Range", f"{display_df['score'].min():.0f} - {display_df['score'].max():.0f}")

with st.expander("🔍 Debug Info - Data Quality Check"):
    st.markdown("**All Unique Setting Types in Data**")
    all_settings = d['setting_type'].value_counts().reset_index()
    st.dataframe(all_settings.rename(columns={'setting_type': 'Setting Type', 'count': 'Count'}), hide_index=True)
    
    st.markdown("**Score Distribution (All Data)**")
    col_d1, col_d2, col_d3 = st.columns(3)
    col_d1.metric("Mean LOC", f"{d['loc_score'].mean():.1f}")
    col_d2.metric("Mean Clinical", f"{d['sophistication_score'].mean():.1f}")
    col_d3.metric("Mean Acuity", f"{d['acuity_score'].mean():.1f}")
    
    st.markdown("**Currently Selected Settings**")
    selected = []
    if inc_psych_hosp: selected.append("Psychiatric Hospital")
    if inc_hosp_inpt: selected.append("Hospital Inpatient")
    if inc_residential: selected.append("Residential/Residential + Detox")
    if inc_detox: selected.append("Detox Only/Residential + Detox")
    st.write(selected if selected else "None (showing all non-outpatient)")
    
    st.markdown("**Active Filters**")
    st.write(f"- Min LOC Score: {min_loc}")
    st.write(f"- Min Clinical Score: {min_clinical}")
    st.write(f"- Min Acuity Score: {min_acuity}")
    st.write(f"- Min Total Score: {min_total_score}")
    st.write(f"- Exclude Government: {exclude_govt}")
    st.write(f"- Require COD: {require_cooccurring}")
    st.write(f"- Require MAT: {require_mat}")
    st.write(f"- Require SMI: {require_smi}")
