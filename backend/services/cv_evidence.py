"""
Layer 1 — CV Evidence Extraction dataclasses.

These dataclasses define the canonical, structured representation of what a CV
contains.  They are produced by the CVFactsExtractor (Batch 2A-4) and persisted
to application_scores.cv_facts_json (Batch 2A-3).

No extraction logic lives here — this file is pure data contracts.
No LLM calls, no DB access, no scoring changes.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Literal, Optional


# ── Type aliases ──────────────────────────────────────────────────────────────

MatchMethod = Literal["exact", "normalised", "fuzzy", "semantic", "inferred", "absent"]
LanguageCode = Literal["ar", "en", "mixed"]

# Education levels ordered from lowest to highest for comparison.
EDUCATION_LEVELS: tuple[str, ...] = (
    "None",
    "High School",
    "Diploma",
    "Associate",
    "Bachelor's",
    "Master's",
    "PhD",
)


# ── Evidence atoms ────────────────────────────────────────────────────────────

@dataclass
class SkillEvidence:
    """A single skill claim extracted from the CV.

    Explicit evidence is directly stated (e.g. "Microsoft Excel" in a skills
    list).  Inferred evidence is derived from context (e.g. "prepared monthly
    spreadsheet reports" → Excel).  Both types are valid but carry different
    confidence levels.
    """

    skill_name: str
    """Normalised skill name (diacritics stripped, homoglyphs resolved).
    E.g. "إكسل" is normalised to "Microsoft Excel"."""

    raw_text: str
    """Exact phrase as it appeared in the CV before normalisation."""

    explicit: bool
    """True = directly stated in a skills section or bullet point.
    False = inferred from a responsibility or achievement description."""

    confidence: float
    """Match confidence 0.0–1.0.  See confidence scale in architecture doc."""

    context_snippet: str
    """Up to 200 characters of surrounding CV text providing context."""

    language: LanguageCode
    """Language of the raw_text."""

    section_hint: str
    """Heuristic section label: 'skills' | 'experience' | 'education' | 'other'."""

    inference_basis: str = ""
    """Non-empty only for inferred evidence.
    E.g. 'prepared spreadsheets → Microsoft Excel'."""


@dataclass
class ExperienceEvidence:
    """A single employment or project block extracted from the CV.

    One ExperienceEvidence is created per distinct employer/role entry.
    Years is computed from dates where parseable; 0.0 when dates are absent
    or unparseable.
    """

    employer: str
    """Extracted employer or organisation name."""

    role_title: str
    """Job title or role description."""

    years: float
    """Computed duration in years (0.0 if dates are absent or unparseable)."""

    responsibilities: list[str] = field(default_factory=list)
    """Bullet points or sentences describing what the candidate did."""

    domain_signals: list[str] = field(default_factory=list)
    """Domain keywords found in this block (e.g. 'records management', 'GDPR')."""

    skill_signals: list[str] = field(default_factory=list)
    """Skill names identified within this experience block."""

    raw_text: str = ""
    """Original text block before parsing."""


@dataclass
class EducationEvidence:
    """A single education entry from the CV.

    degree_level is normalised to one of the values in EDUCATION_LEVELS so
    it can be compared algorithmically against job requirements.
    """

    degree_level: str
    """Normalised level: one of EDUCATION_LEVELS values."""

    field_of_study: str
    """Field or major (e.g. 'Business Administration', 'Computer Science')."""

    institution: str
    """University, college, or school name."""

    year: int | None
    """Graduation year (None if not stated or unparseable)."""

    raw_text: str = ""
    """Original text before parsing."""


@dataclass
class CertificationEvidence:
    """A single certification or professional credential found in the CV."""

    name: str
    """Normalised certification name."""

    raw_text: str
    """Original text as it appeared in the CV."""

    issuer: str = ""
    """Issuing body if extractable (e.g. 'Microsoft', 'PMI', 'ISO')."""

    year: int | None = None
    """Year obtained (None if not stated)."""


@dataclass
class SoftSkillSignal:
    """Behavioural evidence phrase suggesting a soft skill.

    Soft skills are rarely listed explicitly; they are inferred from
    responsibility descriptions (e.g. 'led a team of 5' → leadership).
    """

    soft_skill_category: str
    """Standardised category: 'communication' | 'leadership' | 'teamwork' |
    'problem_solving' | 'adaptability' | 'time_management' | 'other'."""

    evidence_phrase: str
    """The CV text that signals this soft skill."""

    confidence: float
    """Inference confidence 0.0–1.0."""

    inference_basis: str = ""
    """Explanation of the inference, e.g. 'led a team of 5 → leadership'."""


@dataclass
class DomainSignal:
    """An industry or domain keyword found in the CV.

    Multiple occurrences of the same term are collapsed into a single
    DomainSignal with frequency > 1 and multiple context_snippets.
    """

    domain_term: str
    """Normalised domain term (e.g. 'records management', 'GDPR', 'ISO 9001')."""

    frequency: int = 1
    """Number of times this term appears in the CV."""

    context_snippets: list[str] = field(default_factory=list)
    """Up to 3 surrounding text windows (≤150 chars each) for evidence."""


# ── Aggregate CV representation ───────────────────────────────────────────────

@dataclass
class CVFacts:
    """Canonical, structured representation of what a CV contains.

    Produced by Layer 1 (CVFactsExtractor, Batch 2A-4) before any LLM call.
    All text fields are normalised: diacritics stripped, homoglyphs resolved,
    whitespace collapsed.

    Persisted to application_scores.cv_facts_json (Batch 2A-3).
    The _version field enables cache invalidation when the extractor changes.
    """

    # ── Language ──────────────────────────────────────────────────────────
    language: LanguageCode
    """Dominant language of the CV text."""

    total_char_count: int
    """Total character count of the normalised CV text."""

    # ── Evidence collections ──────────────────────────────────────────────
    skills: list[SkillEvidence] = field(default_factory=list)
    """All skill evidence items, explicit and inferred, in extraction order."""

    experience: list[ExperienceEvidence] = field(default_factory=list)
    """Employment and project blocks, most recent first where detectable."""

    education: list[EducationEvidence] = field(default_factory=list)
    """Education entries, highest level first where detectable."""

    certifications: list[CertificationEvidence] = field(default_factory=list)
    """Certification and credential entries."""

    soft_skill_signals: list[SoftSkillSignal] = field(default_factory=list)
    """Inferred soft skill signals from responsibility descriptions."""

    domain_signals: list[DomainSignal] = field(default_factory=list)
    """Domain and industry keyword occurrences."""

    # ── Pre-computed aggregates (convenience for Layer 2) ─────────────────
    total_experience_years: float = 0.0
    """Sum of ExperienceEvidence.years across all non-overlapping blocks.
    Computed by extractor; 0.0 if no parseable dates were found."""

    highest_education_level: str = "None"
    """Highest degree level found, using EDUCATION_LEVELS ordering."""

    skill_names_normalised: list[str] = field(default_factory=list)
    """Deduplicated, normalised skill names for fast lookup."""

    # ── Extraction metadata ───────────────────────────────────────────────
    extractor_version: str = "0.0.0"
    """Semver of the CVFactsExtractor that produced this record.
    Used to invalidate cached CVFacts when the extractor changes."""

    extraction_method: str = "rule_based_v1"
    """Extraction strategy: 'rule_based_v1' | 'hybrid_v1' | 'llm_assisted_v1'."""

    extraction_warnings: list[str] = field(default_factory=list)
    """Non-fatal issues encountered during extraction (e.g. 'date unparseable',
    'skills section not found')."""


# ── CVFactsExtractor — Batch 2A-4 ────────────────────────────────────────────

_EXTRACTOR_VERSION = "1.0.0"
_CURRENT_YEAR: int = datetime.date.today().year

# ---------------------------------------------------------------------------
# Skill registry
# (normalised_skill_name, (regex_pattern_string, ...))
# Patterns matched case-insensitively.  Arabic patterns are included for
# bilingual CVs.
# ---------------------------------------------------------------------------
_SKILL_REGISTRY: tuple[tuple[str, tuple[str, ...]], ...] = (
    # ── Programming languages ──────────────────────────────────────────────
    ("Python",               (r"\bpython\b",                   r"\bبايثون\b")),
    ("Java",                 (r"\bjava\b(?!\s*script)",         r"\bجافا\b")),
    ("JavaScript",           (r"\bjavascript\b",               r"\bجافاسكريبت\b")),
    ("TypeScript",           (r"\btypescript\b",)),
    ("C++",                  (r"\bc\+\+",                      r"\bcpp\b")),
    ("C#",                   (r"\bc#",                         r"\bc\s+sharp\b")),
    ("PHP",                  (r"\bphp\b",)),
    ("Ruby",                 (r"\bruby\b",)),
    ("Golang",               (r"\bgolang\b",                   r"\bgo\s+lang\b")),
    ("Rust",                 (r"\brust\b",)),
    ("Swift",                (r"\bswift\b",)),
    ("Kotlin",               (r"\bkotlin\b",)),
    ("Scala",                (r"\bscala\b",)),
    # ── Web frameworks ─────────────────────────────────────────────────────
    ("React",                (r"\breact\.?js\b",               r"\breact\b",       r"\bريأكت\b")),
    ("Angular",              (r"\bangular\b",)),
    ("Vue.js",               (r"\bvue\.?js\b",                 r"\bvuejs\b")),
    ("Django",               (r"\bdjango\b",)),
    ("Flask",                (r"\bflask\b",)),
    ("Spring Boot",          (r"\bspring\s+boot\b",            r"\bspring\s+framework\b")),
    ("Node.js",              (r"\bnode\.?js\b",                r"\bnodejs\b")),
    ("Laravel",              (r"\blaravel\b",)),
    ("Next.js",              (r"\bnext\.?js\b",)),
    ("FastAPI",              (r"\bfastapi\b",)),
    # ── Databases ──────────────────────────────────────────────────────────
    ("SQL",                  (r"\bsql\b",                      r"\bقواعد\s+البيانات\b")),
    ("PostgreSQL",           (r"\bpostgresql\b",               r"\bpostgres\b")),
    ("MySQL",                (r"\bmysql\b",)),
    ("Oracle Database",      (r"\boracle\s+database\b",        r"\boracle\s+db\b",     r"\boracle\b")),
    ("MongoDB",              (r"\bmongodb\b",                  r"\bmongo\b")),
    ("Redis",                (r"\bredis\b",)),
    ("SQL Server",           (r"\bsql\s+server\b",             r"\bmssql\b")),
    ("SQLite",               (r"\bsqlite\b",)),
    ("Elasticsearch",        (r"\belasticsearch\b",)),
    # ── Cloud & DevOps ─────────────────────────────────────────────────────
    ("AWS",                  (r"\baws\b",                      r"\bamazon\s+web\s+services\b")),
    ("Azure",                (r"\bazure\b",                    r"\bmicrosoft\s+azure\b")),
    ("Google Cloud",         (r"\bgoogle\s+cloud\b",           r"\bgcp\b")),
    ("Docker",               (r"\bdocker\b",)),
    ("Kubernetes",           (r"\bkubernetes\b",               r"\bk8s\b")),
    ("Terraform",            (r"\bterraform\b",)),
    ("Ansible",              (r"\bansible\b",)),
    ("Git",                  (r"\bgit\b",)),
    ("CI/CD",                (r"\bci/cd\b",                    r"\bcontinuous\s+integration\b")),
    # ── Microsoft Office & Productivity ────────────────────────────────────
    ("Microsoft Excel",      (r"\bexcel\b",             r"\bms\s+excel\b",       r"\bإكسل\b")),
    ("Microsoft Word",       (r"\bmicrosoft\s+word\b",  r"\bms\s+word\b",        r"\bوورد\b")),
    ("Microsoft PowerPoint", (r"\bpowerpoint\b",        r"\bppt\b",              r"\bباوربوينت\b")),
    ("Microsoft Office",     (r"\bmicrosoft\s+office\b",r"\bms\s+office\b",      r"\boffice\s+365\b", r"\boffice\s+suite\b")),
    ("Microsoft Outlook",    (r"\boutlook\b",)),
    ("SharePoint",           (r"\bsharepoint\b",)),
    ("Google Sheets",        (r"\bgoogle\s+sheets\b",   r"\bgoogle\s+docs\b")),
    # ── BI & Analytics ─────────────────────────────────────────────────────
    ("Power BI",             (r"\bpower\s+bi\b",)),
    ("Tableau",              (r"\btableau\b",)),
    ("Qlik",                 (r"\bqlik(?:view|sense)?\b",)),
    # ── ERP & Business Systems ─────────────────────────────────────────────
    ("SAP",                  (r"\bsap\b",)),
    ("Microsoft Dynamics",   (r"\bdynamics\s+365\b",    r"\bdynamics\s+crm\b",   r"\bnavision\b")),
    ("Salesforce",           (r"\bsalesforce\b",)),
    # ── Project Management tools ───────────────────────────────────────────
    ("Jira",                 (r"\bjira\b",)),
    ("Confluence",           (r"\bconfluence\b",)),
    ("Trello",               (r"\btrello\b",)),
    ("Asana",                (r"\basana\b",)),
    # ── Document & Records Management ─────────────────────────────────────
    ("OpenText",             (r"\bopentext\b",)),
    ("Documentum",           (r"\bdocumentum\b",)),
    ("Alfresco",             (r"\balfresco\b",)),
    ("FileNet",              (r"\bfilenet\b",)),
    # ── Data Science & ML ──────────────────────────────────────────────────
    ("Pandas",               (r"\bpandas\b",)),
    ("NumPy",                (r"\bnumpy\b",)),
    ("TensorFlow",           (r"\btensorflow\b",)),
    ("PyTorch",              (r"\bpytorch\b",)),
    ("Scikit-learn",         (r"\bscikit-learn\b",            r"\bsklearn\b")),
    ("Apache Spark",         (r"\bapache\s+spark\b",          r"\bpyspark\b")),
    # ── Design & Creative ──────────────────────────────────────────────────
    ("Adobe Photoshop",      (r"\bphotoshop\b",               r"\bفوتوشوب\b")),
    ("Figma",                (r"\bfigma\b",)),
    ("AutoCAD",              (r"\bautocad\b",)),
    # ── Networking & Security ──────────────────────────────────────────────
    ("Cisco",                (r"\bcisco\b",)),
    ("Cybersecurity",        (r"\bcybersecurity\b",           r"\binformation\s+security\b")),
    # ── Methodology ───────────────────────────────────────────────────────
    ("Scrum",                (r"\bscrum\b",)),
    ("Agile",                (r"\bagile\b",)),
)

# Compile skill patterns once at module load
_COMPILED_SKILLS: tuple[tuple[str, tuple[re.Pattern, ...]], ...] = tuple(
    (name, tuple(re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns))
    for name, patterns in _SKILL_REGISTRY
)

# ---------------------------------------------------------------------------
# Education level patterns — most specific first
# ---------------------------------------------------------------------------
_EDU_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("PhD",        re.compile(r"\b(ph\.?\s*d\.?|doctorate|doctoral|دكتوراه)\b",                    re.IGNORECASE | re.UNICODE)),
    ("Master's",   re.compile(r"\b(master(?:'?s)?|m\.?\s*sc\.?|mba|m\.?\s*b\.?\s*a\.?|m\.?\s*eng\.?|ماجستير)\b", re.IGNORECASE | re.UNICODE)),
    ("Bachelor's", re.compile(r"\b(bachelor(?:'?s)?|b\.?\s*sc\.?|b\.?\s*a\.?|b\.?\s*eng\.?|بكالوريوس|ليسانس)\b", re.IGNORECASE | re.UNICODE)),
    ("Diploma",    re.compile(r"\b(diploma|hnd|higher\s+national\s+diploma|دبلوم)\b",              re.IGNORECASE | re.UNICODE)),
    ("Associate",  re.compile(r"\b(associate\s+degree)\b",                                          re.IGNORECASE | re.UNICODE)),
    ("High School",re.compile(r"\b(high\s+school|secondary\s+school|gcse|baccalaureate|a-levels?|o-levels?|ثانوية\s+عامة|شهادة\s+ثانوية)\b", re.IGNORECASE | re.UNICODE)),
)

# ---------------------------------------------------------------------------
# Certification patterns
# ---------------------------------------------------------------------------
_CERT_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("PMP",                    re.compile(r"\bpmp\b",                                             re.IGNORECASE)),
    ("PRINCE2",                re.compile(r"\bprince\s*2\b",                                      re.IGNORECASE)),
    ("ITIL",                   re.compile(r"\bitil\b",                                            re.IGNORECASE)),
    ("ISO 9001",               re.compile(r"\biso\s*9001\b",                                      re.IGNORECASE)),
    ("ISO 27001",              re.compile(r"\biso\s*27001\b",                                     re.IGNORECASE)),
    ("ISO 15489",              re.compile(r"\biso\s*15489\b",                                     re.IGNORECASE)),
    ("AWS Certified",          re.compile(r"\baws\s+certif\w*",                                   re.IGNORECASE)),
    ("Azure Certified",        re.compile(r"\bazure\s+certif\w*|az-\d{3}\b",                     re.IGNORECASE)),
    ("CCNA",                   re.compile(r"\bccna\b",                                            re.IGNORECASE)),
    ("CCNP",                   re.compile(r"\bccnp\b",                                            re.IGNORECASE)),
    ("CompTIA Security+",      re.compile(r"\bsecurity\+|\bcomptia\s+security\b",                 re.IGNORECASE)),
    ("CompTIA A+",             re.compile(r"\bcomptia\s+a\+",                                     re.IGNORECASE)),
    ("CPA",                    re.compile(r"\bcpa\b",                                             re.IGNORECASE)),
    ("CFA",                    re.compile(r"\bcfa\b",                                             re.IGNORECASE)),
    ("ACCA",                   re.compile(r"\bacca\b",                                            re.IGNORECASE)),
    ("Microsoft Certified",    re.compile(r"\bmicrosoft\s+certif\w*|\bmcp\b|\bmcsa\b|\bmcse\b",   re.IGNORECASE)),
    ("Scrum Master",           re.compile(r"\bscrum\s+master\b|\bcsm\b",                          re.IGNORECASE)),
    ("Six Sigma",              re.compile(r"\bsix\s+sigma\b|\blean\s+six\s+sigma\b",              re.IGNORECASE)),
    ("Certified Records Manager", re.compile(r"\bcertified\s+records\s+manager\b",               re.IGNORECASE)),
    ("Google Cloud Certified", re.compile(r"\bgoogle\s+cloud\s+certif\w*|\bgcp\s+certif\w*",     re.IGNORECASE)),
)

# ---------------------------------------------------------------------------
# Soft skill signal patterns: (category, ((pattern, confidence), ...))
# ---------------------------------------------------------------------------
_SOFT_SKILL_PATTERNS: tuple[tuple[str, tuple[tuple[str, float], ...]], ...] = (
    ("leadership", (
        (r"\bled\s+(?:a\s+)?team\b",         0.80),
        (r"\bmanaged\s+(?:a\s+)?team\b",     0.80),
        (r"\bsupervised\b",                  0.75),
        (r"\boversaw\b",                     0.75),
        (r"\bmentored\b",                    0.70),
        (r"\bأدار\b",                        0.75),
        (r"\bقاد\b",                         0.75),
    )),
    ("communication", (
        (r"\bpresented\s+to\b",              0.75),
        (r"\bliaised\b",                     0.75),
        (r"\bnegotiated\b",                  0.75),
        (r"\bdrafted\s+reports\b",           0.70),
        (r"\bprepared\s+(?:formal\s+)?reports\b", 0.65),
    )),
    ("teamwork", (
        (r"\bcollaborated\b",                0.75),
        (r"\bcross-functional\b",            0.75),
        (r"\bcoordinated\s+with\b",          0.70),
        (r"\bworked\s+with\s+(?:a\s+)?team\b", 0.70),
    )),
    ("problem_solving", (
        (r"\bresolved\s+(?:issues|problems|conflicts)\b", 0.75),
        (r"\btroubleshoot\w*\b",             0.70),
        (r"\bidentified\s+solutions\b",      0.70),
        (r"\bdiagnosed\b",                   0.65),
    )),
    ("time_management", (
        (r"\bmet\s+deadlines\b",             0.75),
        (r"\bdelivered\s+on\s+time\b",       0.75),
        (r"\bprioritis?ed\b",               0.70),
        (r"\bmanaged\s+multiple\s+(?:tasks|projects)\b", 0.70),
    )),
    ("adaptability", (
        (r"\badapted\s+to\b",                0.70),
        (r"\bfast-paced\s+environment\b",    0.65),
        (r"\brapidly\s+changing\b",          0.65),
    )),
)

_COMPILED_SOFT_SKILLS: tuple[tuple[str, tuple[tuple[re.Pattern, float], ...]], ...] = tuple(
    (
        category,
        tuple((re.compile(p, re.IGNORECASE | re.UNICODE), conf) for p, conf in patterns),
    )
    for category, patterns in _SOFT_SKILL_PATTERNS
)

# ---------------------------------------------------------------------------
# Domain signal patterns
# ---------------------------------------------------------------------------
_DOMAIN_REGISTRY: tuple[tuple[str, tuple[re.Pattern, ...]], ...] = tuple(
    (
        term,
        tuple(re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns),
    )
    for term, patterns in (
        ("records management",            [r"\brecords\s+management\b",        r"\bإدارة\s+الوثائق\b"]),
        ("document control",              [r"\bdocument\s+control\b"]),
        ("archiving",                     [r"\barchiv\w*",                      r"\bأرشفة"]),
        ("filing system",                 [r"\bfiling\s+system\b",             r"\bfile\s+management\b"]),
        ("digitization",                  [r"\bdigitiz\w*",                    r"\bdigitis\w*",        r"\bرقمنة"]),
        ("data protection",               [r"\bdata\s+protection\b",           r"\bGDPR\b"]),
        ("compliance",                    [r"\bcompliance\b",                   r"\bامتثال"]),
        ("quality assurance",             [r"\bquality\s+assurance\b",         r"\bqc\b(?!\s*\d)"]),
        ("ISO 9001",                      [r"\biso\s*9001\b"]),
        ("ISO 15489",                     [r"\biso\s*15489\b"]),
        ("project management",            [r"\bproject\s+management\b",        r"\bإدارة\s+المشاريع\b"]),
        ("procurement",                   [r"\bprocurement\b",                  r"\bمشتريات"]),
        ("supply chain",                  [r"\bsupply\s+chain\b"]),
        ("finance",                       [r"\bfinance\b",                      r"\baccounting\b",     r"\bمالية"]),
        ("budgeting",                     [r"\bbudget\w*",                      r"\bميزانية"]),
        ("human resources",               [r"\bhuman\s+resources\b",            r"\bموارد\s+بشرية"]),
        ("recruitment",                   [r"\brecruitment\b",                  r"\bتوظيف"]),
        ("customer service",              [r"\bcustomer\s+service\b",           r"\bخدمة\s+العملاء"]),
        ("information security",          [r"\binformation\s+security\b",       r"\bأمن\s+المعلومات"]),
        ("enterprise content management", [r"\becm\b",                          r"\bcontent\s+management\b"]),
        ("digital transformation",        [r"\bdigital\s+transform\w*"]),
        ("data analysis",                 [r"\bdata\s+analys\w*",               r"\bتحليل\s+البيانات"]),
    )
)

# ---------------------------------------------------------------------------
# Section header detection
# ---------------------------------------------------------------------------
_SECTION_RE: dict[str, re.Pattern] = {
    "skills": re.compile(
        r"^(?:technical\s+)?(?:skills?|competenc\w+|proficienc\w+|expertise"
        r"|المهارات|مهارات|القدرات|قدرات)",
        re.IGNORECASE | re.UNICODE,
    ),
    "experience": re.compile(
        r"^(?:(?:work\s+)?experience|employment|career\s+history|professional\s+(?:background|experience)"
        r"|الخبرة|خبرة|الخبرات|تجربة|تجربة\s+العمل)",
        re.IGNORECASE | re.UNICODE,
    ),
    "education": re.compile(
        r"^(?:education|qualif\w+|academic"
        r"|التعليم|تعليم|المؤهلات|مؤهلات|الدراسة)",
        re.IGNORECASE | re.UNICODE,
    ),
    "certifications": re.compile(
        r"^(?:certif\w+|accredit\w+|licens\w+|courses?|training"
        r"|الشهادات|شهادات|التدريب|تدريب|الدورات|دورات)",
        re.IGNORECASE | re.UNICODE,
    ),
    "summary": re.compile(
        r"^(?:summary|objective|profile|about\s+me|الملخص|ملخص)",
        re.IGNORECASE | re.UNICODE,
    ),
    "languages": re.compile(
        r"^(?:languages?|اللغات|لغات)",
        re.IGNORECASE | re.UNICODE,
    ),
}

# Date range pattern for experience year extraction
_DATE_RANGE_RE = re.compile(
    r"\b(?P<start>(?:19|20)\d{2})\s*[-–—/to]+\s*"
    r"(?P<end>(?:(?:19|20)\d{2})|present|current|now|ongoing|till\s+date|حاليا|حالياً|الآن)\b",
    re.IGNORECASE | re.UNICODE,
)


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------

def _infer_language_from_chars(text: str) -> LanguageCode:
    ar_chars = sum(1 for c in text if "؀" <= c <= "ۿ")
    total = max(len(text.replace(" ", "")), 1)
    ratio = ar_chars / total
    if ratio > 0.5:
        return "ar"
    if ratio > 0.15:
        return "mixed"
    return "en"


def _detect_section_header(line: str) -> Optional[str]:
    stripped = line.strip().rstrip(":–—-").strip()
    if not stripped or len(stripped) > 55:
        return None
    for sec_name, pat in _SECTION_RE.items():
        m = pat.match(stripped)
        if m and m.end() >= len(stripped) * 0.8:
            return sec_name
    return None


def _split_into_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "skills": [], "experience": [], "education": [],
        "certifications": [], "summary": [], "languages": [], "other": [],
    }
    current = "other"
    for line in text.splitlines():
        header = _detect_section_header(line)
        if header:
            current = header
        elif line.strip():
            sections[current].append(line.strip())
    return sections


def _get_context_snippet(text: str, match: re.Match, max_len: int = 150) -> str:
    start = max(0, match.start() - 50)
    end = min(len(text), match.end() + 50)
    snippet = text[start:end].strip()
    return snippet[:max_len]


def _is_arabic_match(raw: str) -> bool:
    return any("؀" <= c <= "ۿ" for c in raw)


def _extract_skills(sections: dict[str, list[str]], full_text: str) -> list[SkillEvidence]:
    """Extract skills with priority: skills section > experience > other."""
    skills_text = "\n".join(sections.get("skills", []))
    exp_text = "\n".join(sections.get("experience", []))
    other_text = "\n".join(
        line
        for sec in ("summary", "education", "certifications", "languages", "other")
        for line in sections.get(sec, [])
    )

    # Priority order with associated confidence/explicit flags
    search_order = [
        (skills_text,  True,  0.90, "skills"),
        (exp_text,     False, 0.35, "experience"),
        (other_text,   True,  0.75, "other"),
    ]

    found: dict[str, SkillEvidence] = {}

    for skill_name, compiled_patterns in _COMPILED_SKILLS:
        for text, explicit, confidence, section_hint in search_order:
            if not text or skill_name in found:
                continue
            for pat in compiled_patterns:
                m = pat.search(text)
                if m:
                    raw = m.group(0)
                    found[skill_name] = SkillEvidence(
                        skill_name=skill_name,
                        raw_text=raw,
                        explicit=explicit,
                        confidence=confidence,
                        context_snippet=_get_context_snippet(text, m),
                        language="ar" if _is_arabic_match(raw) else "en",
                        section_hint=section_hint,
                        inference_basis="" if explicit else f"mentioned in {section_hint} section",
                    )
                    break
            if skill_name in found:
                break

    return list(found.values())


def _extract_education(full_text: str) -> tuple[list[EducationEvidence], str]:
    """Return (education_entries, highest_level_name)."""
    found: list[EducationEvidence] = []
    highest_idx = 0

    for level, pat in _EDU_PATTERNS:
        m = pat.search(full_text)
        if m:
            ctx_start = max(0, m.start() - 20)
            ctx_end = min(len(full_text), m.end() + 120)
            raw = full_text[ctx_start:ctx_end].strip()[:200]
            found.append(EducationEvidence(
                degree_level=level,
                field_of_study="",
                institution="",
                year=None,
                raw_text=raw,
            ))
            level_idx = list(EDUCATION_LEVELS).index(level)
            if level_idx > highest_idx:
                highest_idx = level_idx

    highest = EDUCATION_LEVELS[highest_idx] if found else "None"
    return found, highest


def _extract_certifications(full_text: str) -> list[CertificationEvidence]:
    certs: list[CertificationEvidence] = []
    for name, pat in _CERT_PATTERNS:
        m = pat.search(full_text)
        if m:
            ctx_start = max(0, m.start() - 10)
            ctx_end = min(len(full_text), m.end() + 60)
            raw = full_text[ctx_start:ctx_end].strip()[:100]
            certs.append(CertificationEvidence(name=name, raw_text=raw))
    return certs


def _extract_soft_skills(sections: dict[str, list[str]]) -> list[SoftSkillSignal]:
    search_text = "\n".join(
        line
        for sec in ("experience", "summary", "other")
        for line in sections.get(sec, [])
    )
    signals: list[SoftSkillSignal] = []
    for category, compiled_patterns in _COMPILED_SOFT_SKILLS:
        for pat, confidence in compiled_patterns:
            m = pat.search(search_text)
            if m:
                phrase = m.group(0).strip()
                signals.append(SoftSkillSignal(
                    soft_skill_category=category,
                    evidence_phrase=phrase,
                    confidence=confidence,
                    inference_basis=f"{phrase} → {category}",
                ))
                break  # one signal per category
    return signals


def _extract_domain_signals(full_text: str) -> list[DomainSignal]:
    signals: list[DomainSignal] = []
    for term, compiled_patterns in _DOMAIN_REGISTRY:
        freq = 0
        snippets: list[str] = []
        for pat in compiled_patterns:
            for m in pat.finditer(full_text):
                freq += 1
                if len(snippets) < 3:
                    s = max(0, m.start() - 40)
                    e = min(len(full_text), m.end() + 40)
                    snippets.append(full_text[s:e].strip()[:150])
        if freq > 0:
            signals.append(DomainSignal(
                domain_term=term,
                frequency=freq,
                context_snippets=snippets,
            ))
    return signals


def _extract_experience(
    sections: dict[str, list[str]],
    full_text: str,
) -> tuple[list[ExperienceEvidence], float]:
    """Return (experience_blocks, total_years)."""
    exp_section = "\n".join(sections.get("experience", []))
    search_text = exp_section if exp_section.strip() else full_text

    total_years = 0.0
    blocks: list[ExperienceEvidence] = []

    for m in _DATE_RANGE_RE.finditer(search_text):
        start_year = int(m.group("start"))
        end_str = m.group("end").strip().lower()

        if end_str in ("present", "current", "now", "ongoing", "till date", "حاليا", "حالياً", "الآن"):
            end_year = _CURRENT_YEAR
        else:
            try:
                end_year = int(end_str)
            except ValueError:
                continue

        if end_year < start_year or (end_year - start_year) > 50:
            continue

        years = float(end_year - start_year)

        # Attempt to extract role/employer from the lines preceding the date
        ctx_start = max(0, m.start() - 200)
        preceding = search_text[ctx_start:m.start()].strip().splitlines()
        role_title = preceding[-1].strip()[:100] if preceding else ""
        employer = preceding[-2].strip()[:100] if len(preceding) >= 2 else ""

        ctx_end = min(len(search_text), m.end() + 50)
        raw = search_text[ctx_start:ctx_end].strip()[:300]

        blocks.append(ExperienceEvidence(
            employer=employer,
            role_title=role_title,
            years=years,
            raw_text=raw,
        ))
        total_years += years

    total_years = min(total_years, 50.0)
    return blocks, total_years


# ---------------------------------------------------------------------------
# Public extractor class
# ---------------------------------------------------------------------------

class CVFactsExtractor:
    """Rule-based extractor that converts raw CV text into a CVFacts object.

    Supports English, Arabic, and mixed-language CVs.  No LLM calls — all
    extraction is pattern-based.  Import from local_processor is deferred to
    avoid triggering the sentence-transformer model load at import time.
    """

    VERSION = _EXTRACTOR_VERSION

    def extract(self, cv_text: str) -> CVFacts:
        """Extract structured facts from raw CV text.

        Parameters
        ----------
        cv_text:
            Raw CV text (UTF-8, any language).

        Returns
        -------
        CVFacts
            Populated dataclass.  ``extraction_warnings`` lists any non-fatal
            issues encountered.
        """
        warnings: list[str] = []

        if not cv_text or not cv_text.strip():
            return CVFacts(
                language="en",
                total_char_count=0,
                extractor_version=self.VERSION,
                extraction_warnings=["empty cv text"],
            )

        # Clean text — defer import to avoid loading numpy/sentence-transformers
        try:
            from services.local_processor import clean_text, detect_language
        except ImportError:
            # Fallback for environments without local_processor (e.g. unit tests
            # that run without the full ML stack)
            def clean_text(t: str) -> str:  # type: ignore[misc]
                import unicodedata
                return " ".join(unicodedata.normalize("NFC", t).split())

            def detect_language(t: str) -> str:  # type: ignore[misc]
                return "en"

        # clean_text collapses newlines — use it only for char_count/language.
        # Section splitting and pattern matching use cv_text to preserve structure.
        cleaned_for_meta = clean_text(cv_text)
        char_count = len(cleaned_for_meta)

        # Language detection
        try:
            lang_raw = detect_language(cleaned_for_meta)
            if lang_raw in ("ar", "en", "mixed"):
                language: LanguageCode = lang_raw  # type: ignore[assignment]
            else:
                language = _infer_language_from_chars(cv_text)
                warnings.append(
                    f"langdetect returned '{lang_raw}'; fell back to char-ratio heuristic"
                )
        except Exception:
            language = _infer_language_from_chars(cv_text)
            warnings.append("language detection failed; used char-ratio heuristic")

        # Override: langdetect can misclassify bilingual text as "en".
        # If the char-ratio heuristic detects significant Arabic, trust it.
        if language == "en":
            char_lang = _infer_language_from_chars(cv_text)
            if char_lang in ("ar", "mixed"):
                language = char_lang

        # Use original cv_text (newlines intact) so section headers are preserved.
        sections = _split_into_sections(cv_text)

        skills = _extract_skills(sections, cv_text)
        education, highest_edu = _extract_education(cv_text)
        certifications = _extract_certifications(cv_text)
        soft_skill_signals = _extract_soft_skills(sections)
        domain_signals = _extract_domain_signals(cv_text)
        experience_blocks, total_years = _extract_experience(sections, cv_text)

        if not skills:
            warnings.append("no skills extracted")
        if not education:
            warnings.append("no education section found")

        skill_names = list(dict.fromkeys(s.skill_name for s in skills))

        return CVFacts(
            language=language,
            total_char_count=char_count,
            skills=skills,
            experience=experience_blocks,
            education=education,
            certifications=certifications,
            soft_skill_signals=soft_skill_signals,
            domain_signals=domain_signals,
            total_experience_years=total_years,
            highest_education_level=highest_edu,
            skill_names_normalised=skill_names,
            extractor_version=self.VERSION,
            extraction_method="rule_based_v1",
            extraction_warnings=warnings,
        )
