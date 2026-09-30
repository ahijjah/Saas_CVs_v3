"""
P0-02a — build the D-01 criteria-mapping prompt v10 from an exported v9.

Offline only: reads a text file, writes text files. No database connection,
no prompt insert, no activation.

v10 = v9 + targeted edits (everything else in v9 is left untouched):
  E1  replace the MATCH STATUS DEFINITIONS block with the authoritative
      ASSESSMENT STATUS CONTRACT (services.llm_criteria_mapper.D01_STATUS_CONTRACT);
      if v9 has no such block, insert the contract before MATCH TYPE GUIDE
  E2  REQUIRED vs PREFERRED: drop "Partial evidence -> PARTIAL" (uncertainty
      is CANNOT_DETERMINE; a known shortfall is PARTIAL)
  E3  output schema: status enum gains CANNOT_DETERMINE, plus a cd_reason line
  E4  TYPE B: "years met, relevance not established -> PARTIAL" becomes
      CANNOT_DETERMINE with cd_reason "relevance_unverified" (flag kept)
  E5  relevance_unverified risk-flag definition points to CANNOT_DETERMINE
  E6  qualitative summary: CANNOT_DETERMINE is never a gap; interview
      questions verify CANNOT_DETERMINE criteria first
  E7  v9 evidence paragraph ("Only set status=MATCHED or status=PARTIAL if you
      can provide … supporting_evidence … else ABSENT") also covers
      CANNOT_DETERMINE

Every edit is reported (applied / not found). E1 and E3 are required: when
their anchors are missing nothing is written and the exit code is 2, so the
prompt is reviewed by hand instead of shipping a half-edited v10. Lines that
still mention relevance_unverified together with PARTIAL after E4/E5 are
listed for manual review.

Usage (from backend/):
    # 1. Export v9 READ-ONLY on the production server, e.g.
    #    docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -At \
    #      -c "SELECT system_prompt FROM cv_analyzer.ai_prompts
    #          WHERE prompt_code = 'recruitment.criteria_mapping' AND version = 9" \
    #      > d01_prompt_v9.txt
    # 2. Build v10 locally:
    python scripts/build_d01_prompt_v10.py --v9-file d01_prompt_v9.txt --out-dir out/

Outputs in --out-dir:
    d01_prompt_v10.txt          the v10 system prompt
    d01_prompt_v9_to_v10.diff   unified diff for review
    d01_prompt_v10_report.md    per-edit anchor report
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.llm_criteria_mapper import D01_STATUS_CONTRACT  # noqa: E402

_CONTRACT_HEADING = "ASSESSMENT STATUS CONTRACT"

_REQUIRED_PREFERRED_TEXT = (
    "- required=true criteria are hard requirements; required=false criteria are nice-to-have.\n"
    "- The same ASSESSMENT STATUS CONTRACT applies to both; required/preferred affects scoring,\n"
    "  not which status you choose."
)
_RELEVANCE_FLAG_TEXT = (
    "- relevance_unverified:   A relevance-qualified experience criterion's years threshold was "
    "met, but domain/functional relevance could not be confirmed from available data "
    "(status CANNOT_DETERMINE)."
)
_QS_CD_RULE = (
    "suggested_interview_questions should first verify CANNOT_DETERMINE criteria, then target "
    "PARTIAL or ABSENT criteria areas. CANNOT_DETERMINE criteria are never listed as gaps."
)

# A block heading: an upper-case line ending with ':' (e.g. "MATCH TYPE GUIDE:").
_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 /()\-—–&]+:\s*$")


@dataclass
class BuildResult:
    text: str
    applied: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _block_span(lines: list[str], start: int) -> int:
    """Index one past the end of the block whose heading is lines[start]."""
    i = start + 1
    while i < len(lines):
        if not lines[i].strip():
            return i
        if _HEADING_RE.match(lines[i]):
            return i
        i += 1
    return i


def _block_span_from(lines: list[str], i: int) -> int:
    """Index one past the last non-blank, non-heading line starting at lines[i]."""
    while i < len(lines) and lines[i].strip() and not _HEADING_RE.match(lines[i]):
        i += 1
    return i


def build_v10(v9: str) -> BuildResult:
    r = BuildResult(text=v9)
    if _CONTRACT_HEADING in v9:
        r.errors.append(f"input already contains '{_CONTRACT_HEADING}' (already v10?)")
        return r

    text = v9.replace("\r\n", "\n")
    contract = D01_STATUS_CONTRACT.rstrip("\n")

    # ── E1: status definitions → contract ───────────────────────────────────
    lines = text.split("\n")
    idx = next((i for i, ln in enumerate(lines) if ln.strip().startswith("MATCH STATUS DEFINITIONS")), None)
    if idx is not None:
        # Keep a banner rule ("=====…") directly under the heading, if present.
        body_start = idx + 1
        if body_start < len(lines) and re.fullmatch(r"=+\s*", lines[body_start]):
            body_start += 1
        end = _block_span(lines, body_start - 1) if body_start == idx + 1 else _block_span_from(lines, body_start)
        contract_lines = contract.split("\n")
        if body_start == idx + 1:
            lines[idx:end] = contract_lines
        else:
            # heading line → contract heading; keep the banner; contract body replaces the bullets
            lines[idx:end] = [contract_lines[0], lines[idx + 1]] + contract_lines[1:]
        r.applied.append(f"E1 replaced MATCH STATUS DEFINITIONS block ({end - idx} lines)")
    else:
        anchor = next((i for i, ln in enumerate(lines) if ln.strip().startswith("MATCH TYPE GUIDE")), None)
        if anchor is None:
            r.errors.append("E1 anchor missing: neither 'MATCH STATUS DEFINITIONS' nor 'MATCH TYPE GUIDE'")
        else:
            lines[anchor:anchor] = contract.split("\n") + [""]
            r.applied.append("E1 inserted contract before MATCH TYPE GUIDE (no status-definition block in v9)")
    text = "\n".join(lines)

    # ── E2: REQUIRED vs PREFERRED ────────────────────────────────────────────
    req_re = re.compile(r"^- required=true criteria are hard requirements\..*$", re.MULTILINE)
    pref_re = re.compile(r"^- required=false criteria are nice-to-have\..*\n?", re.MULTILINE)
    if req_re.search(text):
        text = req_re.sub(lambda _m: _REQUIRED_PREFERRED_TEXT, text, count=1)
        text = pref_re.sub("", text, count=1)
        r.applied.append("E2 rewrote REQUIRED vs PREFERRED lines")
    else:
        r.not_found.append("E2 'required=true criteria are hard requirements.' line")

    # ── E3: output schema ───────────────────────────────────────────────────
    schema_re = re.compile(r'^([ \t]*)"status":\s*"<MATCHED\|PARTIAL\|ABSENT>",[ \t]*$', re.MULTILINE)
    m = schema_re.search(text)
    if m:
        indent = m.group(1)
        text = schema_re.sub(
            lambda mm: (
                f'{indent}"status": "<MATCHED|PARTIAL|ABSENT|CANNOT_DETERMINE>",\n'
                f'{indent}"cd_reason": "<relevance_unverified|detail_missing|ambiguous|conflicting, '
                f'or null unless CANNOT_DETERMINE>",'
            ),
            text, count=1,
        )
        r.applied.append("E3 added CANNOT_DETERMINE to the status enum and a cd_reason line")
    else:
        r.errors.append('E3 anchor missing: "status": "<MATCHED|PARTIAL|ABSENT>", in the output schema')

    # ── E4: TYPE B relevance → CANNOT_DETERMINE ──────────────────────────────
    # One sentence (no '.' other than "e.g."/"i.e.") that says the years are met and relevance is not
    # established/confirmed, then "→ (assign) PARTIAL" (optionally with the
    # old match_type/confidence hints). "years are NOT met → PARTIAL" is a
    # known shortfall and never matches ("years … met" must not be negated).
    typeb_re = re.compile(
        r"(years\s+(?:are\s+)?met\b(?:e\.g\.|i\.e\.|[^.]){0,400}?)(→|->)\s*(assign\s+)?PARTIAL"
        r"(?:,\s*match_type=\"inferred\")?(?:,\s*confidence\s*[0-9.]+\s*[-–]\s*[0-9.]+)?",
        re.IGNORECASE,
    )

    def _typeb(mm: re.Match) -> str:
        span = mm.group(1)
        if not re.search(r"relevan", span, re.IGNORECASE):
            return mm.group(0)
        return f'{span}{mm.group(2)} {mm.group(3) or ""}CANNOT_DETERMINE with cd_reason "relevance_unverified"'

    text, n = typeb_re.subn(_typeb, text)

    # v9 wording: "If the years threshold is met/exceeded but you have NO …
    # evidence available to confirm relevance … Assign status=PARTIAL,
    # match_type="inferred", confidence in the 0.35–0.59 range," — replace the
    # status only when the enclosing bullet/paragraph is about years being met
    # with relevance unconfirmed (never "NOT met").
    status_re = re.compile(
        r'status=PARTIAL(?:,\s*match_type="inferred")?'
        r'(?:,\s*confidence in the [0-9.]+\s*[–-]\s*[0-9.]+ range)?'
    )

    def _typeb_status(mm: re.Match) -> str:
        start = max(text.rfind("\n- ", 0, mm.start()), text.rfind("\n\n", 0, mm.start()))
        ctx = text[start:mm.start()]
        if (re.search(r"years(?:\s+threshold)?\s+(?:is|are)\s+met", ctx, re.IGNORECASE)
                and re.search(r"confirm\s+relevance|relevance\s+(?:could\s+not|cannot|is\s+not)", ctx, re.IGNORECASE)
                and not re.search(r"\bNOT\s+met\b", ctx)):
            return 'status=CANNOT_DETERMINE with cd_reason "relevance_unverified"'
        return mm.group(0)

    text = status_re.sub(_typeb_status, text)
    n = sum(1 for _ in re.finditer(r'CANNOT_DETERMINE with cd_reason "relevance_unverified"', text))
    if n:
        r.applied.append(f"E4 years met + relevance not established → CANNOT_DETERMINE ({n} occurrence(s))")
    else:
        r.not_found.append("E4 'years met … relevance not established … → PARTIAL' sentence")

    # ── E5: relevance_unverified flag definition ────────────────────────────
    flag_re = re.compile(r"^- relevance_unverified:.*$", re.MULTILINE)
    if flag_re.search(text):
        text = flag_re.sub(lambda _m: _RELEVANCE_FLAG_TEXT, text, count=1)
        r.applied.append("E5 rewrote the relevance_unverified risk-flag definition")
    else:
        r.not_found.append("E5 '- relevance_unverified:' risk-flag line")

    # ── E6: qualitative-summary rules ───────────────────────────────────────
    gaps_re = re.compile(r'("gaps_identified":\s*\["<[^">]*?)(>"\])')
    if gaps_re.search(text) and "never CANNOT_DETERMINE" not in text:
        text = gaps_re.sub(r"\1 (never CANNOT_DETERMINE)\2", text, count=1)
        r.applied.append("E6a gaps_identified schema hint excludes CANNOT_DETERMINE")
    else:
        r.not_found.append("E6a gaps_identified schema hint")
    qs4_re = re.compile(r"^QS4\..*?(?=^QS\d+\.|\n\n|\Z)", re.MULTILINE | re.DOTALL)
    if qs4_re.search(text):
        text = qs4_re.sub(lambda _m: f"QS4. {_QS_CD_RULE}\n", text, count=1)
        r.applied.append("E6b replaced QS4 (interview questions verify CANNOT_DETERMINE first)")
    elif "QUALITATIVE SUMMARY RULES:" in text:
        lines = text.split("\n")
        i = next(i for i, ln in enumerate(lines) if ln.strip().startswith("QUALITATIVE SUMMARY RULES:"))
        lines.insert(_block_span(lines, i), f"QS-CD. {_QS_CD_RULE}")
        text = "\n".join(lines)
        r.applied.append("E6b appended a CANNOT_DETERMINE rule to QUALITATIVE SUMMARY RULES")
    else:
        r.not_found.append("E6b QS4 / QUALITATIVE SUMMARY RULES")

    # ── E7: v9 evidence paragraph also covers CANNOT_DETERMINE ───────────────
    ev_re = re.compile(r"^(IMPORTANT: Only set )status=MATCHED or status=PARTIAL( if you can provide)",
                       re.MULTILINE)
    if ev_re.search(text):
        text = ev_re.sub(r"\1status=MATCHED, status=PARTIAL or status=CANNOT_DETERMINE\2", text, count=1)
        r.applied.append("E7 evidence paragraph now covers CANNOT_DETERMINE (no quote -> ABSENT)")
    else:
        r.not_found.append("E7 'IMPORTANT: Only set status=MATCHED or status=PARTIAL' evidence paragraph")

    # ── Manual-review list (lines outside the inserted contract) ─────────────
    contract_lines = set(contract.split("\n"))
    for no, ln in enumerate(text.split("\n"), 1):
        if ln in contract_lines:
            continue
        if "relevance_unverified" in ln and re.search(r"\bPARTIAL\b", ln) and "CANNOT_DETERMINE" not in ln:
            r.review.append(f"line {no}: {ln.strip()[:160]}")

    r.text = text
    return r


def _report(r: BuildResult, v9_path: str) -> str:
    out = [f"# D-01 prompt v10 build report\n\nSource: `{v9_path}`\n"]
    for title, items in (("Errors (nothing written)", r.errors), ("Applied", r.applied),
                         ("Anchors not found (edit skipped)", r.not_found),
                         ("Manual review: relevance_unverified + PARTIAL still together", r.review)):
        out.append(f"## {title}\n")
        out.extend(f"- {i}" for i in items) if items else out.append("- none")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v9-file", required=True, help="exported v9 system_prompt (plain text)")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)

    v9 = Path(args.v9_file).read_text(encoding="utf-8")
    r = build_v10(v9)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "d01_prompt_v10_report.md").write_text(_report(r, args.v9_file), encoding="utf-8")
    if r.errors:
        print("\n".join(r.errors), file=sys.stderr)
        return 2
    (out / "d01_prompt_v10.txt").write_text(r.text, encoding="utf-8")
    diff = difflib.unified_diff(
        v9.replace("\r\n", "\n").splitlines(keepends=True), r.text.splitlines(keepends=True),
        fromfile="v9", tofile="v10",
    )
    (out / "d01_prompt_v9_to_v10.diff").write_text("".join(diff), encoding="utf-8")
    print(f"v10 written to {out}; applied={len(r.applied)} not_found={len(r.not_found)} review={len(r.review)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
