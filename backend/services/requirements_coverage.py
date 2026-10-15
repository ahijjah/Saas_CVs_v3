"""Coverage warning for requirements-v2 extraction: detects TOTAL omission of a duties section only.

The rule is general (English and Arabic headings, no wording of any particular job): when the job description has a
duties heading (Responsibilities, Duties, What you will do, المسؤوليات, المهام, ...) with duty lines under it, but the
extracted document has no item that came from responsibilities, the editor shows a non-blocking warning that duties may
have been omitted. It does NOT judge partial completeness (a few duties missing, a competency or an OR option missing),
never changes readiness, weights or items, and never blocks a save.

Outside the frozen package on purpose (services/requirements_v2 is frozen at 059c56b).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

CODE = "duties_may_be_omitted"
DUTY_HEADING_TERMS = (
    "responsibilities", "duties", "what you will do", "what you'll do", "day-to-day tasks", "day to day tasks", "main tasks", "key tasks",
    "المسؤوليات", "المسئوليات", "المهام", "الأعمال اليومية", "الواجبات",
)
OTHER_HEADING_TERMS = (
    "qualification", "requirement", "skill", "competenc", "ability", "abilities", "experience", "education", "certificat", "benefit",
    "what we offer", "reporting", "supervision", "about", "company", "salary", "location", "objective", "language",
    "متطلبات", "المؤهلات", "المهارات", "الكفاءات", "الخبرة", "المزايا", "الشروط", "التعليم", "الشهادات", "الكفاءة",
)
MAX_HEADING_WORDS = 4          # a heading is a short line; a sentence that mentions "responsibilities" is not one
MIN_DUTY_WORDS = 3             # a duty line has at least this many words (a bare "TBD" or "N/A" is not a duty)
_BULLET = re.compile(r"^\s*(?:[-•*●▪◦]|\d+[.)]|[a-z][.)])\s*")


def _clean(line: str) -> str:
    return _BULLET.sub("", line).strip().rstrip(":：").strip()


def _is_heading(line: str, terms: tuple[str, ...]) -> bool:
    s = line.strip().rstrip(":：").strip()
    if not s or s.endswith(".") or len(s.split()) > MAX_HEADING_WORDS:
        return False
    low = s.lower()
    return any(t in low for t in terms)


def _sections(jd_text: str) -> list[tuple[str, list[str]]]:
    """(heading, body lines) for every heading-like line; the first pseudo-section has heading ''."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for raw in (jd_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _is_heading(line, DUTY_HEADING_TERMS) or _is_heading(line, OTHER_HEADING_TERMS):
            out.append((line.rstrip(":：").strip(), []))
            if line.endswith((":", "：")) or _is_heading(line, DUTY_HEADING_TERMS) or _is_heading(line, OTHER_HEADING_TERMS):
                continue
        out[-1][1].append(line)
    return out


def duty_sections(jd_text: str) -> list[dict[str, Any]]:
    """Every recognised duties section with its duty lines (the lines up to the next heading of any kind)."""
    found: list[dict[str, Any]] = []
    for heading, body in _sections(jd_text):
        if not _is_heading(heading, DUTY_HEADING_TERMS):
            continue
        duties = [_clean(line) for line in body if len(_clean(line).split()) >= MIN_DUTY_WORDS
                  and not _is_heading(line, DUTY_HEADING_TERMS) and not _is_heading(line, OTHER_HEADING_TERMS)]
        found.append({"heading": heading, "candidate_lines": len(duties)})
    return found


def responsibility_items(requirements: Mapping[str, Any] | None) -> int:
    count = 0
    for cat in ((requirements or {}).get("categories") or {}).values():
        items = cat.get("items", []) if isinstance(cat, Mapping) else cat
        count += sum(1 for it in items or [] if isinstance(it, Mapping) and it.get("origin") == "from_responsibilities")
    return count


def coverage_warnings(jd_text: str | None, requirements: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The non-blocking warnings for one extraction. Empty when there is no duties section with duty lines, or when the
    extraction has at least one responsibility item (partial completeness is not judged here)."""
    if not jd_text or not requirements:
        return []
    if responsibility_items(requirements) > 0:
        return []
    return [{"code": CODE, "heading": s["heading"], "candidate_lines": s["candidate_lines"]} for s in duty_sections(jd_text) if s["candidate_lines"] > 0][:1]


def jd_sha256(jd_text: str) -> str:
    return hashlib.sha256(jd_text.encode("utf-8")).hexdigest()
