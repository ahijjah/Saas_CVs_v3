"""
Deterministic heuristic validator for job descriptions.

Used before queuing AI/LLM criteria extraction to avoid wasting tokens
on meaningless or placeholder descriptions.

Returns a DescriptionQuality dataclass with score (0-100), state
('insufficient' | 'needs_improvement' | 'ready'), issues, and suggestions.

Supports English, Arabic, and mixed text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# ── Placeholder / test text ────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(
    r"^(test\d*|sample|demo|placeholder|temp|hello|hi|n/?a|tbd|todo|"
    r"lorem|ipsum|xxx+|aaa+|بيانات\s*تجريبية|نص\s*تجريبي|"
    r"اختبار|تجربة|مثال|نموذج|job\s*description|required|"
    r"we\s+are\s+hiring|مطلوب\s*موظف)\.?$",
    re.IGNORECASE,
)
_REPEAT_CHAR_RE = re.compile(r"(.)\1{3,}")  # 4+ identical consecutive chars

# ── English signals ────────────────────────────────────────────────────────────

_EN_RESPONSIBILITIES = re.compile(
    r"\b(responsibilities|duties|tasks?|manag(?:e[sd]?|es|ing)|"
    r"develop(?:s|ed|ing)?|maintain(?:s|ed|ing)?|support(?:s|ed|ing)?|"
    r"assist(?:s|ed|ing)?|coordinat(?:e[sd]?|es|ing)|lead(?:s|ing)?|"
    r"ensur(?:e[sd]?|es|ing)|provid(?:e[sd]?|es|ing)|creat(?:e[sd]?|es|ing)|"
    r"design(?:s|ed|ing)?|implement(?:s|ed|ing)?|analyz(?:e[sd]?|es|ing)|"
    r"analys(?:e[sd]?|es|ing)|monitor(?:s|ed|ing)?|review(?:s|ed|ing)?|"
    r"deliver(?:s|ed|ing)?|build(?:s|ing)?|writ(?:e[sd]?|es|ing)|"
    r"operat(?:e[sd]?|es|ing)|handl(?:e[sd]?|es|ing)|"
    r"process(?:es|ed|ing)?|prepar(?:e[sd]?|es|ing)|"
    r"report(?:s|ed|ing)?|oversee(?:s|ing|n)?|execut(?:e[sd]?|es|ing)|"
    r"perform(?:s|ed|ing)?|conduct(?:s|ed|ing)?|train(?:s|ed|ing)?|"
    r"respond(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)

_EN_REQUIREMENTS = re.compile(
    r"\b(requirements?|skills?|qualifications?|experience|education|"
    r"degree|bachelor|master|diploma|certificate|proficiency|knowledge|"
    r"familiar(?:ity)?|fluent|fluency|expertise|background|abilit(?:y|ies)|"
    r"able\s+to|must[\s-]have|should[\s-]have|preferred|minimum|required|"
    r"ideally|competenc(?:y|ies)|capable)\b",
    re.IGNORECASE,
)

_EN_JOB_CONTEXT = re.compile(
    r"\b(position|role|job|candidate|applicant|team|department|company|"
    r"organization|office|client|customer|products?|services?|projects?|"
    r"industry|sector|firm|employer|vacancy|hire|hiring|recruit|workforce|"
    r"retail|stores?|shops?|bank|hospital|school|factory|warehouse|hotel|"
    r"restaurant|engineer(?:ing)?|developer|manager|analyst|designer|"
    r"consultant|coordinator|specialist|administrator|director|officer|"
    r"executive|accountant|nurse|teacher|driver|technician)\b",
    re.IGNORECASE,
)

_EN_OPEN_ROLE = re.compile(
    r"\b(open\s+to\s+all|no\s+(?:specific\s+)?(?:degree|experience|"
    r"qualification)|all\s+backgrounds?|training\s+(?:will\s+be\s+)?"
    r"(?:provided|given)|no\s+prior\s+experience|entry[\s-]level\s+welcome|"
    r"fresh\s+graduates?\s+(?:are\s+)?welcome|suitable\s+for\s+all|"
    r"anyone\s+can\s+apply)\b",
    re.IGNORECASE,
)

# ── Arabic signals ─────────────────────────────────────────────────────────────

_AR_RESPONSIBILITIES = re.compile(
    r"(?:مسؤوليات|مهام|واجبات|إدارة|تطوير|صيانة|دعم|مساعدة|تنسيق|قيادة|"
    r"تنفيذ|تصميم|مراجعة|تحليل|تقديم|إعداد|متابعة|تشغيل|بناء|ضمان|"
    r"الإشراف|تحقيق|معالجة|تدريب\s*الفريق)"
)
_AR_REQUIREMENTS = re.compile(
    r"(?:متطلبات|مهارات|مؤهلات|خبرة|تعليم|شهادة|معرفة|يجب|مطلوب|"
    r"إجادة|إلمام|كفاءة|خلفية|قدرة|حاصل\s*على|بكالوريوس|ماجستير|دبلوم|"
    r"ثانوية|تقنية)"
)
_AR_JOB_CONTEXT = re.compile(
    r"(?:منصب|دور|وظيفة|مرشح|فريق|قسم|شركة|مؤسسة|مكتب|عميل|خدمة|"
    r"مشروع|صناعة|قطاع|توظيف|عمل|متجر|مستودع|مستشفى|مدرسة|بنك|"
    r"فندق|مصنع)"
)
_AR_OPEN_ROLE = re.compile(
    r"(?:مفتوح\s+لجميع\s+الخلفيات|لا\s+يشترط\s+خبرة|"
    r"سيتم\s+توفير\s+التدريب|لا\s+تشترط\s+شهادة|"
    r"التدريب\s+سيتم|جميع\s+الخلفيات|مناسب\s+للجميع)"
)


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class DescriptionQuality:
    score: int
    state: Literal["insufficient", "needs_improvement", "ready"]
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def is_sufficient_for_ai(self) -> bool:
        return self.state == "ready"

    def rejection_message(self) -> str:
        """Human-readable rejection message for API responses."""
        return (
            "Please add meaningful job details such as responsibilities, skills, "
            "qualifications, or experience expectations before running AI analysis. "
            + (" | ".join(self.issues) if self.issues else "")
        ).strip()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _count_words(text: str) -> int:
    return len(text.split())


def _count_sentences(text: str) -> int:
    parts = re.split(r"[.!?؟\n]+", text)
    return len([p for p in parts if len(p.strip()) > 3])


def _is_placeholder(text: str) -> bool:
    normalized = text.strip().lower()
    if _PLACEHOLDER_RE.match(normalized):
        return True
    words = normalized.split()
    # All words are the same (e.g. "aaaa aaaa aaaa")
    if len(words) > 0 and all(w == words[0] for w in words):
        return True
    return False


def _has_repetition(text: str) -> bool:
    if _REPEAT_CHAR_RE.search(text):
        return True
    words = [w.lower() for w in text.split() if len(w) > 3]
    if len(words) < 5:
        return False
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    max_freq = max(freq.values())
    return max_freq / len(words) > 0.35


# ── Public API ─────────────────────────────────────────────────────────────────

def evaluate_description_quality(description: str) -> DescriptionQuality:
    """
    Evaluate job description quality using deterministic heuristics.

    Scoring (max 100):
      Word count:       0-10 words → 0, 11-30 → 15, 31-60 → 25, 61-120 → 40, 120+ → 50
      Sentences ≥ 3:   +10  (≥1: +5)
      Responsibilities: +20
      Requirements:     +20
      Job context:      +10
      Open-role decl.:  +25
      Repetition:       -25

    Thresholds:
      < 40  → insufficient
      40-69 → needs_improvement
      ≥ 70  → ready
    """
    text = (description or "").strip()

    if not text:
        return DescriptionQuality(
            score=0,
            state="insufficient",
            issues=["Job description is empty."],
            suggestions=["Add responsibilities, required skills, qualifications, and experience expectations."],
        )

    word_count     = _count_words(text)
    sentence_count = _count_sentences(text)
    has_placeholder = _is_placeholder(text)
    has_repetition  = _has_repetition(text)

    if has_placeholder:
        return DescriptionQuality(
            score=0,
            state="insufficient",
            issues=["The description appears to be placeholder or test text."],
            suggestions=["Replace with a real job description including responsibilities, skills, and qualifications."],
        )

    has_responsibilities = bool(_EN_RESPONSIBILITIES.search(text) or _AR_RESPONSIBILITIES.search(text))
    has_requirements     = bool(_EN_REQUIREMENTS.search(text)     or _AR_REQUIREMENTS.search(text))
    has_job_context      = bool(_EN_JOB_CONTEXT.search(text)      or _AR_JOB_CONTEXT.search(text))
    has_open_role        = bool(_EN_OPEN_ROLE.search(text)        or _AR_OPEN_ROLE.search(text))

    score = 0

    # Word count contribution (0-50 pts)
    if word_count >= 120:     score += 50
    elif word_count >= 61:    score += 40
    elif word_count >= 31:    score += 25
    elif word_count >= 11:    score += 15

    # Sentence structure (0-10 pts)
    if sentence_count >= 3:     score += 10
    elif sentence_count >= 1:   score += 5

    # Content signals
    if has_responsibilities: score += 20
    if has_requirements:     score += 20
    if has_job_context:      score += 10
    if has_open_role:        score += 25

    # Penalty
    if has_repetition:       score -= 25

    score = max(0, min(100, score))

    issues: list[str] = []
    suggestions: list[str] = []

    if word_count < 20:
        issues.append("Description is too short.")
    if has_repetition:
        issues.append("The text contains excessive repetition.")
    if not has_responsibilities and not has_open_role:
        issues.append("No responsibilities or duties mentioned.")
    if not has_requirements and not has_open_role:
        issues.append("No skills, qualifications, or experience requirements mentioned.")
    if not has_job_context:
        issues.append("No clear job context detected.")

    if word_count < 50:
        suggestions.append("Expand to at least 50 words for better AI analysis.")
    if not has_responsibilities and not has_open_role:
        suggestions.append("Add key responsibilities and daily duties.")
    if not has_requirements and not has_open_role:
        suggestions.append("Add required skills, qualifications, or experience level.")
    if not has_job_context:
        suggestions.append("Mention the team, department, industry, or company context.")

    if score < 40:
        state: Literal["insufficient", "needs_improvement", "ready"] = "insufficient"
    elif score < 70:
        state = "needs_improvement"
    else:
        state = "ready"

    return DescriptionQuality(score=score, state=state, issues=issues, suggestions=suggestions)


# ── Job title validation ───────────────────────────────────────────────────────

_TITLE_PLACEHOLDER_RE = re.compile(
    r"^(test\d*|sample|demo|placeholder|temp|hello|hi|n/?a|tbd|todo|"
    r"job|new\s+job|title|position|vacancy|role|post|opening|"
    r"اختبار|تجربة|مثال|نموذج|وظيفة\s*جديدة|منصب|دور)\.?$",
    re.IGNORECASE,
)
_TITLE_REPEAT_CHARS_RE = re.compile(r"(.)\1{2,}")  # 3+ identical consecutive chars

_BAD_TITLE_MSG = (
    "Please enter a real job title, such as Sales Assistant, "
    "Accountant, Driver, or HR Manager."
)


def validate_job_title(title: str) -> tuple[bool, str | None]:
    """Return (is_valid, error_message_or_None).

    Rejects placeholder, gibberish, and symbol-only titles.
    Accepts real job titles in any language (English, Arabic, etc.).
    """
    text = (title or "").strip()

    if not text:
        return False, "Job title is required."

    if len(text) < 2:
        return False, "Job title is too short."

    if _TITLE_PLACEHOLDER_RE.match(text.lower()):
        return False, _BAD_TITLE_MSG

    # Reject titles with no Unicode letter at all (symbols/numbers/punctuation only).
    # str.isalpha() is Unicode-aware: Arabic, Chinese, etc. all return True.
    if not any(c.isalpha() for c in text):
        return False, _BAD_TITLE_MSG

    if _TITLE_REPEAT_CHARS_RE.search(text):
        return False, _BAD_TITLE_MSG

    words = text.split()
    if len(words) > 1 and all(w.lower() == words[0].lower() for w in words):
        return False, _BAD_TITLE_MSG

    return True, None
