import streamlit as st
import pandas as pd
import numpy as np
import gspread
import re
from google.oauth2.service_account import Credentials

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. CONFIG & STYLING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

st.set_page_config(page_title="Rounding Solution Targets", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 1.1rem; padding-bottom: 0rem; max-width: 100%;}
[data-testid="stSidebar"] {width: 340px !important;}
[data-testid="stSidebarNav"] {display: none;}
[data-testid="stSidebar"] h1 {margin-top: -30px !important; margin-bottom: 0.5rem !important; font-size: 1.7rem !important;}
[data-testid="stSidebar"] h3 {margin-top: 0.4rem !important; margin-bottom: 0.1rem !important; font-size: 1.05rem !important; font-weight: 600;}
[data-testid="stSidebar"] hr {margin: 0.3rem 0px !important;}
/* Tighten main content vertical spacing */
[data-testid="stMetric"] {padding-bottom: 0 !important;}
[data-testid="stMetricValue"] {font-size: 1.6rem !important;}
h1 {margin-bottom: 0 !important; padding-bottom: 0 !important; font-size: 1.8rem !important;}
hr {margin: 0.3rem 0 !important;}
.stDivider {margin: 0.2rem 0 !important;}
div[data-testid="stTextInput"], div[data-testid="stMultiSelect"] {margin-bottom: -0.5rem !important;}
/* Align tie button with metric row */
div[data-testid="stButton"] button {font-size: 0.8rem !important;}
div[data-testid="stHorizontalBlock"] {align-items: flex-start !important;}
.legend-text {font-size: 0.78rem; color: #808495; line-height: 1.4; margin-top: -0.3rem;}
.legend-text b {color: #b0b4c0;}
</style>
""", unsafe_allow_html=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. SESSION STATE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if 'max_show' not in st.session_state:
    st.session_state.max_show = 250
if 'pending_tie_add' not in st.session_state:
    st.session_state.pending_tie_add = 0

# Apply any pending tie addition (from previous run)
if st.session_state.pending_tie_add > 0:
    st.session_state.max_show += st.session_state.pending_tie_add
    st.session_state.pending_tie_add = 0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. SCORING DEFINITIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# --- Service Code Dictionaries ---

# Level of Care indicators (used for setting classification + scoring)
LOC_CODES = {
    'PSYCH_HOSPITAL': ['PSYH'],
    'INPATIENT_PSYCH_UNIT': ['IPSY'],
    'HOSPITAL_INPATIENT': ['HI'],       # word-boundary regex required
    'RTC': ['RTCA', 'RTCC'],            # Residential Treatment Centers
    'RESIDENTIAL': ['RES', 'RL', 'RS'],
    'DETOX': ['DT', 'ADTX', 'BDTX', 'CDTX', 'MDTX', 'ODTX'],
}

# MAT medications (tiered scoring)
MAT_MEDICATIONS = ['METH', 'BERI', 'NXN', 'VTRL', 'BWN', 'BWON', 'BSDM']

# Individual antipsychotic medications (tiered scoring by breadth)
ANTIPSYCHOTICS = [
    'CHLOR', 'DROPE', 'FLUPH', 'HALOP', 'LOXAP', 'PERPH', 'PIMOZ', 'PROCH',
    'THIOT', 'THIOR', 'TRIFL', 'ARIPI', 'ASENA', 'BREXP', 'CARIP', 'CLOZA',
    'ILOPE', 'LURAS', 'OLANZ', 'OLANZF', 'PALIP', 'QUETI', 'RISPE', 'ZIPRA'
]

# Advanced treatment modalities
ADVANCED_THERAPIES = ['DBT', 'EMDR', 'ECT', 'TMS', 'KIT']

# Sentinel event / acuity signals
ACUITY_CODES = {
    'COOCCURRING': ['SUMH'],
    'SMI_PROGRAMS': ['SMI'],
    'SUICIDE_PREVENTION': ['SPS'],
    'DETOX': ['DT', 'ADTX', 'BDTX', 'CDTX', 'MDTX', 'ODTX'],
    'CRISIS_SERVICES': ['CIT', 'PEON', 'PEOFF', 'WI'],
    'FIRST_EPISODE_PSYCHOSIS': ['PEFP'],
}

# Institutional quality signals
QUALITY_CODES = {
    'JOINT_COMMISSION': ['JC'],
    'CARF': ['CARF'],
    'NALOXONE_OD_ED': ['NOE'],
    'DISCHARGE_PLANNING': ['DP'],
    'OUTCOME_FOLLOWUP': ['OFD'],
    'METABOLIC_MONITORING': ['MST'],
    'INTEGRATED_PRIMARY_CARE': ['IPC'],
    'LAB_TESTING': ['LABT'],
}

# Government facility codes (for exclusion)
GOVT_CODES = ['FED', 'STG', 'VAMC', 'LCCG', 'GVT', 'STLG', 'TBG']

# Ownership codes
OWNERSHIP_CODES = {'FOR_PROFIT': ['PVTP'], 'NON_PROFIT': ['PVTN']}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. UTILITY FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def has_code(codes_str, code_list):
    """Check if any code from list exists in service code string."""
    c = str(codes_str).upper()
    return any(code in c for code in code_list)

def has_code_wb(codes_str, code):
    """Check for a code using word-boundary regex (for short codes like HI, JC)."""
    return bool(re.search(rf'\b{code}\b', str(codes_str).upper()))

def count_codes(codes_str, code_list):
    """Count how many codes from list exist in service code string."""
    c = str(codes_str).upper()
    return sum(1 for code in code_list if code in c)

def merge_tags(series):
    """Merge service code tags from multiple rows, dedup and sort."""
    all_tags = " * ".join(series.astype(str)).split('*')
    unique_tags = sorted(set(t.strip() for t in all_tags if t.strip()))
    return " * ".join(unique_tags)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. SETTING CLASSIFICATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def classify_setting(codes):
    """Classify facility into care setting with finer granularity."""
    c = str(codes).upper()

    # Highest → lowest acuity
    if 'PSYH' in c:
        return 'Psychiatric Hospital'
    if 'IPSY' in c:
        return 'Inpatient Psych Unit'
    if re.search(r'\bHI\b', c):
        return 'Hospital Inpatient'
    if 'RTCA' in c or 'RTCC' in c:
        return 'Residential Treatment Center'

    has_res = has_code(c, LOC_CODES['RESIDENTIAL'])
    has_dtx = has_code(c, LOC_CODES['DETOX'])

    if has_res and has_dtx:
        return 'Residential + Detox'
    if has_res:
        return 'Residential'
    if has_dtx:
        return 'Detox Only'

    return 'Outpatient'

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. FUZZY INFERENCE ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def infer_smi(codes, setting, facility_type, antipsych_count):
    """Infer SMI treatment capability. Returns (has_smi, source, confidence_penalty)."""
    c = str(codes).upper()

    if has_code(c, ['SMI']):
        return True, 'explicit', 0

    # Psych hospitals treat SMI by definition
    if setting == 'Psychiatric Hospital':
        return True, 'inferred_psych_hospital', 1
    if setting == 'Inpatient Psych Unit':
        return True, 'inferred_inpatient_psych', 2

    # Hospital inpatient + co-occurring
    if setting == 'Hospital Inpatient' and 'SUMH' in c:
        return True, 'inferred_hospital_cooccur', 3

    # MH facility with residential care
    if 'MH' in str(facility_type) and setting in ('Residential', 'Residential + Detox', 'Residential Treatment Center'):
        return True, 'inferred_mh_residential', 3

    # Facility prescribing 5+ antipsychotics likely treats SMI
    if antipsych_count >= 5:
        return True, 'inferred_antipsych_breadth', 4

    return False, 'none', 0

def infer_crisis(codes, setting):
    """Infer crisis services capability. Returns (has_crisis, source, confidence_penalty)."""
    c = str(codes).upper()

    if has_code(c, ACUITY_CODES['CRISIS_SERVICES']):
        return True, 'explicit', 0

    # Psych hospitals have crisis capability
    if setting == 'Psychiatric Hospital':
        return True, 'inferred_psych_hospital', 1
    if setting == 'Inpatient Psych Unit':
        return True, 'inferred_inpatient_psych', 2

    # Hospital inpatient + detox
    if setting == 'Hospital Inpatient' and has_code(c, LOC_CODES['DETOX']):
        return True, 'inferred_hospital_detox', 3

    # Residential + co-occurring + detox
    if has_code(c, LOC_CODES['RESIDENTIAL']) and 'SUMH' in c and has_code(c, LOC_CODES['DETOX']):
        return True, 'inferred_res_cooccur_detox', 4

    return False, 'none', 0

def infer_suicide_prevention(codes, setting, has_smi_flag):
    """Infer suicide prevention services. Returns (has_sps, source, confidence_penalty)."""
    c = str(codes).upper()

    if has_code_wb(c, 'SPS'):
        return True, 'explicit', 0

    # Psych hospitals / inpatient psych units
    if setting in ('Psychiatric Hospital', 'Inpatient Psych Unit'):
        return True, 'inferred_psych_setting', 1

    # Hospital inpatient with SMI
    if setting == 'Hospital Inpatient' and has_smi_flag:
        return True, 'inferred_hospital_smi', 3

    return False, 'none', 0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. SCORING FUNCTIONS — 4 PILLARS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Default weights (exposed in Advanced sidebar)
DEFAULT_WEIGHTS = {
    # --- Level of Care (0-30) ---
    'loc_psych_hospital': 30,
    'loc_inpatient_psych': 28,
    'loc_hospital_inpatient': 25,
    'loc_rtc': 22,
    'loc_residential_detox': 20,
    'loc_residential': 17,
    'loc_detox_only': 14,

    # --- Clinical Complexity (0-30) ---
    'mat_none': 0,
    'mat_basic': 6,       # 1-2 meds
    'mat_standard': 10,   # 3-4 meds
    'mat_comprehensive': 14,  # 5+ meds
    'antipsych_none': 0,
    'antipsych_basic': 3,    # 1-4 meds
    'antipsych_moderate': 5, # 5-9 meds
    'antipsych_broad': 7,    # 10+ meds
    'advanced_therapy_each': 2,  # per modality, cap 6
    'med_management': 3,     # MMD

    # --- Sentinel Event Risk (0-25) ---
    'risk_cooccurring': 6,
    'risk_smi': 5,
    'risk_suicide_prevention': 5,
    'risk_detox': 4,
    'risk_crisis': 3,
    'risk_first_episode': 2,

    # --- Institutional Quality (0-15) ---
    'quality_jc': 5,
    'quality_carf': 3,
    'quality_noe': 2,
    'quality_discharge': 2,
    'quality_followup': 1,
    'quality_monitoring': 2,  # MST or IPC or LABT (any combo)
}

PILLAR_CAPS = {
    'loc': 30,
    'clinical': 30,
    'risk': 25,
    'quality': 15,
}


def score_level_of_care(setting, w):
    """Score based on care setting (0-30)."""
    mapping = {
        'Psychiatric Hospital': w['loc_psych_hospital'],
        'Inpatient Psych Unit': w['loc_inpatient_psych'],
        'Hospital Inpatient': w['loc_hospital_inpatient'],
        'Residential Treatment Center': w['loc_rtc'],
        'Residential + Detox': w['loc_residential_detox'],
        'Residential': w['loc_residential'],
        'Detox Only': w['loc_detox_only'],
    }
    return min(PILLAR_CAPS['loc'], mapping.get(setting, 0))


def score_clinical_complexity(codes, w):
    """Score clinical complexity (0-30): MAT tier + antipsychotic breadth + advanced therapies + MMD."""
    c = str(codes).upper()

    # MAT tier
    mat_count = count_codes(c, MAT_MEDICATIONS)
    if mat_count == 0:
        mat_score = w['mat_none']
    elif mat_count <= 2:
        mat_score = w['mat_basic']
    elif mat_count <= 4:
        mat_score = w['mat_standard']
    else:
        mat_score = w['mat_comprehensive']

    # Antipsychotic breadth
    ap_count = count_codes(c, ANTIPSYCHOTICS)
    if ap_count == 0:
        ap_score = w['antipsych_none']
    elif ap_count <= 4:
        ap_score = w['antipsych_basic']
    elif ap_count <= 9:
        ap_score = w['antipsych_moderate']
    else:
        ap_score = w['antipsych_broad']

    # Advanced therapies (2 pts each, cap 6)
    adv_count = sum(1 for t in ADVANCED_THERAPIES if (
        re.search(rf'\b{t}\b', c) if len(t) <= 3 else t in c
    ))
    adv_score = min(6, adv_count * w['advanced_therapy_each'])

    # Medication management (MMD)
    mmd_score = w['med_management'] if has_code_wb(c, 'MMD') else 0

    return min(PILLAR_CAPS['clinical'], mat_score + ap_score + adv_score + mmd_score), mat_count, ap_count, adv_count


def score_sentinel_risk(codes, setting, facility_type, antipsych_count, w):
    """Score sentinel event risk (0-25) with fuzzy inference. Returns (score, inferred_penalty)."""
    c = str(codes).upper()
    score = 0
    inferred_penalty = 0

    # Co-occurring
    if has_code(c, ACUITY_CODES['COOCCURRING']):
        score += w['risk_cooccurring']

    # SMI (with inference)
    has_smi, smi_src, smi_pen = infer_smi(c, setting, facility_type, antipsych_count)
    if has_smi:
        score += w['risk_smi']
        inferred_penalty += smi_pen

    # Suicide prevention (with inference)
    has_sps, sps_src, sps_pen = infer_suicide_prevention(c, setting, has_smi)
    if has_sps:
        score += w['risk_suicide_prevention']
        inferred_penalty += sps_pen

    # Detox
    if has_code(c, ACUITY_CODES['DETOX']):
        score += w['risk_detox']

    # Crisis (with inference)
    has_crisis, crisis_src, crisis_pen = infer_crisis(c, setting)
    if has_crisis:
        score += w['risk_crisis']
        inferred_penalty += crisis_pen

    # First-episode psychosis
    if has_code(c, ACUITY_CODES['FIRST_EPISODE_PSYCHOSIS']):
        score += w['risk_first_episode']

    capped = min(PILLAR_CAPS['risk'], score)
    return capped, inferred_penalty, has_smi, smi_src, has_sps, sps_src, has_crisis, crisis_src


def score_institutional_quality(codes, w):
    """Score institutional quality signals (0-15)."""
    c = str(codes).upper()
    score = 0

    if has_code_wb(c, 'JC'):
        score += w['quality_jc']
    if has_code(c, QUALITY_CODES['CARF']):
        score += w['quality_carf']
    if has_code_wb(c, 'NOE'):
        score += w['quality_noe']
    if has_code_wb(c, 'DP'):
        score += w['quality_discharge']
    if has_code_wb(c, 'OFD'):
        score += w['quality_followup']

    # Medical monitoring: any of MST, IPC, LABT
    has_monitoring = (
        has_code_wb(c, 'MST') or
        has_code_wb(c, 'IPC') or
        has_code(c, QUALITY_CODES['LAB_TESTING'])
    )
    if has_monitoring:
        score += w['quality_monitoring']

    return min(PILLAR_CAPS['quality'], score)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. GAP ANALYSIS & CONFIDENCE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def calculate_gaps(row):
    """Identify what's missing relative to a max-score profile."""
    gaps = []

    # Clinical gaps
    if row['mat_count'] == 0:
        gaps.append('MAT')
    elif row['mat_count'] < 3:
        gaps.append('MAT↑')
    if row['antipsych_count'] == 0 and row['setting_type'] not in ('Residential', 'Detox Only'):
        gaps.append('AP')
    if row['adv_therapy_count'] == 0:
        gaps.append('Adv Tx')

    # Risk gaps
    if not row['has_cooccurring']:
        gaps.append('COD')
    if not row['has_smi']:
        gaps.append('SMI')
    if not row['has_sps']:
        gaps.append('SPS')
    if not row['has_detox']:
        gaps.append('DTX')
    if not row['has_crisis']:
        gaps.append('Crisis')

    # Quality gaps
    if not row['has_jc'] and not row['has_carf']:
        gaps.append('Accred')

    return ' · '.join(gaps) if gaps else '—'


def confidence_score(inferred_penalty):
    """Convert inference penalty to a 0-100 confidence score."""
    # Max realistic penalty ~12 (SMI 4 + Crisis 4 + SPS 3 + 1)
    # Scale so 0 penalty = 100%, 12+ penalty = ~55%
    return max(55, int(100 - (inferred_penalty * 3.75)))


def confidence_label(conf):
    if conf >= 90:
        return f'✓ {conf}%'
    elif conf >= 75:
        return f'~ {conf}%'
    else:
        return f'⚠ {conf}%'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. DATA LOADING (cached) & SCORING (live)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@st.cache_data(ttl=3600)
def load_data():
    """Load SAMHSA data from Google Sheets, dedup, and extract features.
    Scoring is done separately so weight changes don't require re-fetching."""

    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)

    try:
        sheet = client.open("SAMHSA_Master_Data").sheet1
        raw = pd.DataFrame(sheet.get_all_records())
        raw['orig_row'] = raw.index + 2
        total_raw = len(raw)

        # Clean
        raw['city_clean'] = raw['city'].fillna('').astype(str).str.title()
        raw['state_clean'] = raw['state'].fillna('').astype(str).str.upper()

        # Dedup by name + city + state, MERGE service codes
        rollup = raw.groupby(['name1', 'city_clean', 'state_clean'], as_index=False).agg({
            'service_code_info': merge_tags,
            'phone': 'first',
            'street1': 'first',
            'zip': 'first',
            'orig_row': lambda x: ", ".join(x.astype(str)),
            'Facility_Type': lambda x: ' & '.join(sorted(set(x.dropna().astype(str)))),
        })

        # Classify AFTER merging
        rollup['setting_type'] = rollup['service_code_info'].apply(classify_setting)

        # Flags (weight-independent)
        rollup['is_govt'] = rollup['service_code_info'].apply(lambda x: has_code(x, GOVT_CODES))
        rollup['is_for_profit'] = rollup['service_code_info'].apply(lambda x: has_code(x, OWNERSHIP_CODES['FOR_PROFIT']))
        rollup['is_non_profit'] = rollup['service_code_info'].apply(lambda x: has_code(x, OWNERSHIP_CODES['NON_PROFIT']))
        rollup['has_cooccurring'] = rollup['service_code_info'].apply(lambda x: has_code(x, ACUITY_CODES['COOCCURRING']))
        rollup['has_detox'] = rollup['service_code_info'].apply(lambda x: has_code(x, ACUITY_CODES['DETOX']))
        rollup['has_jc'] = rollup['service_code_info'].apply(lambda x: has_code_wb(x, 'JC'))
        rollup['has_carf'] = rollup['service_code_info'].apply(lambda x: has_code(x, QUALITY_CODES['CARF']))
        rollup['has_accreditation'] = rollup['has_jc'] | rollup['has_carf']

        # Raw feature counts (weight-independent)
        rollup['mat_count'] = rollup['service_code_info'].apply(lambda x: count_codes(x, MAT_MEDICATIONS))
        rollup['has_mat'] = rollup['mat_count'] > 0
        rollup['antipsych_count'] = rollup['service_code_info'].apply(lambda x: count_codes(x, ANTIPSYCHOTICS))
        rollup['adv_therapy_count'] = rollup['service_code_info'].apply(
            lambda c: sum(1 for t in ADVANCED_THERAPIES if (
                re.search(rf'\b{t}\b', str(c).upper()) if len(t) <= 3 else t in str(c).upper()
            ))
        )
        rollup['has_mmd'] = rollup['service_code_info'].apply(lambda x: has_code_wb(x, 'MMD'))
        rollup['has_pefp'] = rollup['service_code_info'].apply(lambda x: has_code(x, ACUITY_CODES['FIRST_EPISODE_PSYCHOSIS']))

        # Location
        rollup['Location'] = rollup.apply(
            lambda x: f"{x['city_clean']}, {x['state_clean']}" if x['city_clean'] else x['state_clean'],
            axis=1
        )

        return rollup, total_raw

    except Exception as e:
        st.error(f"❌ Connection Failed: {e}")
        st.stop()


def score_data(df, w):
    """Apply scoring using current weights. Called on every rerun (fast — no I/O)."""
    d = df.copy()

    # Pillar 1: Level of Care
    d['loc_score'] = d['setting_type'].apply(lambda s: score_level_of_care(s, w))

    # Pillar 2: Clinical Complexity
    def calc_clinical(row):
        c = str(row['service_code_info']).upper()
        mat_n = row['mat_count']
        mat_s = w['mat_none'] if mat_n == 0 else (w['mat_basic'] if mat_n <= 2 else (w['mat_standard'] if mat_n <= 4 else w['mat_comprehensive']))
        ap_n = row['antipsych_count']
        ap_s = w['antipsych_none'] if ap_n == 0 else (w['antipsych_basic'] if ap_n <= 4 else (w['antipsych_moderate'] if ap_n <= 9 else w['antipsych_broad']))
        adv_s = min(6, row['adv_therapy_count'] * w['advanced_therapy_each'])
        mmd_s = w['med_management'] if row['has_mmd'] else 0
        return min(PILLAR_CAPS['clinical'], mat_s + ap_s + adv_s + mmd_s)

    d['clinical_score'] = d.apply(calc_clinical, axis=1)

    # Pillar 3: Sentinel Event Risk (with fuzzy inference)
    def calc_risk(row):
        c = str(row['service_code_info']).upper()
        setting = row['setting_type']
        score = 0
        inferred_penalty = 0

        if row['has_cooccurring']:
            score += w['risk_cooccurring']

        has_smi, smi_src, smi_pen = infer_smi(c, setting, row['Facility_Type'], row['antipsych_count'])
        if has_smi:
            score += w['risk_smi']
            inferred_penalty += smi_pen

        has_sps, sps_src, sps_pen = infer_suicide_prevention(c, setting, has_smi)
        if has_sps:
            score += w['risk_suicide_prevention']
            inferred_penalty += sps_pen

        if row['has_detox']:
            score += w['risk_detox']

        has_crisis, crisis_src, crisis_pen = infer_crisis(c, setting)
        if has_crisis:
            score += w['risk_crisis']
            inferred_penalty += crisis_pen

        if row['has_pefp']:
            score += w['risk_first_episode']

        capped = min(PILLAR_CAPS['risk'], score)
        return pd.Series({
            'risk_score': capped, 'inferred_penalty': inferred_penalty,
            'has_smi': has_smi, 'smi_source': smi_src,
            'has_sps': has_sps, 'sps_source': sps_src,
            'has_crisis': has_crisis, 'crisis_source': crisis_src,
        })

    risk_results = d.apply(calc_risk, axis=1)
    for col in risk_results.columns:
        d[col] = risk_results[col]

    # Pillar 4: Institutional Quality
    def calc_quality(row):
        c = str(row['service_code_info']).upper()
        score = 0
        if row['has_jc']: score += w['quality_jc']
        if row['has_carf']: score += w['quality_carf']
        if has_code_wb(c, 'NOE'): score += w['quality_noe']
        if has_code_wb(c, 'DP'): score += w['quality_discharge']
        if has_code_wb(c, 'OFD'): score += w['quality_followup']
        if has_code_wb(c, 'MST') or has_code_wb(c, 'IPC') or 'LABT' in c:
            score += w['quality_monitoring']
        return min(PILLAR_CAPS['quality'], score)

    d['quality_score'] = d.apply(calc_quality, axis=1)

    # Composite
    d['score'] = (d['loc_score'] + d['clinical_score'] + d['risk_score'] + d['quality_score']).clip(upper=100).astype(int)

    # Confidence
    d['data_confidence'] = d['inferred_penalty'].apply(confidence_score)

    return d


# --- Load (cached) then Score (live) ---
raw_data, total_raw = load_data()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 10. SIDEBAR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

st.sidebar.title("🎯 Target Filters")

# --- Care Settings ---
with st.sidebar.expander("🏥 Care Settings", expanded=True):
    inc_psych = st.checkbox("Psychiatric Hospitals", value=True)
    inc_ipsy = st.checkbox("Inpatient Psych Units", value=True)
    inc_hosp = st.checkbox("Hospital Inpatient", value=True)
    inc_rtc = st.checkbox("Residential Treatment Centers", value=True)
    inc_res = st.checkbox("Residential (24hr)", value=True)
    inc_detox = st.checkbox("Detox Settings", value=True)

st.sidebar.divider()

# --- Score Thresholds ---
with st.sidebar.expander("🎚️ Minimum Scores", expanded=False):
    min_total = st.slider("Total Score", 0, 100, 67, 1)
    st.caption("Pillar minimums (0 = no filter, 10 = require max)")
    min_loc_pct = st.slider("Level of Care", 0, 10, 5, 1)
    min_clinical_pct = st.slider("Clinical Complexity", 0, 10, 5, 1)
    min_risk_pct = st.slider("Sentinel Event Risk", 0, 10, 5, 1)
    min_quality_pct = st.slider("Institutional Quality", 0, 10, 5, 1)

    # Convert 0-10 scale to actual pillar thresholds
    min_loc = int(min_loc_pct * PILLAR_CAPS['loc'] / 10)
    min_clinical = int(min_clinical_pct * PILLAR_CAPS['clinical'] / 10)
    min_risk = int(min_risk_pct * PILLAR_CAPS['risk'] / 10)
    min_quality = int(min_quality_pct * PILLAR_CAPS['quality'] / 10)

st.sidebar.divider()

# --- Treatment Filters ---
with st.sidebar.expander("💊 Other Treatments", expanded=False):
    require_cod = st.checkbox("Co-Occurring Disorders (COD)", value=False)
    mat_filter = st.selectbox(
        "Medication-Assisted Therapy (MAT)",
        ["Any / No Filter", "Has MAT (1+ meds)", "Standard MAT (3+ meds)", "Comprehensive MAT (5+ meds)"],
        index=0,
    )
    require_smi = st.checkbox("Severe Mental Illness (SMI)", value=False)

st.sidebar.divider()

# --- Quality Filters ---
with st.sidebar.expander("🏅 Institutional Quality", expanded=False):
    require_accred = st.checkbox("Require Accreditation (JC or CARF)", value=False)

st.sidebar.divider()

# --- Toggles ---
exclude_govt = st.sidebar.toggle("Exclude Government Facilities", value=True)

st.sidebar.divider()
st.sidebar.number_input("Display Row Count", key="max_show", min_value=1, step=1)

# --- Adjustable Scoring Weights (collapsed) ---
with st.sidebar.expander("⚙️ Scoring Weights", expanded=False):
    st.caption("Adjust how much each signal contributes to the total score. Changes take effect immediately.")

    st.markdown("**Level of Care** (max 30)")
    w_loc_psych = st.number_input("Psych Hospital", 0, 30, DEFAULT_WEIGHTS['loc_psych_hospital'], 1, key="w_lp")
    w_loc_ipsy = st.number_input("Inpatient Psych Unit", 0, 30, DEFAULT_WEIGHTS['loc_inpatient_psych'], 1, key="w_li")
    w_loc_hosp = st.number_input("Hospital Inpatient", 0, 30, DEFAULT_WEIGHTS['loc_hospital_inpatient'], 1, key="w_lh")
    w_loc_rtc = st.number_input("Residential Tx Center", 0, 30, DEFAULT_WEIGHTS['loc_rtc'], 1, key="w_lr")
    w_loc_resdtx = st.number_input("Residential + Detox", 0, 30, DEFAULT_WEIGHTS['loc_residential_detox'], 1, key="w_lrd")
    w_loc_res = st.number_input("Residential", 0, 30, DEFAULT_WEIGHTS['loc_residential'], 1, key="w_lre")
    w_loc_dtx = st.number_input("Detox Only", 0, 30, DEFAULT_WEIGHTS['loc_detox_only'], 1, key="w_ld")

    st.markdown("**Clinical Complexity** (max 30)")
    w_mat_b = st.number_input("MAT Basic (1-2 meds)", 0, 30, DEFAULT_WEIGHTS['mat_basic'], 1, key="w_mb")
    w_mat_s = st.number_input("MAT Standard (3-4)", 0, 30, DEFAULT_WEIGHTS['mat_standard'], 1, key="w_ms")
    w_mat_c = st.number_input("MAT Comprehensive (5+)", 0, 30, DEFAULT_WEIGHTS['mat_comprehensive'], 1, key="w_mc")
    w_ap_b = st.number_input("Antipsych Basic (1-4)", 0, 30, DEFAULT_WEIGHTS['antipsych_basic'], 1, key="w_ab")
    w_ap_m = st.number_input("Antipsych Moderate (5-9)", 0, 30, DEFAULT_WEIGHTS['antipsych_moderate'], 1, key="w_am")
    w_ap_br = st.number_input("Antipsych Broad (10+)", 0, 30, DEFAULT_WEIGHTS['antipsych_broad'], 1, key="w_abr")
    w_adv = st.number_input("Adv Therapy (each, cap 6)", 0, 6, DEFAULT_WEIGHTS['advanced_therapy_each'], 1, key="w_adv")
    w_mmd = st.number_input("Med Management (MMD)", 0, 10, DEFAULT_WEIGHTS['med_management'], 1, key="w_mmd")

    st.markdown("**Sentinel Event Risk** (max 25)")
    w_cod = st.number_input("Co-Occurring (COD)", 0, 25, DEFAULT_WEIGHTS['risk_cooccurring'], 1, key="w_cod")
    w_smi = st.number_input("SMI", 0, 25, DEFAULT_WEIGHTS['risk_smi'], 1, key="w_smi")
    w_sps = st.number_input("Suicide Prevention", 0, 25, DEFAULT_WEIGHTS['risk_suicide_prevention'], 1, key="w_sps")
    w_dtx = st.number_input("Detox", 0, 25, DEFAULT_WEIGHTS['risk_detox'], 1, key="w_dtx")
    w_cri = st.number_input("Crisis Services", 0, 25, DEFAULT_WEIGHTS['risk_crisis'], 1, key="w_cri")
    w_pefp = st.number_input("First-Episode Psychosis", 0, 25, DEFAULT_WEIGHTS['risk_first_episode'], 1, key="w_pefp")

    st.markdown("**Institutional Quality** (max 15)")
    w_jc = st.number_input("Joint Commission", 0, 15, DEFAULT_WEIGHTS['quality_jc'], 1, key="w_jc")
    w_carf = st.number_input("CARF", 0, 15, DEFAULT_WEIGHTS['quality_carf'], 1, key="w_carf")
    w_noe = st.number_input("Naloxone/OD Ed", 0, 15, DEFAULT_WEIGHTS['quality_noe'], 1, key="w_noe")
    w_dp = st.number_input("Discharge Planning", 0, 15, DEFAULT_WEIGHTS['quality_discharge'], 1, key="w_dp")
    w_mon = st.number_input("Medical Monitoring", 0, 15, DEFAULT_WEIGHTS['quality_monitoring'], 1, key="w_mon")
    w_ofd = st.number_input("Outcome Follow-up", 0, 15, DEFAULT_WEIGHTS['quality_followup'], 1, key="w_ofd")

# --- Build active weights dict from sidebar inputs ---
active_weights = {
    'loc_psych_hospital': w_loc_psych,
    'loc_inpatient_psych': w_loc_ipsy,
    'loc_hospital_inpatient': w_loc_hosp,
    'loc_rtc': w_loc_rtc,
    'loc_residential_detox': w_loc_resdtx,
    'loc_residential': w_loc_res,
    'loc_detox_only': w_loc_dtx,
    'mat_none': 0,
    'mat_basic': w_mat_b,
    'mat_standard': w_mat_s,
    'mat_comprehensive': w_mat_c,
    'antipsych_none': 0,
    'antipsych_basic': w_ap_b,
    'antipsych_moderate': w_ap_m,
    'antipsych_broad': w_ap_br,
    'advanced_therapy_each': w_adv,
    'med_management': w_mmd,
    'risk_cooccurring': w_cod,
    'risk_smi': w_smi,
    'risk_suicide_prevention': w_sps,
    'risk_detox': w_dtx,
    'risk_crisis': w_cri,
    'risk_first_episode': w_pefp,
    'quality_jc': w_jc,
    'quality_carf': w_carf,
    'quality_noe': w_noe,
    'quality_discharge': w_dp,
    'quality_monitoring': w_mon,
    'quality_followup': w_ofd,
}

# --- Score data with active weights ---
d = score_data(raw_data, active_weights)
count_unique = len(d)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 11. FILTERING ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

d_work = d.copy()

# --- Setting filter (OR logic) ---
allowed = set()
if inc_psych:  allowed.add('Psychiatric Hospital')
if inc_ipsy:   allowed.add('Inpatient Psych Unit')
if inc_hosp:   allowed.add('Hospital Inpatient')
if inc_rtc:    allowed.add('Residential Treatment Center')
if inc_res:
    allowed.add('Residential')
    allowed.add('Residential + Detox')
if inc_detox:
    allowed.add('Detox Only')
    allowed.add('Residential + Detox')

if allowed:
    d_work = d_work[d_work['setting_type'].isin(allowed)]
else:
    d_work = d_work[d_work['setting_type'] != 'Outpatient']

count_setting = len(d_work)

# --- Score thresholds ---
d_work = d_work[
    (d_work['score'] >= min_total) &
    (d_work['loc_score'] >= min_loc) &
    (d_work['clinical_score'] >= min_clinical) &
    (d_work['risk_score'] >= min_risk) &
    (d_work['quality_score'] >= min_quality)
]
count_scored = len(d_work)

# --- Capability filters ---
if require_cod:
    d_work = d_work[d_work['has_cooccurring']]
if mat_filter == "Has MAT (1+ meds)":
    d_work = d_work[d_work['mat_count'] >= 1]
elif mat_filter == "Standard MAT (3+ meds)":
    d_work = d_work[d_work['mat_count'] >= 3]
elif mat_filter == "Comprehensive MAT (5+ meds)":
    d_work = d_work[d_work['mat_count'] >= 5]
if require_smi:
    d_work = d_work[d_work['has_smi']]
if require_accred:
    d_work = d_work[d_work['has_accreditation']]

# --- Government exclusion ---
if exclude_govt:
    d_work = d_work[~d_work['is_govt']]

count_final = len(d_work)

# --- Sort ---
d_final = d_work.sort_values(['score', 'name1'], ascending=[False, True]).copy()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 13. MAIN VIEW
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

st.title("🎯 Rounding Solution — Target Facilities")

st.markdown(
    '<span style="font-size:0.9rem; color:#808495;">Ranked 0–100 across four pillars: '
    'care setting · clinical complexity · sentinel event risk · institutional quality</span>',
    unsafe_allow_html=True,
)

# --- Funnel Metrics (tighter spacing, ties+button on same row) ---
c1, c2, c3, c4, c5, c6, c7 = st.columns([1, 1, 1, 1, 1, 0.6, 0.8])
c1.metric("Raw Records", f"{total_raw:,}", help="Total rows in SAMHSA dataset (SUD + MH)")
c2.metric("Locations", f"{count_unique:,}", delta=-(total_raw - count_unique), delta_color="normal",
          help="Deduplicated by facility name + city + state. SUD & MH entries at the same location are merged.")
c3.metric("Setting Fit", f"{count_setting:,}", delta=-(count_unique - count_setting), delta_color="normal",
          help="Filtered to selected care settings (sidebar). Outpatient-only facilities excluded.")
c4.metric("Score Fit", f"{count_scored:,}", delta=-(count_setting - count_scored), delta_color="normal",
          help="Filtered by minimum score thresholds (Total score + per-pillar 0–10 selectivity sliders).")
c5.metric("Qualified", f"{count_final:,}", delta=-(count_scored - count_final), delta_color="normal",
          help="After applying treatment requirements (COD, MAT, SMI), accreditation filter, and government exclusion.")

st.markdown('<hr style="margin:0.2rem 0;">', unsafe_allow_html=True)

# --- Search / State filter ---
# State options drawn from the FILTERED set so users only see states that have results
c_search, c_state = st.columns([2, 1])
search = c_search.text_input("🔍 Search Facility Name").lower()
available_states = sorted(d_final['state_clean'].unique())
states = c_state.multiselect("📍 State Filter", options=available_states)

# Apply search/state to the full qualified set BEFORE display limit
if search:
    d_final = d_final[d_final['name1'].str.lower().str.contains(search, na=False)]
if states:
    d_final = d_final[d_final['state_clean'].isin(states)]

count_filtered = len(d_final)

# Show filtered count when search/state narrows results
if search or states:
    c6.metric("Filtered", f"{count_filtered:,}", help="Narrowed by facility name search and/or state filter.")

# --- Apply display limit AFTER search/state ---
current_limit = int(st.session_state.max_show)
count_ties = 0

if count_filtered > current_limit:
    cutoff = d_final.iloc[current_limit - 1]['score']
    count_ties = len(d_final.iloc[current_limit:][d_final.iloc[current_limit:]['score'] == cutoff])
    display_df = d_final.head(current_limit).copy()
else:
    display_df = d_final.copy()

# Ties — count in c6, button in c7 (same row)
if count_ties > 0:
    if not (search or states):
        c6.metric("Ties", f"{count_ties:,}", help=f"Facilities just outside the display limit sharing the cutoff score of {int(cutoff)}.")
    with c7:
        if st.button("➕ Include Ties"):
            st.session_state.pending_tie_add = count_ties
            st.rerun()

# --- Gap analysis ---
display_df = display_df.copy()
display_df['Gaps'] = display_df.apply(calculate_gaps, axis=1)
display_df['Confidence'] = display_df['data_confidence'].apply(confidence_label)

# --- Pillar breakdown for display ---
display_df['Pillars'] = display_df.apply(
    lambda r: f"{r['loc_score']}·{r['clinical_score']}·{r['risk_score']}·{r['quality_score']}", axis=1
)

# --- Rank ---
display_df = display_df.reset_index(drop=True)
display_df.insert(0, 'Rank', display_df.index + 1)

# --- Table ---
st.dataframe(
    display_df[[
        'Rank', 'name1', 'Location', 'setting_type',
        'score', 'Pillars', 'Gaps', 'Confidence',
    ]].rename(columns={
        'name1': 'Facility Name',
        'setting_type': 'Setting',
        'score': 'Score',
        'Pillars': 'L·C·R·Q',
    }),
    use_container_width=True,
    height=420,
    hide_index=True,
    column_config={
        "Rank": st.column_config.NumberColumn("Rank", width=55),
        "Facility Name": st.column_config.TextColumn("Facility Name", width=260),
        "Location": st.column_config.TextColumn("Location", width=170),
        "Setting": st.column_config.TextColumn("Setting", width=170),
        "Score": st.column_config.ProgressColumn("Score", format="%d", min_value=0, max_value=100, width=75),
        "L·C·R·Q": st.column_config.TextColumn("L·C·R·Q", width=95,
            help="Level of Care · Clinical Complexity · Sentinel Risk · Institutional Quality"),
        "Gaps": st.column_config.TextColumn("Missing", width=190),
        "Confidence": st.column_config.TextColumn("Data", width=70),
    }
)

# --- Compact legend ---
st.markdown(
    '<div class="legend-text">'
    '<b>L·C·R·Q:</b> Level of Care (30) · Clinical (30) · Risk (25) · Quality (15) &nbsp;|&nbsp; '
    '<b>Missing:</b> MAT · MAT↑ limited · AP antipsych · Adv Tx · COD · SMI · SPS · DTX · Crisis · Accred &nbsp;|&nbsp; '
    '<b>Data:</b> ✓ &ge;90% · ~ 75-89% · ⚠ &lt;75%'
    '</div>',
    unsafe_allow_html=True,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 14. EXPANDERS: SCORING MODEL, SUMMARY STATS, DOWNLOAD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

with st.expander("ℹ️ Scoring Model"):
    st.markdown("""
**Four-pillar model (0–100 total)** — weights adjustable in sidebar ⚙️

| Pillar | Max | Key Signals |
|--------|-----|-------------|
| **Level of Care** | 30 | Psych Hospital → Inpatient Psych → Hospital → RTC → Res+Detox → Res → Detox |
| **Clinical Complexity** | 30 | MAT tier + Antipsychotic breadth + Advanced therapies (DBT, EMDR, ECT, TMS, KIT) + MMD |
| **Sentinel Event Risk** | 25 | Co-occurring + SMI + Suicide Prevention + Detox + Crisis + First-Episode Psychosis |
| **Institutional Quality** | 15 | Joint Commission + CARF + Naloxone/OD Ed + Discharge Planning + Outcome Follow-up + Medical Monitoring |

**Fuzzy Inference** (when SAMHSA data is incomplete):
- Psych hospitals → assumed SMI, Crisis, Suicide Prevention
- Inpatient psych units → assumed SMI, Crisis
- Hospital + co-occurring → assumed SMI
- 5+ antipsychotics prescribed → assumed SMI
- Hospital + detox → assumed Crisis
- Confidence score penalized proportionally to inference used
    """)

# --- Summary Statistics ---
# Uses d_final (full filtered set, NOT truncated) for accurate stats
with st.expander(f"📊 Summary Statistics — {count_filtered:,} filtered facilities"):
    if count_filtered == 0:
        st.info("No facilities match current filters.")
    else:
        stats_df = d_final  # full filtered set, pre-truncation

        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown("**Setting Distribution**")
            st.dataframe(
                stats_df['setting_type'].value_counts().reset_index()
                .rename(columns={'setting_type': 'Setting', 'count': 'Count'}),
                hide_index=True, use_container_width=True,
            )

        with col2:
            st.markdown("**Top 10 States**")
            st.dataframe(
                stats_df['state_clean'].value_counts().head(10).reset_index()
                .rename(columns={'state_clean': 'State', 'count': 'Count'}),
                hide_index=True, use_container_width=True,
            )

        with col3:
            st.markdown("**Score Breakdown**")
            st.metric("Mean Score", f"{stats_df['score'].mean():.1f}")
            st.metric("Median Score", f"{stats_df['score'].median():.0f}")
            st.metric("Range", f"{stats_df['score'].min():.0f} – {stats_df['score'].max():.0f}")

        # Pillar averages
        st.markdown("**Pillar Averages**")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Level of Care", f"{stats_df['loc_score'].mean():.1f} / 30")
        p2.metric("Clinical", f"{stats_df['clinical_score'].mean():.1f} / 30")
        p3.metric("Risk", f"{stats_df['risk_score'].mean():.1f} / 25")
        p4.metric("Quality", f"{stats_df['quality_score'].mean():.1f} / 15")

        # Gap frequency — compute on full set
        stats_df_gaps = stats_df.copy()
        stats_df_gaps['Gaps'] = stats_df_gaps.apply(calculate_gaps, axis=1)
        st.markdown("**Most Common Gaps**")
        all_gaps = []
        for g in stats_df_gaps['Gaps']:
            if g != '—':
                all_gaps.extend(g.split(' · '))
        if all_gaps:
            st.dataframe(
                pd.Series(all_gaps).value_counts().head(8).reset_index()
                .rename(columns={'index': 'Gap', 0: 'Count'}),
                hide_index=True, use_container_width=True,
            )

        # Data quality
        st.markdown("**Data Quality**")
        q1, q2, q3 = st.columns(3)
        q1.metric("High Confidence (✓)", f"{(stats_df['data_confidence'] >= 90).sum()}")
        q2.metric("Medium (~)", f"{((stats_df['data_confidence'] >= 75) & (stats_df['data_confidence'] < 90)).sum()}")
        q3.metric("Needs Verification (⚠)", f"{(stats_df['data_confidence'] < 75).sum()}")

# --- Download (bottom of page) ---
showing_label = f"top {len(display_df)}" if len(display_df) < count_filtered else "all"
st.download_button(
    f"📥 Download Current View — {len(display_df):,} facilities ({showing_label})",
    display_df.to_csv(index=False).encode('utf-8'),
    "rounding_targets.csv",
    mime="text/csv",
)
