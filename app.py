import streamlit as st
import pandas as pd
import gspread
from collections import defaultdict
from google.oauth2.service_account import Credentials

# --- 1. CONFIG & STYLING ---
st.set_page_config(page_title="A/S RR Tuner", layout="wide")

st.markdown("""
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 0rem;}
    [data-testid="stSidebar"] {width: 250px !important;}
    div[data-testid="stMetric"] {padding: 0px 0px 5px 0px;}
    .stCheckbox {margin-bottom: -15px;}
    </style>
    """, unsafe_allow_html=True)

# --- 2. WEIGHTED SCORING ENGINE ---
# This dictionary defines the "Value" of each tag.
WEIGHT_MAP = defaultdict(lambda: 0.1) # Default weight for unknown/noise codes is 0.1

# PLATINUM: Core Infrastructure & High Acuity (5.0 pts)
for code in ['HI', 'PSYH', 'RES', 'RL', 'RS', 'DT', 'ADTX', 'ODTX', 'BDTX', 'CDTX', 'MDTX', 'SUMH', 'MH', 'SA', 'OTP']:
    WEIGHT_MAP[code] = 5.0

# GOLD: Business Drivers & Clinical Depth (3.0 pts)
for code in ['PVTP', 'UB', 'MM', 'VTRL', 'BERI', 'GH', 'CO', 'VET', 'ADM', 'PW', 'SE', 'LABT', 'MSRV']:
    WEIGHT_MAP[code] = 3.0

# SILVER: Standard Therapies & Outpatient (1.0 pt)
for code in ['OP', 'IOP', 'PH', 'CBT', 'DBT', 'MI', 'ANG', 'REL', 'TRC', 'SAE', 'TCC', 'CM', 'SS', 'TA']:
    WEIGHT_MAP[code] = 1.0

# NOISE: Languages, Policies, Gov Ownership (0.0 - 0.1 pts)
# (Explicitly setting low value tags to ensure they don't inflate scores)
for code in ['SPS', 'AH', 'SMOP', 'SMON', 'SMPD', 'VAPP', 'VAPN', 'VPPD', 'LCCG', 'STG', 'FED', 'VAMC']:
    WEIGHT_MAP[code] = 0.05

def calculate_weighted_score(series):
    # Splits the string by '*' and sums the weights
    codes = "*".join(series.astype(str)).split('*')
    unique_codes = set([c.strip() for c in codes if c.strip()])
    
    score = 0.0
    for c in unique_codes:
        # Check for Language codes (F followed by numbers) to treat as noise
        if c.startswith('F') and c[1:].isdigit():
            score += 0.01
        else:
            score += WEIGHT_MAP[c]
            
    return score

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
        
        # --- PRE-PROCESSING ---
        df['city'] = df['city'].fillna('')
        df['state'] = df['state'].fillna('')
        df['city_clean'] = df['city'].astype(str).str.title()
        df['state_clean'] = df['state'].astype(str).str.upper()
        
        # --- ROLLUP & SCORING ---
        rollup = df.groupby(['name1', 'city_clean', 'state_clean']).agg({
            'service_code_info': merge_tags,
            'phone': 'first',
            'orig_row': merge_rows
        }).reset_index()
        
        rollup['Location'] = rollup.apply(
            lambda x: f"{x['city_clean']}, {x['state_clean']}" if x['city_clean'] else x['state_clean'], 
            axis=1
        )
        
        # Apply Weighted Scoring
        rollup['weighted_score'] = rollup['service_code_info'].apply(lambda x: calculate_weighted_score(pd.Series([x])))
        
        # Normalize to 0-100 based on the highest scoring facility
        u_max = rollup['weighted_score'].max() if not rollup.empty else 1
        rollup['Propensity Score'] = ((rollup['weighted_score'] / u_max) * 100).round(0).astype(int)
        
        return rollup, u_max, len(df)
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}"); st.stop()

# Load Data
d, u_max_score, total_raw = load_data()

# --- 3. TUNER RIBBON ---
st.sidebar.title("🎯 Prospect Filters")

st.sidebar.subheader("Care Type(s) To Include")
inc_res = st.sidebar.checkbox("Residential", value=True)
inc_dtx = st.sidebar.checkbox("Detox (DT)")
inc_hosp = st.sidebar.checkbox("Hospital / Inpatient")

st.sidebar.divider()

st.sidebar.subheader("Propensity Threshold")
min_propensity = st.sidebar.slider("Min. Propensity Score", 0, 100, 40, step=1)

# Updated Sidebar Legend
st.sidebar.info(f"""
**Weighted Logic:**
* **Platinum (5pts):** Inpatient, Residential, Detox, Dual-Diag.
* **Gold (3pts):** Private Owner, MAT, Vets, Co-occurring.
* **Silver (1pt):** Standard Therapy, Outpatient.
* **Noise (0pt):** Languages, Policies.
""")

st.sidebar.divider()

st.sidebar.subheader("Settings")
exclude_gov = st.sidebar.toggle("Exclude Govt/VAMC", value=True)
only_private = st.sidebar.toggle("Only Private For-Profit")
max_show = st.sidebar.number_input("Max Rows Shown", value=1000)

# --- 4. FILTER ENGINE ---
d_filtered = d.copy()

patterns = []
if inc_res: patterns.append("RES|RL|RS")
if inc_dtx: patterns.append("DT")
if inc_hosp: patterns.append("HI|PSY")

if patterns:
    combined_pattern = "|".join(patterns)
    d_filtered = d_filtered[d_filtered['service_code_info'].str.contains(combined_pattern, case=False, na=False)]
else:
    # If explicit filters are empty, we might still want to see data if weighting is doing the heavy lifting?
    # User said "outpatient only should be excluded anyway".
    # Sticking to the filter pattern ensures we honor that exclusion.
    d_filtered = pd.DataFrame(columns=d.columns)

d_filtered = d_filtered[d_filtered['Propensity Score'] >= min_propensity]

if exclude_gov:
    d_filtered = d_filtered[~d_filtered['service_code_info'].str.contains('STG|FED|VAMC', case=False, na=False)]
if only_private:
    d_filtered = d_filtered[d_filtered['service_code_info'].str.contains('PVTP', case=False, na=False)]

# Sort by Score (High to Low), then Location (A-Z)
d_filtered = d_filtered.sort_values(by=['Propensity Score', 'Location', 'name1'], ascending=[False, True, True])

# --- 5. MAIN OUTPUT PANE ---
st.title("📊 Scored Prospects (Weighted)")

# Metrics
m1, m2, m3, m4 = st.columns(4)
m1.metric("Universe Total", f"{total_raw:,}")
m2.metric("Qualifying Facilities", f"{len(d_filtered):,}")
m3.metric("Avg Propensity", f"{int(d_filtered['Propensity Score'].mean()) if not d_filtered.empty else 0}%")
m4.metric("Max Weighted Score", f"{int(u_max_score)}")

# Filters
c_search, c_state = st.columns(2)
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 Filter by State", options=sorted(d['state_clean'].unique()))

if search: d_filtered = d_filtered[d_filtered['name1'].str.lower().str.contains(search)]
if states: d_filtered = d_filtered[d_filtered['state_clean'].isin(states)]

# Table Display
if d_filtered.empty:
    st.warning("⚠️ No prospects found. Try lowering the Propensity Threshold or adding more Care Types.")
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
                help="Weighted Score (0-100)",
                format="%d",
                min_value=0,
                max_value=100,
            ),
        }
    )

st.download_button("📥 Download Scored List (CSV)", d_filtered.to_csv(index=False).encode('utf-8'), "Scored_Prospects.csv")
