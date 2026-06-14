"""
Deterministic language proficiency inference engine (D-04).

Infers Arabic and English proficiency from CV facts without LLM calls.

Evidence priority:
  Arabic: CV Arabic chars > 50% → 0.95  |  nationality → 0.90  |
          location → 0.88  |  country in header → 0.85  |
          Arabic university → 0.85  |  CV mixed (>15% ar) → 0.80
  English: CV <15% Arabic chars → 0.88  |  English education → 0.75  |
           English work history (≥2 entries) → 0.72

Configuration: ARABIC_COUNTRIES and ARABIC_UNIVERSITIES frozensets are the
admin-tunable knobs — add/remove entries without touching logic.

Never raises.  Returns None when no evidence found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Admin-tunable configuration ───────────────────────────────────────────────

ARABIC_COUNTRIES: frozenset[str] = frozenset({
    # Country names — English
    "palestine", "palestinian", "palestinians",
    "jordan", "jordanian",
    "lebanon", "lebanese",
    "syria", "syrian",
    "iraq", "iraqi",
    "saudi arabia", "saudi",
    "uae", "united arab emirates", "emirati",
    "qatar", "qatari",
    "kuwait", "kuwaiti",
    "bahrain", "bahraini",
    "oman", "omani",
    "yemen", "yemeni",
    "egypt", "egyptian",
    "libya", "libyan",
    "tunisia", "tunisian",
    "algeria", "algerian",
    "morocco", "moroccan",
    "sudan", "sudanese",
    # Country names — Arabic
    "فلسطين", "فلسطيني",
    "الأردن", "أردني",
    "لبنان", "لبناني",
    "سوريا", "سوري",
    "العراق", "عراقي",
    "المملكة العربية السعودية", "سعودي",
    "الإمارات", "إماراتي",
    "قطر", "قطري",
    "الكويت", "كويتي",
    "البحرين", "بحريني",
    "عُمان", "عماني",
    "اليمن", "يمني",
    "مصر", "مصري",
    "ليبيا", "ليبي",
    "تونس", "تونسي",
    "الجزائر", "جزائري",
    "المغرب", "مغربي",
    "السودان", "سوداني",
    # Well-known cities / regions inside Arab countries
    "west bank", "gaza",
    "ramallah", "nablus", "jenin", "hebron", "bethlehem",
    "jericho", "tulkarm", "qalqilya", "salfit",
    "amman", "irbid", "zarqa", "aqaba",
    "beirut", "tripoli", "sidon",
    "damascus", "aleppo", "homs",
    "baghdad", "basra", "erbil",
    "cairo", "alexandria", "giza",
    "riyadh", "jeddah", "mecca", "medina",
    "dubai", "abu dhabi", "sharjah",
    "doha", "manama", "muscat",
    "sanaa", "aden",
    "tripoli", "benghazi",
    "tunis", "sfax",
    "algiers", "oran",
    "casablanca", "rabat", "fez",
    "khartoum",
    # Arabic city names
    "الرياض", "جدة", "مكة", "المدينة",
    "دبي", "أبوظبي", "الشارقة",
    "الدوحة", "المنامة", "مسقط",
    "صنعاء", "عدن",
    "طرابلس", "بنغازي",
    "تونس", "صفاقس",
    "الجزائر", "وهران",
    "الدار البيضاء", "الرباط", "فاس",
    "الخرطوم",
    "القاهرة", "الإسكندرية", "الجيزة",
    "بغداد", "البصرة", "أربيل",
    "دمشق", "حلب", "حمص",
    "بيروت", "طرابلس",
    "عمان", "إربد", "الزرقاء",
    "رام الله", "نابلس", "جنين", "الخليل", "بيت لحم",
})

ARABIC_UNIVERSITIES: frozenset[str] = frozenset({
    # Palestinian
    "birzeit", "بيرزيت",
    "an-najah", "najah", "النجاح",
    "hebron university", "جامعة الخليل",
    "al-quds", "quds university", "القدس",
    "bethlehem university", "جامعة بيت لحم",
    "arab american university", "الجامعة العربية الأمريكية",
    "palestine polytechnic", "بوليتكنيك فلسطين",
    "al-azhar university", "الأزهر",
    "islamic university of gaza", "الجامعة الإسلامية",
    # Jordanian
    "university of jordan", "الجامعة الأردنية",
    "yarmouk", "اليرموك",
    "german jordanian", "الألمانية الأردنية",
    "jordan university of science", "جامعة العلوم والتكنولوجيا",
    "hashemite university", "الجامعة الهاشمية",
    # Lebanese
    "american university of beirut", "aub",
    "lebanese american university", "lau",
    "lebanese university", "الجامعة اللبنانية",
    "notre dame university", "جامعة سيدة اللويزة",
    # Egyptian
    "cairo university", "جامعة القاهرة",
    "ain shams", "عين شمس",
    "alexandria university", "جامعة الإسكندرية",
    "american university in cairo", "auc",
    "helwan university", "جامعة حلوان",
    # Saudi / Gulf
    "king abdulaziz", "جامعة الملك عبدالعزيز",
    "king saud", "جامعة الملك سعود",
    "king fahd", "جامعة الملك فهد",
    "king faisal", "جامعة الملك فيصل",
    "kuwait university", "جامعة الكويت",
    "uae university", "جامعة الإمارات",
    "khalifa university",
    "american university of sharjah",
    "university of sharjah", "جامعة الشارقة",
    "qatar university", "جامعة قطر",
    "university of bahrain", "جامعة البحرين",
    "sultan qaboos", "جامعة السلطان قابوس",
    # Syrian / Iraqi
    "damascus university", "جامعة دمشق",
    "aleppo university", "جامعة حلب",
    "baghdad university", "جامعة بغداد",
    "mosul university", "جامعة الموصل",
    # North African
    "tunis university", "جامعة تونس",
    "university of algiers", "جامعة الجزائر",
    "mohammed v", "جامعة محمد الخامس",
    "university of khartoum", "جامعة الخرطوم",
})

# Confidence thresholds
CONF_NATIVE_AR_TEXT:    float = 0.95   # >50% Arabic chars in CV
CONF_MIXED_AR_TEXT:     float = 0.80   # 15–50% Arabic chars
CONF_NATIONALITY:       float = 0.90
CONF_LOCATION:          float = 0.88
CONF_COUNTRY_IN_HEADER: float = 0.85
CONF_ARABIC_UNIVERSITY: float = 0.85
CONF_EN_CV_PRIMARY:     float = 0.88   # <15% Arabic chars, CV in English
CONF_EN_CV_MIXED:       float = 0.70   # 15–50% Arabic chars
CONF_EN_EDUCATION:      float = 0.75
CONF_EN_WORK_HISTORY:   float = 0.72

# ── Patterns ──────────────────────────────────────────────────────────────────

_NATL_LABEL_RE = re.compile(
    r"(?:nationality|citizenship|national|جنسية|الجنسية|مواطنة)\s*[:\-–]\s*(.{2,50}?)(?:\n|$)",
    re.IGNORECASE | re.UNICODE,
)
_LOC_LABEL_RE = re.compile(
    r"(?:location|address|city|country|residence|residing|based\s+in"
    r"|عنوان|الموقع|مقيم(?:\s+في)?|مكان\s+الإقامة|الموقع\s+الجغرافي)\s*[:\-–]?\s*(.{2,80}?)(?:\n|$)",
    re.IGNORECASE | re.UNICODE,
)

# Language criterion detection
_ARABIC_CRIT_RE = re.compile(
    r"(?:"
    r"\barabic\b[\w\s]*(?:proficien\w*|fluen\w*|language|speaking|native|working|conversational)?"
    r"|native\s+arabic"
    r"|arabic\s+(?:proficien\w*|fluen\w*|language|speaker)"
    r"|العربية|اللغة\s+العربية|إجادة\s+العربية|اللغة\s+العربية\s+\w+"
    r")",
    re.IGNORECASE | re.UNICODE,
)
_ENGLISH_CRIT_RE = re.compile(
    r"(?:"
    r"\benglish\b[\w\s]*(?:proficien\w*|fluen\w*|language|speaking|native|working|conversational|business|professional)?"
    r"|native\s+english"
    r"|working\s+english|business\s+english|professional\s+english"
    r"|english\s+(?:proficien\w*|fluen\w*|language|speaker)"
    r"|الإنجليزية|اللغة\s+الإنجليزية|إجادة\s+الإنجليزية"
    r")",
    re.IGNORECASE | re.UNICODE,
)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class LanguageProficiencyEvidence:
    """Deterministic inference result for a single language."""
    language: str
    confidence: float
    supporting_evidence: list[str] = field(default_factory=list)
    match_type: str = "inferred"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ar_ratio(text: str) -> float:
    ar_chars = sum(1 for c in text if "؀" <= c <= "ۿ")
    total = max(len(text.replace(" ", "")), 1)
    return ar_chars / total


def _header(cv_text: str) -> str:
    return cv_text[:1500]


def _country_in_text(text: str) -> str | None:
    """Return the first Arab-country term found in text, or None."""
    text_lower = text.lower()
    for term in sorted(ARABIC_COUNTRIES, key=len, reverse=True):
        if term in text_lower:
            idx = text_lower.find(term)
            ctx = text[max(0, idx - 10):idx + len(term) + 10].strip()
            return ctx
    return None


def _university_in_education(cv_facts: object) -> str | None:
    """Return institution name if it matches a known Arabic university."""
    for edu in getattr(cv_facts, "education", []):
        inst = (getattr(edu, "institution", None) or "").lower()
        if not inst:
            continue
        for uni in sorted(ARABIC_UNIVERSITIES, key=len, reverse=True):
            if uni in inst:
                return getattr(edu, "institution", None) or uni
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def infer_arabic_proficiency(
    cv_text: str,
    cv_facts: object,
) -> LanguageProficiencyEvidence | None:
    """
    Infer Arabic proficiency from CV text and facts.

    Returns LanguageProficiencyEvidence when confidence >= 0.50, else None.
    """
    evidence: list[str] = []
    conf = 0.0
    header = _header(cv_text)

    ratio = _ar_ratio(cv_text)
    if ratio > 0.50:
        evidence.append("CV written primarily in Arabic")
        conf = max(conf, CONF_NATIVE_AR_TEXT)
    elif ratio > 0.15:
        evidence.append("CV contains significant Arabic content")
        conf = max(conf, CONF_MIXED_AR_TEXT)

    natl_m = _NATL_LABEL_RE.search(header)
    if natl_m:
        natl_text = natl_m.group(1).strip()
        if _country_in_text(natl_text):
            evidence.append(f"Nationality: {natl_text}")
            conf = max(conf, CONF_NATIONALITY)

    loc_m = _LOC_LABEL_RE.search(header)
    if loc_m:
        loc_text = loc_m.group(1).strip()
        if _country_in_text(loc_text):
            evidence.append(f"Location: {loc_text}")
            conf = max(conf, CONF_LOCATION)

    if not any("Nationality" in e or "Location" in e for e in evidence):
        hit = _country_in_text(header)
        if hit:
            evidence.append(hit)
            conf = max(conf, CONF_COUNTRY_IN_HEADER)

    uni = _university_in_education(cv_facts)
    if uni:
        evidence.append(uni)
        conf = max(conf, CONF_ARABIC_UNIVERSITY)

    if conf < 0.50 or not evidence:
        return None

    return LanguageProficiencyEvidence(
        language="arabic",
        confidence=round(conf, 2),
        supporting_evidence=evidence[:4],
        match_type="inferred",
    )


def infer_english_proficiency(
    cv_text: str,
    cv_facts: object,
) -> LanguageProficiencyEvidence | None:
    """
    Infer English proficiency from CV text and facts.

    Returns LanguageProficiencyEvidence when confidence >= 0.50, else None.
    """
    evidence: list[str] = []
    conf = 0.0

    ratio = _ar_ratio(cv_text)
    if ratio < 0.15:
        evidence.append("CV written primarily in English")
        conf = max(conf, CONF_EN_CV_PRIMARY)
    elif ratio < 0.50:
        evidence.append("CV contains significant English content")
        conf = max(conf, CONF_EN_CV_MIXED)

    for edu in getattr(cv_facts, "education", []):
        fos = getattr(edu, "field_of_study", "") or ""
        inst = getattr(edu, "institution", "") or ""
        combined = fos + inst
        if combined and not any("؀" <= c <= "ۿ" for c in combined):
            label = inst or fos
            if label:
                evidence.append(f"Education: {label}")
                conf = max(conf, CONF_EN_EDUCATION)
                break

    en_exp_count = 0
    for exp in getattr(cv_facts, "experience", [])[:6]:
        employer = getattr(exp, "employer", "") or ""
        role = getattr(exp, "role_title", "") or ""
        combined = employer + role
        if combined and not any("؀" <= c <= "ۿ" for c in combined):
            en_exp_count += 1
    if en_exp_count >= 2:
        evidence.append(f"English-language work history ({en_exp_count} entries)")
        conf = max(conf, CONF_EN_WORK_HISTORY)

    if conf < 0.50 or not evidence:
        return None

    return LanguageProficiencyEvidence(
        language="english",
        confidence=round(conf, 2),
        supporting_evidence=evidence[:4],
        match_type="inferred",
    )


def is_arabic_language_criterion(criterion_text: str) -> bool:
    """True when criterion_text describes an Arabic language requirement."""
    return bool(_ARABIC_CRIT_RE.search(criterion_text))


def is_english_language_criterion(criterion_text: str) -> bool:
    """True when criterion_text describes an English language requirement."""
    return bool(_ENGLISH_CRIT_RE.search(criterion_text))


def is_language_criterion(criterion_text: str) -> bool:
    """True when criterion_text describes any language requirement."""
    return is_arabic_language_criterion(criterion_text) or is_english_language_criterion(criterion_text)
