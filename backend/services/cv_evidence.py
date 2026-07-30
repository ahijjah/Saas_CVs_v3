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

    risk_flag: str = ""
    """Non-empty for extracted evidence with special risk profiles.
    E.g. 'unregistered_skill' for skills captured by header-trust fallback,
    not by registry match. Allows downstream scoring to weight appropriately."""


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

    inferred: bool = False
    """True when degree level was inferred from context (university name + duration), not stated explicitly."""

    basis: str = ""
    """How the degree was determined: 'explicit_degree' or 'university_study_pattern'."""

    confidence: float = 1.0
    """Confidence: 1.0 for explicit degree keywords, 0.85 for university-pattern inference."""

    attendance_years: str = ""
    """Study period as a string, e.g. '2016–2020'.  Empty when not determinable."""

    supporting_evidence: list[str] = field(default_factory=list)
    """Raw CV text snippets that support this education entry (up to 3)."""


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

    risk_flag: str = ""
    """Non-empty for inferred evidence with special risk profiles.
    E.g. 'unregistered_soft_skill' for header-extracted items,
    'semantic_inferred' for semantic-similarity fallback matches."""


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

_EXTRACTOR_VERSION = "1.5.0"
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
    ("Microsoft Word",       (r"\bmicrosoft\s+word\b",  r"\bms\s+word\b",  r"\bword\b",  r"\bوورد\b")),
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
# University / higher-education institution indicators
# ---------------------------------------------------------------------------
_UNIVERSITY_INDICATORS = re.compile(
    r"\b(university|universities|université|universidad|universität"
    r"|college|colleges|institute\s+of\s+technology|polytechnic"
    r"|academy|académie|school\s+of\s+(?:business|engineering|law|medicine|science)"
    r"|جامعة|كلية|معهد\s+تقني?|أكاديمية)\b",
    re.IGNORECASE | re.UNICODE,
)

# Excludes training centres, bootcamps, and short courses from university inference
_TRAINING_EXCLUSIONS = re.compile(
    r"\b(training|bootcamp|boot\s+camp|online\s+course|mooc|coursera|udemy"
    r"|edx|udacity|workshop|seminar|certificate\s+course|short\s+course"
    r"|دورة\s+تدريبية|مركز\s+تدريب)\b",
    re.IGNORECASE | re.UNICODE,
)

# Year range covering a typical 3-6 year study duration
_YEAR_RANGE = re.compile(
    r"\b((?:19|20)\d{2})\s*[-–—/]\s*((?:19|20)\d{2})\b"
)

# EDU-02: field-of-study extraction — matches "in <Field Name>" after a degree keyword.
# Search is intentionally narrow (within ~100 chars of the degree keyword) to avoid
# accidentally matching "in <something>" from a later experience or skills section.
# Colon is in the lookahead so "Finance and Banking:" terminates correctly.
_FIELD_AFTER_IN_RE = re.compile(
    r"\bin\s+([A-Za-z؀-ۿ][A-Za-z؀-ۿ\s&\-']{2,55}?)"
    r"(?=\s*(?:[-–—,:;\n(]|from\b|at\b|$))",
    re.IGNORECASE | re.UNICODE,
)

# EDU-02.1: Arabic field extraction — field follows degree keyword directly (no "in").
# Matches "بكالوريوس إدارة أعمال" → "إدارة أعمال"
_AR_FIELD_AFTER_DEGREE_RE = re.compile(
    r"(?:بكالوريوس|ليسانس|ماجستير|دكتوراه|دبلوم)\s+([؀-ۿ][؀-ۿ\s]{2,60}?)"
    r"(?=\s*(?:\n|,|،|$))",
    re.UNICODE,
)

# EDU-02: a line that contains ONLY a year or year-range (used to skip years in field extraction)
_YEAR_ONLY_LINE_RE = re.compile(
    r"^\s*(?:(?:19|20)\d{2})(?:\s*[-–—/]\s*(?:19|20)\d{2})?\s*$"
)

# EDU-02: common CV section headers to skip during adjacent-line search
_SECTION_HEADER_RE = re.compile(
    r"^\s*(?:education|experience|skills|work\s+history|employment"
    r"|summary|objective|profile|certifications?)\s*:?\s*$",
    re.IGNORECASE,
)

# EDU-02.1: single graduation year when no full year range is present.
# Matches "Graduated 2024", "Expected Graduation 2025", or a year alone on its line.
_GRAD_YEAR_RE = re.compile(
    r"\b(?:graduated|graduation|expected\s+graduat\w*|class\s+of|تخرج)\s*((?:19|20)\d{2})\b"
    r"|\b((?:19|20)\d{2})\s*(?:\n|$)",
    re.IGNORECASE | re.MULTILINE | re.UNICODE,
)

# EDU-02.1: detects a ", <short suffix>" that is a city/location, not part of the
# institution name.  Strip only when the suffix contains no university indicator.
_CITY_SUFFIX_RE = re.compile(r",\s*[A-Za-z؀-ۿ][A-Za-z؀-ۿ\s\-]{0,35}$")

# EDU-02.2: "Bachelor of Business Administration" → "Business Administration".
# Applied only to the same line as the degree keyword (not subsequent lines) to
# prevent "University of Jordan" on the next line from being captured as field.
_FIELD_AFTER_OF_RE = re.compile(
    r"\bof\s+([A-Za-z؀-ۿ][A-Za-z؀-ۿ\s&\-']{2,55}?)"
    r"(?=\s*(?:[-–—,:;\n|(]|from\b|at\b|$))",
    re.IGNORECASE | re.UNICODE,
)

# EDU-02.2: degree-line detector used to prevent education entries from leaking
# into experience role_title / employer fields.
_EDU_DEGREE_LINE_RE = re.compile(
    r"\b(bachelor(?:'?s)?|b\.?\s*sc\.?|b\.?\s*a\.?|b\.?\s*eng\."
    r"|master(?:'?s|\s+(?:of|in|degree))|m\.?\s*sc\.?|mba|m\.?\s*b\.?\s*a\."
    r"|ph\.?\s*d\.?|doctorate|diploma"
    r"|بكالوريوس|ليسانس|ماجستير|دكتوراه|دبلوم)\b",
    re.IGNORECASE | re.UNICODE,
)

# ---------------------------------------------------------------------------
# Education level patterns — most specific first
# ---------------------------------------------------------------------------
_EDU_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("PhD",        re.compile(r"\b(ph\.?\s*d\.?|doctorate|doctoral|دكتوراه)\b",                    re.IGNORECASE | re.UNICODE)),
    # EDU-02.2: require possessive/plural OR a degree preposition to prevent
    # "master the process" / "master sales skills" from matching.
    # Match ASCII apostrophe (U+0027) and curly/right single quote (U+2019) for degree names.
    # Use character class with both apostrophe characters via Unicode escapes.
    ("Master\x27s",   re.compile("\\b(master(?:[\x27\u2019]?s|\\s+(?:of|in|degree))|m\\.?\\s*sc\\.?|mba|m\\.?\\s*b\\.?\\s*a\\.?|m\\.?\\s*a\\.?|m\\.?\\s*eng\\.?|ماجستير)\\b", re.IGNORECASE | re.UNICODE)),
    ("Bachelor\x27s", re.compile("\\b(bachelor(?:[\x27\u2019]?s)?|b\\.?\\s*sc\\.?|b\\.?\\s*a\\.?|b\\.?\\s*eng\\.?|بكالوريوس|ليسانس)\\b", re.IGNORECASE | re.UNICODE)),
    ("Diploma",    re.compile(r"\b(diploma|hnd|higher\s+national\s+diploma|دبلوم)\b",              re.IGNORECASE | re.UNICODE)),
    ("Associate",  re.compile(r"\b(associate\s+degree)\b",                                          re.IGNORECASE | re.UNICODE)),
    ("High School",re.compile(r"\b(high\s+school|secondary\s+school|gcse|baccalaureate|a-levels?|o-levels?|ثانوية\s+عامة|شهادة\s+ثانوية)\b", re.IGNORECASE | re.UNICODE)),
)

# In-progress education patterns: "X student specializing in Y" or "Computer Science student"
_IN_PROGRESS_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("Bachelor's", re.compile(r"\b(bachelor\s+|undergrad\w+\s+|computer\s+science|engineering|business|information\s+technology)\s+student\b", re.IGNORECASE | re.UNICODE)),
    ("Master's",   re.compile(r"\b(master\s+|graduate|postgrad\w+)\s+student\b", re.IGNORECASE | re.UNICODE)),
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

# ── Semantic reference sentences for Mechanism B (similarity-based fallback) ──
# Used when regex patterns don't match but semantic evidence suggests a skill.
# One clear description per category; scores >= 0.50 trigger extraction.
_SOFT_SKILL_SEMANTIC_REFS: dict[str, str] = {
    "leadership": "supervising, leading, or managing a team or project",
    "communication": "communicating, presenting, or coordinating with colleagues, clients, or departments",
    "teamwork": "working collaboratively with others, supporting team efforts, or contributing to group projects",
    "problem_solving": "identifying issues, troubleshooting, finding solutions, or resolving conflicts",
    "time_management": "organizing, scheduling, prioritizing multiple tasks, or meeting deadlines",
    "adaptability": "adapting to change, working in fast-paced environments, or learning quickly",
    "confidentiality": "handling sensitive or confidential information with discretion and care",
    "professional_ethics": "maintaining professional standards, integrity, or ethical conduct",
    "organizational_ability": "organizing documents, systems, or processes for efficiency and accuracy",
}

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
        ("confidentiality",               [r"\bconfidentiality\b",              r"\bnon-disclosure\b",    r"\bnda\b",  r"\bسرية\b"]),
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
    "soft_skills": re.compile(
        r"^(?:soft\s+)?(?:skills?|interpersonal|behavioral|personal\s+qualities|attributes"
        r"|المهارات\s+الناعمة|مهارات\s+ناعمة)",
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

# Month pattern for handling "Month Year" date formats (e.g. "April 2021", "Feb 2020").
# Supports English month names (full and abbreviated) and Arabic month names.
_MONTH_PATTERN = (
    r"(?:"
    r"january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan\.?|feb\.?|mar\.?|apr\.?|may|jun\.?|jul\.?|aug\.?|sep\.?|oct\.?|nov\.?|dec\.?"
    r"|يناير|فبراير|مارس|أبريل|مايو|يونيو|يوليو|أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر"
    r"|كانون\s+الثاني|شباط|آذار|نيسان|أيار|حزيران|تموز|آب|أيلول|تشرين\s+الأول|تشرين\s+الثاني"
    r")"
)

# Date range pattern for experience year extraction.
# Supports both "Month Year – Month Year" (e.g. "April 2021 – February 2025") and
# "Year – Year" (e.g. "2021 – 2025") formats. Also supports English and Arabic
# separators and many "present" variants.
_DATE_RANGE_RE = re.compile(
    r"(?:من\s+)?"                                       # optional Arabic "from" prefix
    r"(?:"
        # Format 1: "Month Year – Month Year" (e.g. "April 2021 – February 2025")
        r"(?P<start_month>" + _MONTH_PATTERN + r")\s+"
        r"(?P<start_year>(?:19|20)\d{2})"
    r"|"
        # Format 2: "Year – Year" (e.g. "2021 – 2025") - original format
        r"\b(?P<start_year_bare>(?:19|20)\d{2})\b"
    r"|"
        # Format 3: "MM/YYYY – MM/YYYY" or "MM.YYYY – MM.YYYY" (e.g. "11/2023 - 11/2025")
        r"(?P<start_month_numeric>\d{1,2})(?:[/.])"
        r"(?P<start_year_numeric>(?:19|20)\d{2})"
    r")"
    r"\s*(?:[-–—/]|\bto\b|\bإلى\b|\bحتى\b)\s*"        # separator (English or Arabic)
    r"(?P<end>"                                          # end: year or present variant
        # Allow optional month before end year (for both word and numeric formats)
        r"(?:(?:\d{1,2})[/.])?(?:(?:" + _MONTH_PATTERN + r")\s+)?"
        r"(?:(?:19|20)\d{2})"                            # 4-digit year
    r"|present|current|now|ongoing"                     # English present
    r"|till\s+date|to\s+date"                           # "till/to date"
    r"|حاليا|حالياً|الآن"                             # Arabic "now"
    r"|حتى\s+الآن|إلى\s+الآن"                        # Arabic "until now"
    r"|الوقت\s+الحاضر|الحالي"                         # Arabic "the present"
    r")",
    re.IGNORECASE | re.UNICODE,
)

# Regex to recognise "present" variants — used when parsing the end group.
_PRESENT_RE = re.compile(
    r"^(?:present|current|now|ongoing|till\s+date|to\s+date|"
    r"حاليا|حالياً|الآن|حتى\s+الآن|إلى\s+الآن|"
    r"الوقت\s+الحاضر|الحالي)$",
    re.IGNORECASE | re.UNICODE,
)

# Explicit total-experience statements, e.g. "10 years experience" / "over 10 years" /
# "10+ years of relevant experience" / "10 سنوات خبرة" / "أكثر من 10 سنوات خبرة".
# Group 1 captures the numeric value.
_EXPLICIT_EXP_RE = re.compile(
    r"(?:(?:over|more\s+than|above|approximately|around|about|"
    r"أكثر\s+من|حوالي|نحو)\s+)?"           # optional quantifier prefix
    r"\b(\d{1,2}(?:\.\d)?)\+?\s*"           # number (1-2 digits, optional +)
    r"(?:years?|yrs?|سنوات?|عاما?|أعوام|سنة)"  # year word
    r"(?:['\s]*of)?\s*(?:relevant\s+|work\s+)?(?:experience|خبرة|خبرات)",
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
        "skills": [], "soft_skills": [], "experience": [], "education": [],
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
    """Extract skills with priority: skills section > experience > other.

    Fallback: if no skills found but there's a block of short lines after education
    (likely an unlabeled skill list), extract known skills from those lines.
    """
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

    # ── MECHANISM A: Explicit-header trust (unregistered skills) ───────────────────
    # After closed-set registry pass, capture skills under explicit "Skills" header
    # that weren't matched by the registry. These get lower confidence (0.55) and are
    # flagged "unregistered_skill" so downstream can distinguish them from known terms.
    skills_section_lines = sections.get("skills", [])
    for line in skills_section_lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        # Skip if already found by registry
        if any(skill.raw_text == line_stripped for skill in found.values()):
            continue
        # Skip labeled-category lines like "Programming: Python, C++" or
        # "Machine Learning: item1, item2" — the pattern "Label: comma-sep items"
        # is already well-served by registry matching on the individual items.
        # Detection: line has colon AND contains comma after colon (or is just the label).
        if ":" in line_stripped:
            colon_pos = line_stripped.find(":")
            after_colon = line_stripped[colon_pos + 1:].strip()
            # If after colon is empty or comma-separated list, skip the whole line
            if not after_colon or "," in after_colon:
                continue

        # Only capture lines that look like actual skill items:
        # - Are reasonably short (< 100 chars, avoids full sentences/paragraphs)
        # - Start with bullet/dash/number pattern OR are very short (< 40 chars)
        # This prevents capturing prose paragraphs as skill names
        bullet_pattern = r"^[\s•\-*#\d\.]+\s*"
        is_bullet = bool(re.match(bullet_pattern, line))
        is_short = len(line_stripped) < 40
        is_reasonable_length = len(line_stripped) < 100

        if (is_bullet or is_short) and is_reasonable_length:
            # Clean up the skill name by removing leading bullets/whitespace
            skill_name = re.sub(bullet_pattern, "", line_stripped)
            if skill_name:  # Only add if something remains after cleanup
                found[skill_name] = SkillEvidence(
                    skill_name=skill_name,
                    raw_text=line_stripped,
                    explicit=True,
                    confidence=0.55,
                    context_snippet=line_stripped[:200],
                    language="ar" if _is_arabic_match(skill_name) else "en",
                    section_hint="skills",
                    inference_basis="",
                    risk_flag="unregistered_skill",
                )

    # Fallback: if no skills found but "other" section has short lines after education,
    # treat them as unlabeled skills. Common pattern: education line followed by bullets.
    if not found and sections.get("other"):
        other_lines = sections.get("other", [])
        # Look for sequences of short lines (< 50 chars) that don't contain typical
        # job verbs (managed, designed, led, etc.) - these are likely skills.
        job_verbs = r"\b(managed|designed|led|developed|implemented|coordinated|led|supervised|oversaw|founded|resolved|handled|organized)\b"
        short_bullet_lines = [
            line for line in other_lines
            if 3 <= len(line) < 50 and not re.search(job_verbs, line, re.IGNORECASE)
        ]

        if short_bullet_lines:
            # Reconstruct as potential skills section
            potential_skills_text = "\n".join(short_bullet_lines)
            for skill_name, compiled_patterns in _COMPILED_SKILLS:
                if skill_name in found:
                    continue
                for pat in compiled_patterns:
                    m = pat.search(potential_skills_text)
                    if m:
                        raw = m.group(0)
                        found[skill_name] = SkillEvidence(
                            skill_name=skill_name,
                            raw_text=raw,
                            explicit=True,
                            confidence=0.70,
                            context_snippet=_get_context_snippet(potential_skills_text, m),
                            language="ar" if _is_arabic_match(raw) else "en",
                            section_hint="other",
                            inference_basis="",
                        )
                        break

    return list(found.values())


def _parse_edu_fields_from_window(
    window: str,
    indicator_pos_in_window: int,
) -> tuple[str, str]:
    """Extract (institution, field_of_study) from a university-indicator window.

    Handles both inline formats (slash/comma-separated) and multi-line blocks.
    Returns ("", "") when extraction is unreliable.  Never raises.
    """
    lines = window.splitlines()
    if not lines:
        return "", ""

    # ── Locate the line containing the university indicator ───────────────────
    pos = 0
    inst_line_idx = 0
    for i, ln in enumerate(lines):
        if pos <= indicator_pos_in_window <= pos + len(ln):
            inst_line_idx = i
            break
        pos += len(ln) + 1  # +1 for the consumed newline

    inst_line = lines[inst_line_idx].strip() if inst_line_idx < len(lines) else ""

    institution = ""
    field_of_study = ""

    # ── Strategy A: slash / pipe separated (e.g. "Degree | Uni" or "Uni / Field") ─
    # EDU-02.2: prefer the part that contains a university indicator as institution;
    # extract field from the remaining part using "of/in" patterns.
    if "/" in inst_line or "|" in inst_line:
        parts = [p.strip() for p in re.split(r"[/|]", inst_line)]
        non_year = [p for p in parts if p and not _YEAR_ONLY_LINE_RE.match(p)
                    and not _YEAR_RANGE.fullmatch(p)]
        uni_parts = [p for p in non_year if _UNIVERSITY_INDICATORS.search(p)]
        non_uni = [p for p in non_year if p not in uni_parts]
        if uni_parts:
            institution = uni_parts[0][:100]
            if non_uni:
                candidate = non_uni[0]
                fo_m = _FIELD_AFTER_OF_RE.search(candidate)
                fi_m = _FIELD_AFTER_IN_RE.search(candidate)
                if fo_m:
                    field_of_study = fo_m.group(1).strip()[:80]
                elif fi_m:
                    field_of_study = fi_m.group(1).strip()[:80]
                elif not _EDU_DEGREE_LINE_RE.search(candidate):
                    field_of_study = candidate[:80]
        elif non_year:
            institution = non_year[0][:100]
            if len(non_year) >= 2:
                field_of_study = non_year[1][:80]

    # ── Strategy B: comma-separated with year visible ─────────────────────────
    elif "," in inst_line and _YEAR_RANGE.search(inst_line):
        parts = [p.strip() for p in inst_line.split(",")]
        non_year = [p for p in parts if p and not _YEAR_ONLY_LINE_RE.match(p)
                    and not _YEAR_RANGE.search(p)]
        uni_parts = [p for p in non_year if _UNIVERSITY_INDICATORS.search(p)]
        other_parts = [p for p in non_year if p not in uni_parts]
        institution = uni_parts[0][:100] if uni_parts else (non_year[0][:100] if non_year else "")
        field_of_study = other_parts[0][:80] if other_parts else ""

    # ── Strategy C: institution is the whole line (strip trailing years/punct) ─
    else:
        yr_m = _YEAR_RANGE.search(inst_line)
        institution = (inst_line[:yr_m.start()].strip(" ,–—/") if yr_m else inst_line).strip()[:100]

    # ── Fallback: look at adjacent non-year lines for field of study ──────────
    if not field_of_study:
        non_empty = [(i, ln.strip()) for i, ln in enumerate(lines) if ln.strip()]
        ne_inst = next((ni for ni, (i, _) in enumerate(non_empty) if i == inst_line_idx), -1)

        for ni, (_, ln) in enumerate(non_empty):
            if ni == ne_inst:
                continue
            if abs(ni - ne_inst) > 4:
                break
            if _YEAR_ONLY_LINE_RE.match(ln):
                continue
            if _SECTION_HEADER_RE.match(ln):
                continue
            if _UNIVERSITY_INDICATORS.search(ln):
                continue
            if _TRAINING_EXCLUSIONS.search(ln):
                continue
            cleaned = _YEAR_RANGE.sub("", ln).strip(" ,–—/").strip()
            if len(cleaned) > 2:
                field_of_study = cleaned[:80]
                break

    return institution, field_of_study


def _infer_university_bachelor(full_text: str) -> EducationEvidence | None:
    """Infer a Bachelor's degree from university name + study duration (3-6 years).

    Returns an EducationEvidence with inferred=True and confidence=0.85 when:
      - A university/college/institute indicator is found in a 300-char window
      - A year range spanning 3-6 years appears nearby
      - The window does NOT match training/bootcamp exclusions

    Returns None when no qualifying pattern is found.
    """
    for m_uni in _UNIVERSITY_INDICATORS.finditer(full_text):
        # Check 300 chars either side of the university mention
        win_start = max(0, m_uni.start() - 50)
        win_end = min(len(full_text), m_uni.end() + 250)
        window = full_text[win_start:win_end]

        if _TRAINING_EXCLUSIONS.search(window):
            continue

        yr_m = _YEAR_RANGE.search(window)
        if not yr_m:
            continue

        try:
            year_start = int(yr_m.group(1))
            year_end = int(yr_m.group(2))
        except (ValueError, IndexError):
            continue

        duration = year_end - year_start
        if not (3 <= duration <= 6):
            continue

        # EDU-02: extract institution, field_of_study, and attendance_years
        indicator_pos_in_window = m_uni.start() - win_start
        institution, field_of_study = _parse_edu_fields_from_window(window, indicator_pos_in_window)
        raw = window.strip()[:200]

        return EducationEvidence(
            degree_level="Bachelor's",
            field_of_study=field_of_study,
            institution=institution,
            year=year_end,
            raw_text=raw,
            inferred=True,
            basis="university_study_pattern",
            confidence=0.85,
            attendance_years=f"{year_start}–{year_end}",
            supporting_evidence=[raw] if raw else [],
        )

    return None


def _extract_institution_from_line(line: str, indicator_pos: int) -> str:
    """Extract institution name from the portion of a line that contains the university indicator.

    Scans backward from `indicator_pos` for a natural delimiter (colon, comma,
    semicolon, opening parenthesis, or space–dash–space), then forward for
    the next comma or parenthesis.  Never raises; returns "" on empty input.
    """
    if not line or indicator_pos < 0:
        return ""

    # Scan backward for start boundary
    start = 0
    i = min(indicator_pos - 1, len(line) - 1)
    while i >= 0:
        ch = line[i]
        if ch in ":,;(،|":  # colon, comma, semicolon, paren, Arabic comma, pipe
            start = i + 1
            break
        # Dash with surrounding space: " - " acts as a section separator
        if ch == "-" and i > 0 and line[i - 1] in " \t":
            start = i + 1
            break
        i -= 1

    # Scan forward for end boundary (comma or opening paren after the indicator)
    end = len(line)
    for ch in (",", "("):
        p = line.find(ch, indicator_pos)
        if p != -1 and p < end:
            end = p

    return line[start:end].strip()[:100]


def _strip_city_suffix(institution: str) -> str:
    """Remove a trailing ', <city/location>' from an institution name.

    Only strips when the text after the comma contains no university-indicator
    word (so "Birzeit University, Ramallah" → "Birzeit University" but
    "University of Science and Technology, Engineering" is left unchanged).
    """
    if "," not in institution:
        return institution
    pre, post = institution.split(",", 1)
    post_clean = post.strip()
    if not _UNIVERSITY_INDICATORS.search(post_clean):
        return pre.strip()
    return institution


def _is_likely_location_line(line: str) -> bool:
    """Detect if a line is primarily ONLY a location/geography (city, country, city-country).

    Used to handle CV formats where experience entries are split across multiple lines:
      Title / Company / Location / Date

    Returns True only if line is SHORT (2-3 words) and contains ONLY geographic info,
    not if it contains company/job info like "ABC Company, Cairo".
    """
    line = line.strip().lower()
    if not line:
        return False

    word_count = len(line.split())

    # A pure location line is typically VERY short: just city, or "city, country"
    # Should have <= 3 words to exclude lines like "ABC Company, Cairo" (4 words)
    if word_count > 3:
        return False

    # Common geographic indicators
    location_keywords = {
        "palestine", "israel", "egypt", "jordan", "lebanon", "syria", "iraq",
        "saudi arabia", "uae", "united arab emirates", "qatar", "bahrain", "kuwait",
        "oman", "yemen", "tunisia", "algeria", "morocco", "sudan", "libya",
        "cairo", "alexandria", "giza", "ramallah", "jericho", "bethlehem", "nablus",
        "amman", "beirut", "damascus", "baghdad", "riyadh", "dubai", "abu dhabi",
        "doha", "manama", "muscat", "sanaa", "tunis", "algiers", "casablanca",
        "khartoum", "tripoli", "london", "paris", "berlin", "new york", "los angeles",
        "usa", "uk", "us", "france", "germany", "canada", "australia", "singapore",
    }

    # Check if line contains ANY geographic keywords
    has_location_keyword = any(keyword in line for keyword in location_keywords)

    if not has_location_keyword:
        return False

    # Check for patterns like "City, Country" or just "Country" or just "City"
    if ", " in line:
        parts = line.split(",")
        # If two parts and BOTH contain location keywords or are very short
        if len(parts) == 2:
            left = parts[0].strip().lower()
            right = parts[1].strip().lower()
            # Both parts should be short (location names) and neither should be a company
            left_has_location = any(kw in left for kw in location_keywords)
            right_has_location = any(kw in right for kw in location_keywords)
            # Reject if it contains company/job indicators
            company_indicators = {"company", "corp", "ltd", "inc", "group", "agency",
                                 "consulting", "services", "solutions"}
            has_company_words = any(ind in line for ind in company_indicators)
            if has_company_words:
                return False
            if left_has_location or right_has_location:
                return True  # Likely "City, Country" or "City, City" format
        return False

    # Standalone location like "Ramallah" or "Cairo"
    # Check that the line doesn't contain company/job words
    job_keywords = {"engineer", "developer", "analyst", "manager", "specialist",
                   "coordinator", "officer", "director", "consultant", "lead",
                   "associate", "supervisor", "representative", "support", "company",
                   "corp", "ltd", "inc"}
    has_job_words = any(keyword in line for keyword in job_keywords)
    if has_job_words:
        return False

    # Short line with only a location keyword
    return has_location_keyword


def _infer_in_progress_education(full_text: str) -> EducationEvidence | None:
    """Infer education from "X student" patterns (e.g. "Computer Science student").

    Returns an EducationEvidence with inferred=True and confidence=0.75 when a
    student pattern is found. Returns None otherwise.
    """
    for level, pat in _IN_PROGRESS_PATTERNS:
        m = pat.search(full_text)
        if m:
            ctx_start = max(0, m.start() - 50)
            ctx_end = min(len(full_text), m.end() + 100)
            raw = full_text[ctx_start:ctx_end].strip()[:200]

            # Try to extract field of study from context
            field_match = re.search(
                r"(?:specializ\w+\s+in|studying|major\s+(?:in|of))\s+([^,.\n]+)",
                raw,
                re.IGNORECASE
            )
            field_of_study = field_match.group(1).strip()[:80] if field_match else ""

            return EducationEvidence(
                degree_level=level,
                field_of_study=field_of_study,
                institution="",
                year=None,
                raw_text=raw,
                inferred=True,
                basis="student_pattern",
                confidence=0.75,
                attendance_years="",
                supporting_evidence=[raw] if raw else [],
            )
    return None


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

            # Wide context for institution and year lookups (up to 300 chars after degree)
            wide_start = max(0, m.start() - 30)
            wide_end = min(len(full_text), m.end() + 300)
            wide_ctx = full_text[wide_start:wide_end]

            # ── Field of study ────────────────────────────────────────────────
            # EDU-02.1: search a NARROW window (degree keyword → +100 chars) only,
            # so we never accidentally match "in <something>" from a later section.
            field_of_study = ""
            field_ctx = full_text[m.start() : min(len(full_text), m.end() + 100)]
            fi_m = _FIELD_AFTER_IN_RE.search(field_ctx)
            if fi_m:
                field_of_study = fi_m.group(1).strip()[:80]
            # Fallback: Arabic degree keyword followed directly by Arabic field
            if not field_of_study:
                ar_m = _AR_FIELD_AFTER_DEGREE_RE.search(field_ctx)
                if ar_m:
                    field_of_study = ar_m.group(1).strip()[:80]
            # EDU-02.2: "Bachelor of Business Administration" → "Business Administration".
            # Restricted to the first line of field_ctx to avoid capturing "University of X"
            # on subsequent lines (which would produce the city/country as field).
            if not field_of_study:
                first_line = field_ctx.split("\n")[0]
                fo_m = _FIELD_AFTER_OF_RE.search(first_line)
                if fo_m:
                    field_of_study = fo_m.group(1).strip()[:80]

            # ── Institution ───────────────────────────────────────────────────
            institution = ""
            degree_pos_in_wide = m.start() - wide_start
            uni_m = _UNIVERSITY_INDICATORS.search(wide_ctx)
            if uni_m:
                between = wide_ctx[min(degree_pos_in_wide, uni_m.start()):
                                   max(degree_pos_in_wide, uni_m.end())]
                # EDU-02.1: handle SAME-LINE institution (e.g. "B.Sc in X: Uni, City")
                nl_before = wide_ctx.rfind("\n", 0, uni_m.start())
                nl_after = wide_ctx.find("\n", uni_m.end())
                if "\n" in between:
                    # Multi-line: institution is on a separate line
                    inst_line = wide_ctx[
                        nl_before + 1 : nl_after if nl_after != -1 else len(wide_ctx)
                    ].strip()
                    institution = _strip_city_suffix(inst_line[:100])
                else:
                    # Same-line: extract institution name from around the indicator
                    same_line = wide_ctx[
                        nl_before + 1 : nl_after if nl_after != -1 else len(wide_ctx)
                    ]
                    indicator_pos_in_line = uni_m.start() - (nl_before + 1)
                    institution = _extract_institution_from_line(same_line, indicator_pos_in_line)

            # ── Attendance years ──────────────────────────────────────────────
            attendance_years = ""
            yr_m = _YEAR_RANGE.search(wide_ctx)
            if yr_m:
                try:
                    attendance_years = f"{yr_m.group(1)}–{yr_m.group(2)}"
                except IndexError:
                    pass
            # EDU-02.1: fallback to single graduation year if no range found
            if not attendance_years:
                narrow_yr_ctx = full_text[m.start() : min(len(full_text), m.end() + 200)]
                gy_m = _GRAD_YEAR_RE.search(narrow_yr_ctx)
                if gy_m:
                    year_val = next((g for g in gy_m.groups() if g), None)
                    if year_val:
                        attendance_years = year_val

            found.append(EducationEvidence(
                degree_level=level,
                field_of_study=field_of_study,
                institution=institution,
                year=None,
                raw_text=raw,
                inferred=False,
                basis="explicit_degree",
                confidence=1.0,
                attendance_years=attendance_years,
                supporting_evidence=[raw] if raw else [],
            ))
            level_idx = list(EDUCATION_LEVELS).index(level)
            if level_idx > highest_idx:
                highest_idx = level_idx

    # If no explicit Bachelor's or higher was found, try university-pattern inference
    if highest_idx < list(EDUCATION_LEVELS).index("Bachelor's"):
        inferred = _infer_university_bachelor(full_text)
        if inferred is not None:
            found.append(inferred)
            bachelor_idx = list(EDUCATION_LEVELS).index("Bachelor's")
            if bachelor_idx > highest_idx:
                highest_idx = bachelor_idx

    # If still no education found, check for in-progress education patterns
    if not found:
        in_progress = _infer_in_progress_education(full_text)
        if in_progress is not None:
            found.append(in_progress)
            level_idx = list(EDUCATION_LEVELS).index(in_progress.degree_level)
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
    categories_found: set[str] = set()

    # ── STEP 1: Regex-based extraction (existing behavior) ─────────────────────
    for category, compiled_patterns in _COMPILED_SOFT_SKILLS:
        if category in categories_found:
            continue
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
                categories_found.add(category)
                break  # one signal per category

    # ── MECHANISM A: Explicit-header trust (unregistered soft skills) ──────────
    # After closed-set pattern pass, capture skills under explicit "Soft Skills"
    # header that weren't matched by the patterns. These get lower confidence
    # (0.55) and are flagged "unregistered_soft_skill" for downstream handling.
    soft_skills_section_lines = sections.get("soft_skills", [])
    for line in soft_skills_section_lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Skip labeled-category lines like "Soft Skills: item1, item2"
        # or unlabeled comma-separated lists like "item1, item2, item3"
        # These should be split on commas, with each item treated separately.
        if ":" in line_stripped:
            colon_pos = line_stripped.find(":")
            after_colon = line_stripped[colon_pos + 1:].strip()
            # If after colon is empty or comma-separated list, split and process
            if not after_colon:
                continue  # Empty after colon, skip
            if "," in after_colon:
                # Split comma-separated items and process each
                items = [item.strip() for item in after_colon.split(",")]
                for item in items:
                    if item:
                        inferred_category = "other"
                        for category, compiled_patterns in _COMPILED_SOFT_SKILLS:
                            for pat, _ in compiled_patterns:
                                if pat.search(item):
                                    inferred_category = category
                                    break
                            if inferred_category != "other":
                                break
                        signals.append(SoftSkillSignal(
                            soft_skill_category=inferred_category,
                            evidence_phrase=item,
                            confidence=0.55,
                            inference_basis="",
                            risk_flag="unregistered_soft_skill",
                        ))
                continue  # Move to next line after processing comma-separated items

        # Handle comma-separated lists without a colon (e.g. "Skill1, Skill2, Skill3")
        if "," in line_stripped:
            # Split on commas and process each item individually
            bullet_pattern = r"^[\s•\-*#\d\.]+\s*"
            line_no_bullet = re.sub(bullet_pattern, "", line_stripped)
            items = [item.strip() for item in line_no_bullet.split(",")]
            for item in items:
                if item and len(item) < 100:
                    inferred_category = "other"
                    for category, compiled_patterns in _COMPILED_SOFT_SKILLS:
                        for pat, _ in compiled_patterns:
                            if pat.search(item):
                                inferred_category = category
                                break
                        if inferred_category != "other":
                            break
                    signals.append(SoftSkillSignal(
                        soft_skill_category=inferred_category,
                        evidence_phrase=item,
                        confidence=0.55,
                        inference_basis="",
                        risk_flag="unregistered_soft_skill",
                    ))
            continue  # Move to next line after processing comma-separated items

        # Only capture non-comma-separated lines that look like actual skill items:
        # - Are reasonably short (< 100 chars, avoids full sentences/paragraphs)
        # - Start with bullet/dash/number pattern OR are very short (< 40 chars)
        bullet_pattern = r"^[\s•\-*#\d\.]+\s*"
        is_bullet = bool(re.match(bullet_pattern, line))
        is_short = len(line_stripped) < 40
        is_reasonable_length = len(line_stripped) < 100

        if (is_bullet or is_short) and is_reasonable_length:
            # Clean up the skill name by removing leading bullets/whitespace
            skill_name = re.sub(bullet_pattern, "", line_stripped)
            if skill_name:
                # Try to infer category from the skill name using existing patterns
                inferred_category = "other"
                for category, compiled_patterns in _COMPILED_SOFT_SKILLS:
                    for pat, _ in compiled_patterns:
                        if pat.search(skill_name):
                            inferred_category = category
                            break
                    if inferred_category != "other":
                        break

                signals.append(SoftSkillSignal(
                    soft_skill_category=inferred_category,
                    evidence_phrase=skill_name,
                    confidence=0.55,
                    inference_basis="",
                    risk_flag="unregistered_soft_skill",
                ))

    # ── MECHANISM B: Semantic-similarity fallback (narrative-inferred soft skills) ──
    # For CVs without explicit soft-skills headers, scan narrative text (experience,
    # summary) for evidence that doesn't match hardcoded patterns. Use semantic
    # similarity to infer soft skills with lower confidence (0.35-0.40) and
    # risk_flag="semantic_inferred".
    try:
        from services.local_processor import compute_semantic_similarity
    except Exception:
        # If semantic model is not available (import error, network error, etc),
        # skip Mechanism B and return regex-only and header-based signals
        return signals

    # Only apply Mechanism B if there's no explicit soft-skills header
    # (otherwise Mechanism A handles those)
    if soft_skills_section_lines:
        return signals

    # Scan narrative text for unmatched evidence
    narrative_sections = ["experience", "summary"]
    narrative_lines = []
    for sec in narrative_sections:
        for line in sections.get(sec, []):
            line_stripped = line.strip()
            if line_stripped and len(line_stripped) > 10 and len(line_stripped) < 200:
                narrative_lines.append(line_stripped)

    # For each narrative line, test semantic similarity against reference sentences
    semantic_threshold = 0.50
    confidence_semantic = 0.38  # Between 0.35-0.40

    for line in narrative_lines:
        # Skip lines that already matched a regex pattern
        already_matched = any(
            sig for sig in signals
            if sig.evidence_phrase.lower() in line.lower()
        )
        if already_matched:
            continue

        # Compute similarity to each soft-skill reference sentence
        for category, ref_sentence in _SOFT_SKILL_SEMANTIC_REFS.items():
            # Skip if this category already found by regex
            if category in categories_found:
                continue

            try:
                # Compute semantic similarity
                similarity = compute_semantic_similarity(line, ref_sentence)
            except Exception:
                # If similarity computation fails (network, model load, etc), skip
                continue

            if similarity >= semantic_threshold:
                signals.append(SoftSkillSignal(
                    soft_skill_category=category,
                    evidence_phrase=line,
                    confidence=confidence_semantic,
                    inference_basis=f"semantic similarity: {similarity:.2f}",
                    risk_flag="semantic_inferred",
                ))
                categories_found.add(category)
                break  # One signal per category

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
    """Return (experience_blocks, total_years).

    Three-pass strategy:
      1. Extract date ranges from experience section (or full text as fallback).
      2. Detect explicit statements like "10 years experience" in summary +
         experience sections — used as a lower-bound override when date
         arithmetic under-counts (e.g. dates in unsupported formats).
      3. Clamp total to 50 years.
    """
    exp_section = "\n".join(sections.get("experience", []))
    search_text = exp_section if exp_section.strip() else full_text

    total_years = 0.0
    blocks: list[ExperienceEvidence] = []

    for m in _DATE_RANGE_RE.finditer(search_text):
        # Extract start year from any format: "Month Year" or "Year" or "MM/YYYY"
        start_year_str = (
            m.group("start_year")
            or m.group("start_year_bare")
            or m.group("start_year_numeric")
        )
        start_year = int(start_year_str)
        end_str = m.group("end").strip()

        if _PRESENT_RE.match(end_str):
            end_year = _CURRENT_YEAR
        else:
            # Extract the 4-digit year from the end group (may have month prefix)
            year_match = re.search(r"(?:19|20)\d{2}", end_str)
            if year_match:
                end_year = int(year_match.group(0))
            else:
                continue

        if end_year < start_year or (end_year - start_year) > 50:
            continue

        years = float(end_year - start_year)

        # Attempt to extract role/employer from the lines preceding and around the date
        ctx_start = max(0, m.start() - 200)
        ctx_end = min(len(search_text), m.end() + 100)
        context_text = search_text[ctx_start:ctx_end]
        all_lines = context_text.splitlines()

        role_title = ""
        employer = ""

        # Find which line contains the date match to detect pipe-delimited format
        date_line_idx = -1
        for i, line in enumerate(all_lines):
            if m.group() in line:
                date_line_idx = i
                break

        if date_line_idx >= 0:
            date_line = all_lines[date_line_idx].strip()
            # ONLY apply special pipe handling if the line contains BOTH date and pipes
            # This handles "Company | Dates | Location" format
            if " | " in date_line:
                # Pipe-delimited format: extract company before first pipe
                parts = date_line.split(" | ")
                if parts:
                    employer = parts[0].strip()[:100]
                # Role title is from the preceding line
                if date_line_idx >= 1:
                    role_title = all_lines[date_line_idx - 1].strip()[:100]
            # ELSE: use standard logic (date on separate line from role/company)
            else:
                # Standard multi-line format: get preceding lines as if date wasn't there
                preceding = all_lines[:date_line_idx]
                if preceding:
                    last_line = preceding[-1].strip()
                    # Handle the Title / Company / Location format (e.g., Rami's CV)
                    if len(preceding) >= 3 and _is_likely_location_line(last_line):
                        # Format: Title / Company / Location, each on separate line
                        role_title = preceding[-3].strip()[:100]
                        employer = preceding[-2].strip()[:100]
                    else:
                        # Original format: single line or Title / Company without location on own line
                        role_title = preceding[-1].strip()[:100]
                        employer = preceding[-2].strip()[:100] if len(preceding) >= 2 else ""
        else:
            # Fallback if date not found in lines (shouldn't happen with wider context)
            preceding = all_lines
            if preceding:
                last_line = preceding[-1].strip()
                if len(preceding) >= 3 and _is_likely_location_line(last_line):
                    role_title = preceding[-3].strip()[:100]
                    employer = preceding[-2].strip()[:100]
                else:
                    role_title = preceding[-1].strip()[:100]
                    employer = preceding[-2].strip()[:100] if len(preceding) >= 2 else ""

        # EDU-02.2: skip blocks where the "role" line is actually an education entry
        # (e.g. "Bachelor of Business Administration | Al-Quds Open University").
        if _EDU_DEGREE_LINE_RE.search(role_title) or _EDU_DEGREE_LINE_RE.search(employer):
            continue

        ctx_end = min(len(search_text), m.end() + 50)
        raw = search_text[ctx_start:ctx_end].strip()[:300]

        blocks.append(ExperienceEvidence(
            employer=employer,
            role_title=role_title,
            years=years,
            raw_text=raw,
        ))
        total_years += years

    # Pass 2 — explicit statements ("10 years experience", "over 10 years", etc.)
    # Search experience section + summary + other to catch e.g. "10+ years in banking".
    explicit_search = "\n".join([
        search_text,
        "\n".join(sections.get("summary", [])),
        "\n".join(sections.get("other", [])),
    ])
    explicit_max = 0.0
    for em in _EXPLICIT_EXP_RE.finditer(explicit_search):
        try:
            val = float(em.group(1))
            if 1.0 <= val <= 45.0:
                explicit_max = max(explicit_max, val)
        except (ValueError, IndexError):
            pass

    # Use explicit statement as lower bound — prevents under-counting from
    # unsupported date formats while never inflating a correctly-parsed sum.
    total_years = max(total_years, explicit_max)
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
