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
.legend-text {font-size: 0.78rem; color: #808495; line-height: 1.4; margin-top: -0.3rem;}
.legend-text b {color: #b0b4c0;}
</style>
""", unsafe_allow_html=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. SESSION STATE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if 'max_show' not in st.session_state:
    st.session_state.max_show = 100
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
# 9. DATA LOADING & SCORING PIPELINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@st.cache_data(ttl=3600)
def load_and_score():
    """Load SAMHSA data from Google Sheets, dedup, and score all facilities."""

    # --- Connect ---
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    client = gspread.authorize(creds)

    try:
        sheet = client.open("SAMHSA_Master_Data").sheet1
        raw = pd.DataFrame(sheet.get_all_records())
        raw['orig_row'] = raw.index + 2
        total_raw = len(raw)

        # --- Clean ---
        raw['city_clean'] = raw['city'].fillna('').astype(str).str.title()
        raw['state_clean'] = raw['state'].fillna('').astype(str).str.upper()

        # --- Dedup by name + city + state, MERGE service codes first ---
        rollup = raw.groupby(['name1', 'city_clean', 'state_clean'], as_index=False).agg({
            'service_code_info': merge_tags,
            'phone': 'first',
            'street1': 'first',
            'zip': 'first',
            'orig_row': lambda x: ", ".join(x.astype(str)),
            'Facility_Type': lambda x: ' & '.join(sorted(set(x.dropna().astype(str)))),
        })

        # --- Classify AFTER merging (merged codes may upgrade a setting) ---
        rollup['setting_type'] = rollup['service_code_info'].apply(classify_setting)

        # --- Government flag ---
        rollup['is_govt'] = rollup['service_code_info'].apply(lambda x: has_code(x, GOVT_CODES))

        # --- Ownership ---
        rollup['is_for_profit'] = rollup['service_code_info'].apply(lambda x: has_code(x, OWNERSHIP_CODES['FOR_PROFIT']))
        rollup['is_non_profit'] = rollup['service_code_info'].apply(lambda x: has_code(x, OWNERSHIP_CODES['NON_PROFIT']))

        # --- Score all four pillars ---
        w = DEFAULT_WEIGHTS

        # Pillar 1: Level of Care
        rollup['loc_score'] = rollup['setting_type'].apply(lambda s: score_level_of_care(s, w))

        # Pillar 2: Clinical Complexity
        clinical_results = rollup['service_code_info'].apply(lambda c: score_clinical_complexity(c, w))
        rollup['clinical_score'] = clinical_results.apply(lambda x: x[0])
        rollup['mat_count'] = clinical_results.apply(lambda x: x[1])
        rollup['antipsych_count'] = clinical_results.apply(lambda x: x[2])
        rollup['adv_therapy_count'] = clinical_results.apply(lambda x: x[3])

        # Pillar 3: Sentinel Event Risk (with fuzzy inference)
        risk_results = rollup.apply(
            lambda row: score_sentinel_risk(
                row['service_code_info'], row['setting_type'],
                row['Facility_Type'], row['antipsych_count'], w
            ), axis=1, result_type='expand'
        )
        rollup['risk_score'] = risk_results[0]
        rollup['inferred_penalty'] = risk_results[1]
        rollup['has_smi'] = risk_results[2]
        rollup['smi_source'] = risk_results[3]
        rollup['has_sps'] = risk_results[4]
        rollup['sps_source'] = risk_results[5]
        rollup['has_crisis'] = risk_results[6]
        rollup['crisis_source'] = risk_results[7]

        # Pillar 4: Institutional Quality
        rollup['quality_score'] = rollup['service_code_info'].apply(lambda c: score_institutional_quality(c, w))

        # --- Composite ---
        rollup['score'] = (
            rollup['loc_score'] +
            rollup['clinical_score'] +
            rollup['risk_score'] +
            rollup['quality_score']
        ).clip(upper=100).astype(int)

        # --- Confidence ---
        rollup['data_confidence'] = rollup['inferred_penalty'].apply(confidence_score)

        # --- Derived flags for filtering ---
        rollup['has_cooccurring'] = rollup['service_code_info'].apply(lambda x: has_code(x, ACUITY_CODES['COOCCURRING']))
        rollup['has_mat'] = rollup['mat_count'] > 0
        rollup['has_detox'] = rollup['service_code_info'].apply(lambda x: has_code(x, ACUITY_CODES['DETOX']))
        rollup['has_jc'] = rollup['service_code_info'].apply(lambda x: has_code_wb(x, 'JC'))
        rollup['has_carf'] = rollup['service_code_info'].apply(lambda x: has_code(x, QUALITY_CODES['CARF']))
        rollup['has_accreditation'] = rollup['has_jc'] | rollup['has_carf']

        # --- Location ---
        rollup['Location'] = rollup.apply(
            lambda x: f"{x['city_clean']}, {x['state_clean']}" if x['city_clean'] else x['state_clean'],
            axis=1
        )

        return rollup, total_raw

    except Exception as e:
        st.error(f"❌ Connection Failed: {e}")
        st.stop()


# --- Load ---
d, total_raw = load_and_score()
count_unique = len(d)


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
    inc_detox = st.checkbox("Detox Settings", value=False)

st.sidebar.divider()

# --- Score Thresholds ---
with st.sidebar.expander("🎚️ Minimum Scores", expanded=False):
    min_total = st.slider("Total Score", 0, 100, 40, 5)
    min_loc = st.slider("Level of Care", 0, 30, 0, 5)
    min_clinical = st.slider("Clinical Complexity", 0, 30, 0, 5)
    min_risk = st.slider("Sentinel Event Risk", 0, 25, 0, 5)
    min_quality = st.slider("Institutional Quality", 0, 15, 0, 1)

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

# --- Advanced Weights (collapsed) ---
with st.sidebar.expander("⚙️ Advanced — Adjust Scoring Weights", expanded=False):
    st.caption("Weights are loaded from defaults. Changes require app refresh to recalculate scores (cache).")
    st.markdown("See ℹ️ Scoring Model below the table for weight details.")


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

count_capability = len(d_work)

# --- Government exclusion ---
if exclude_govt:
    d_work = d_work[~d_work['is_govt']]

count_final = len(d_work)

# --- Sort ---
d_final = d_work.sort_values(['score', 'name1'], ascending=[False, True]).copy()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 12. TIE DETECTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

current_limit = int(st.session_state.max_show)
count_ties = 0

if count_final > current_limit:
    cutoff = d_final.iloc[current_limit - 1]['score']
    count_ties = len(d_final.iloc[current_limit:][d_final.iloc[current_limit:]['score'] == cutoff])
    display_df = d_final.head(current_limit).copy()
else:
    display_df = d_final.copy()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 13. MAIN VIEW
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

st.title("🎯 Rounding Solution — Target Facilities")

st.markdown(
    '<span style="font-size:0.9rem; color:#808495;">Ranked 0–100 across four pillars: '
    'care setting · clinical complexity · sentinel event risk · institutional quality</span>',
    unsafe_allow_html=True,
)

# --- Funnel Metrics ---
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("1. Raw Records", f"{total_raw:,}")
c2.metric("2. Locations", f"{count_unique:,}", delta=f"−{total_raw - count_unique:,}", delta_color="off")
c3.metric("3. Setting Fit", f"{count_setting:,}", delta=f"−{count_unique - count_setting:,}", delta_color="off")
c4.metric("4. Score Fit", f"{count_scored:,}", delta=f"−{count_setting - count_scored:,}", delta_color="off")
c5.metric("5. Qualified", f"{count_final:,}", delta=f"−{count_scored - count_final:,}", delta_color="off")
if count_ties > 0:
    c6.metric("6. Ties", f"{count_ties:,}")
    if c6.button("➕ Include Ties"):
        st.session_state.pending_tie_add = count_ties
        st.rerun()

st.markdown('<hr style="margin:0.2rem 0;">', unsafe_allow_html=True)

# --- Search / State filter ---
c_search, c_state = st.columns([2, 1])
search = c_search.text_input("🔍 Search Facility Name").lower()
states = c_state.multiselect("📍 State Filter", options=sorted(d['state_clean'].unique()))

if search:
    display_df = display_df[display_df['name1'].str.lower().str.contains(search, na=False)]
if states:
    display_df = display_df[display_df['state_clean'].isin(states)]

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
**Four-pillar model (0–100 total)**

| Pillar | Max | Key Signals |
|--------|-----|-------------|
| **Level of Care** | 30 | Psych Hospital (30) → Inpatient Psych (28) → Hospital (25) → RTC (22) → Res+Detox (20) → Res (17) → Detox (14) |
| **Clinical Complexity** | 30 | MAT tier (0/6/10/14) + Antipsychotic breadth (0/3/5/7) + Advanced therapies (DBT, EMDR, ECT, TMS, KIT — 2ea, cap 6) + MMD (3) |
| **Sentinel Event Risk** | 25 | Co-occurring (6) + SMI (5) + Suicide Prevention (5) + Detox (4) + Crisis (3) + First-Episode Psychosis (2) |
| **Institutional Quality** | 15 | Joint Commission (5) + CARF (3) + Naloxone/OD Ed (2) + Discharge Planning (2) + Outcome Follow-up (1) + Medical Monitoring (2) |

**Fuzzy Inference** (when SAMHSA data is incomplete):
- Psych hospitals → assumed SMI, Crisis, Suicide Prevention capabilities
- Inpatient psych units → assumed SMI, Crisis
- Hospital + co-occurring → assumed SMI
- 5+ antipsychotics prescribed → assumed SMI
- Hospital + detox → assumed Crisis
- Confidence score penalized proportionally to inference used
    """)

# --- Download ---
st.download_button(
    "📥 Download Current View (CSV)",
    display_df.to_csv(index=False).encode('utf-8'),
    "rounding_targets.csv",
    mime="text/csv",
)

# --- Summary Statistics ---
with st.expander("📊 Summary Statistics"):
    if len(display_df) == 0:
        st.info("No facilities match current filters.")
    else:
        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown("**Setting Distribution**")
            st.dataframe(
                display_df['setting_type'].value_counts().reset_index()
                .rename(columns={'setting_type': 'Setting', 'count': 'Count'}),
                hide_index=True, use_container_width=True,
            )

        with col2:
            st.markdown("**Top 10 States**")
            st.dataframe(
                display_df['state_clean'].value_counts().head(10).reset_index()
                .rename(columns={'state_clean': 'State', 'count': 'Count'}),
                hide_index=True, use_container_width=True,
            )

        with col3:
            st.markdown("**Score Breakdown**")
            st.metric("Mean Score", f"{display_df['score'].mean():.1f}")
            st.metric("Median Score", f"{display_df['score'].median():.0f}")
            st.metric("Range", f"{display_df['score'].min():.0f} – {display_df['score'].max():.0f}")

        # Pillar averages
        st.markdown("**Pillar Averages (displayed facilities)**")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Level of Care", f"{display_df['loc_score'].mean():.1f} / 30")
        p2.metric("Clinical", f"{display_df['clinical_score'].mean():.1f} / 30")
        p3.metric("Risk", f"{display_df['risk_score'].mean():.1f} / 25")
        p4.metric("Quality", f"{display_df['quality_score'].mean():.1f} / 15")

        # Gap frequency
        st.markdown("**Most Common Gaps**")
        all_gaps = []
        for g in display_df['Gaps']:
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
        q1.metric("High Confidence (✓)", f"{(display_df['data_confidence'] >= 90).sum()}")
        q2.metric("Medium (~)", f"{((display_df['data_confidence'] >= 75) & (display_df['data_confidence'] < 90)).sum()}")
        q3.metric("Needs Verification (⚠)", f"{(display_df['data_confidence'] < 75).sum()}")
