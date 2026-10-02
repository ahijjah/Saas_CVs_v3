"""
S2 semantic-quality evaluation harness — READ-ONLY / OFFLINE.

Question answered: "Did the AI correctly understand whether each CV experience
entry satisfies the experience requirement?" It produces a human-review report
of every (criterion x experience entry) S2 classification. It does NOT compute
years, S5 statuses or candidate scores.

Pipeline per sampled application (scoped by application_id only):
  stored extracted_text (largest file, as the scorer uses)
    -> S0 v2 build_s0 (run in-process; S0 output is not stored in production,
       so it is cached in a local file cache and reused across runs)
    -> S2 classify_criterion for every fixture RequirementSpec
       (scripts/s2_eval_criteria.json — manual fixtures, S1 does not exist)

Guarantees:
  - database: one connection with default_transaction_read_only=on (verified
    with SHOW before any query); only SELECTs inside READ ONLY transactions;
    candidate names/emails are never selected;
  - no change to S0 / S2 / S4 / S5 code, prompts, schema or configuration:
    the production modules are imported and called as-is;
  - nothing is written anywhere except --out (and --cache-dir).
  - the review flags are deterministic keyword heuristics that only PRIORITISE
    human review; they are not a judgement and never alter a label.

OpenAI calls: one S0 call per CV (+ at most one repair), one S2 call per
(CV, semantic criterion) with at least one experience entry (+ at most one
repair). --dry-run selects and prints the sample and the call budget only.

Output (contains CV-derived text: keep it on the server):
  <out>/pairs.jsonl     one row per criterion x entry (+ one row per non-ok S2 result)
  <out>/s2_results.jsonl full s2_result_v1 objects
  <out>/s0_summary.jsonl S0 status per application
  <out>/summary.json    statistics
  <out>/report.md       human-review report
  <out>/review.csv      one row per classification with blank reviewer columns

Usage (one-off container on the server, e.g. the worker image):
  python scripts/s2_semantic_eval.py --out /tmp/s2_eval --dry-run
  python scripts/s2_semantic_eval.py --out /tmp/s2_eval --max-cvs 25
  python scripts/s2_semantic_eval.py --out /tmp/s2_eval --application-id <uuid> [...]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.experience_accounting import RequirementSpec  # noqa: E402
from services.s0_experience import llm_call  # noqa: E402
from services.s0_experience import structurer as s0  # noqa: E402
from services.s0_experience.schema import TRUSTED_STRUCTURE, S0Document  # noqa: E402
from services.s0_experience.text import split_lines  # noqa: E402
from services.s2_experience import classifier as s2  # noqa: E402

DEFAULT_CRITERIA = Path(__file__).resolve().parent / "s2_eval_criteria.json"
# gpt-4o-mini list prices (USD per 1M tokens) — an ASSUMPTION; override with flags.
DEFAULT_PRICE_IN = 0.15
DEFAULT_PRICE_OUT = 0.60
LABELS = ("qualifying", "related", "not_relevant", "insufficient")

# Review-flag categories (heuristic prioritisation only).
FLAG_FALSE_Q = "likely_false_qualifying"
FLAG_FALSE_R = "likely_false_related"
FLAG_R_UNDERCALL = "related_title_is_target"
FLAG_NR_RELEVANT = "not_relevant_appears_relevant"
FLAG_INS_EVIDENCE = "insufficient_with_evidence"
FLAG_QUOTE = "quote_label_inconsistent"
FLAG_SETTING = "setting_not_visible"
FLAG_REASON = "reason_label_tension"
FLAG_ORDER = (FLAG_FALSE_Q, FLAG_FALSE_R, FLAG_R_UNDERCALL, FLAG_NR_RELEVANT, FLAG_INS_EVIDENCE,
              FLAG_QUOTE, FLAG_SETTING, FLAG_REASON)
FLAG_HELP = {
    FLAG_FALSE_Q: "qualifying, but no target/review term in title, employer or quotes",
    FLAG_FALSE_R: "related, but no target/review term anywhere in the entry",
    FLAG_R_UNDERCALL: "related, but the title contains a target phrase without a supporting-role word",
    FLAG_NR_RELEVANT: "not_relevant, but the entry contains a target phrase or >= 2 distinct review terms",
    FLAG_INS_EVIDENCE: "insufficient, but the entry has substantial text or a target phrase in the title",
    FLAG_QUOTE: "qualifying/related quote has no target/review term, or not_relevant quote contains a target phrase",
    FLAG_SETTING: "functional criterion with a setting labelled qualifying, setting phrase absent from entry",
    FLAG_REASON: "reason wording points the other way (weak signal)",
}
SUPPORTING_WORDS = ("assistant", "associate", "deputy", "junior", "intern", "trainee", "apprentice",
                    "support", "aide", "helper", "volunteer", "student")
NEGATIVE_REASON = ("no evidence", "unrelated", "not related", "does not", "doesn't", "lacks", "no connection",
                   "no indication", "not relevant")
POSITIVE_REASON = ("directly", "meets the", "is a target", "qualif", "clearly performs", "held the role")


# ── read-only database access ───────────────────────────────────────────────

class ReadOnlyDB:
    def __init__(self, conn):
        self.conn = conn

    @classmethod
    async def connect(cls, dsn: str) -> "ReadOnlyDB":
        import asyncpg
        conn = await asyncpg.connect(
            dsn, server_settings={"default_transaction_read_only": "on",
                                  "search_path": "cv_analyzer, public"})
        ro = await conn.fetchval("SHOW default_transaction_read_only")
        if ro != "on":
            await conn.close()
            raise RuntimeError(f"connection is not read-only (default_transaction_read_only={ro!r})")
        return cls(conn)

    async def fetch(self, sql: str, *args):
        async with self.conn.transaction(readonly=True):
            return await self.conn.fetch(sql, *args)

    async def close(self):
        await self.conn.close()


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


SAMPLE_IDS_SQL = """
SELECT a.application_id::text AS application_id, a.job_id::text AS job_id, j.title AS job_title,
       max(length(f.extracted_text)) AS text_len
FROM applications a
JOIN jobs j ON j.job_id = a.job_id
JOIN application_files f ON f.application_id = a.application_id
WHERE f.extraction_status = 'done' AND length(coalesce(f.extracted_text, '')) >= $1
GROUP BY a.application_id, a.job_id, j.title
ORDER BY md5(a.application_id::text || $2)
"""

TEXT_SQL = """
SELECT DISTINCT ON (f.application_id) f.application_id::text AS application_id, f.extracted_text,
       a.job_id::text AS job_id, j.title AS job_title
FROM application_files f
JOIN applications a ON a.application_id = f.application_id
JOIN jobs j ON j.job_id = a.job_id
WHERE f.application_id = ANY($1::uuid[]) AND f.extracted_text IS NOT NULL
ORDER BY f.application_id, length(f.extracted_text) DESC
"""


def spread_sample(rows: list[dict], max_cvs: int, max_per_job: int) -> list[dict]:
    """Round-robin across jobs (deterministic order from the SQL) for diversity."""
    by_job: dict[str, list[dict]] = defaultdict(list)
    order: list[str] = []
    for r in rows:
        if r["job_id"] not in by_job:
            order.append(r["job_id"])
        by_job[r["job_id"]].append(r)
    out: list[dict] = []
    depth = 0
    while len(out) < max_cvs and depth < max_per_job:
        added = False
        for j in order:
            if depth < len(by_job[j]) and len(out) < max_cvs:
                out.append(by_job[j][depth])
                added = True
        if not added:
            break
        depth += 1
    return out


async def load_sample(db: ReadOnlyDB, args) -> list[dict]:
    if args.application_id:
        ids = list(dict.fromkeys(args.application_id))
    else:
        rows = [dict(r) for r in await db.fetch(SAMPLE_IDS_SQL, args.min_text_chars, args.seed)]
        ids = [r["application_id"] for r in spread_sample(rows, args.max_cvs, args.max_per_job)]
    if not ids:
        return []
    texts = {r["application_id"]: dict(r) for r in await db.fetch(TEXT_SQL, ids)}
    return [texts[i] for i in ids if i in texts]


# ── fixtures ────────────────────────────────────────────────────────────────

def load_criteria(path: Path, only: list[str] | None = None) -> list[tuple[RequirementSpec, list[str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for c in data["criteria"]:
        if only and c["criterion_id"] not in only:
            continue
        for span in c.get("source_spans", []):
            if span not in c["criterion_text"]:
                raise ValueError(f"{c['criterion_id']}: source span {span!r} not verbatim in criterion_text")
        spec = RequirementSpec(policy=c["policy"], required_years=c.get("required_years"),
                               targets=tuple(c["targets"]), setting=c.get("setting"),
                               spec_version=data.get("spec_version", "eval"),
                               criterion_id=c["criterion_id"], criterion_text=c["criterion_text"],
                               source_spans=tuple(c.get("source_spans", ())))
        out.append((spec, list(c.get("review_terms", []))))
    if only:
        missing = set(only) - {s.criterion_id for s, _ in out}
        if missing:
            raise ValueError(f"unknown criterion ids: {sorted(missing)}")
    return out


# ── local file cache (reuses S0/S2 output across runs; never production) ────

class FileCache:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.hits = 0

    def _p(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> dict | None:
        p = self._p(key)
        if p.exists():
            self.hits += 1
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def set(self, key: str, value: dict) -> None:
        self._p(key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class _CallCache:
    """Per-call view of a FileCache that records whether THIS call was a hit."""

    def __init__(self, inner: FileCache):
        self.inner, self.hit = inner, False

    def get(self, key: str) -> dict | None:
        v = self.inner.get(key)
        self.hit = v is not None
        return v

    def set(self, key: str, value: dict) -> None:
        self.inner.set(key, value)


# ── review heuristics ───────────────────────────────────────────────────────

def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return " ".join(re.sub(r"[^\w]+", " ", s).split())


def _term_re(term: str) -> re.Pattern | None:
    t = norm(term)
    if not t:
        return None
    tail = r"(?!\w)" if len(t) <= 3 else ""       # short terms: whole word only
    return re.compile(r"(?<!\w)" + re.escape(t) + tail)


class Matcher:
    def __init__(self, spec: RequirementSpec, review_terms: list[str]):
        phrases = list(spec.targets) + list(spec.source_spans)
        self.target = [p for p in (_term_re(x) for x in phrases) if p]
        self.terms = [(t, p) for t, p in ((t, _term_re(t)) for t in review_terms) if p]
        self.setting = _term_re(spec.setting) if spec.setting else None

    def strong(self, text: str) -> bool:
        n = norm(text)
        return any(p.search(n) for p in self.target)

    def term_hits(self, text: str) -> set[str]:
        n = norm(text)
        return {t for t, p in self.terms if p.search(n)}

    def any_hit(self, text: str) -> bool:
        return self.strong(text) or bool(self.term_hits(text))


def review_flags(m: Matcher, spec: RequirementSpec, row: dict) -> list[str]:
    label = row["label"]
    title, employer = row.get("title") or "", row.get("employer") or ""
    quotes = " | ".join(row.get("quotes") or [])
    entry = row.get("entry_text") or ""
    reason = (row.get("reason") or "").lower()
    flags: list[str] = []
    if label == "qualifying" and not m.any_hit(f"{title} {employer} {quotes}"):
        flags.append(FLAG_FALSE_Q)
    if label == "related" and not m.any_hit(f"{title} {employer} {entry}"):
        flags.append(FLAG_FALSE_R)
    if label == "related" and m.strong(title) and not any(w in norm(title).split() for w in SUPPORTING_WORDS):
        flags.append(FLAG_R_UNDERCALL)
    if label == "not_relevant" and (m.strong(f"{title} {entry}") or len(m.term_hits(f"{title} {entry}")) >= 2):
        flags.append(FLAG_NR_RELEVANT)
    if label == "insufficient" and (len(row.get("body_text") or "") >= 200 or m.strong(title)):
        flags.append(FLAG_INS_EVIDENCE)
    if (label in ("qualifying", "related") and quotes and row.get("basis") != "context"
            and not m.any_hit(quotes)) or (label == "not_relevant" and m.strong(quotes)):
        flags.append(FLAG_QUOTE)
    if (label == "qualifying" and spec.policy == "functional" and m.setting is not None
            and not m.setting.search(norm(f"{title} {employer} {entry}"))):
        flags.append(FLAG_SETTING)
    if (label == "qualifying" and any(w in reason for w in NEGATIVE_REASON)) or \
            (label == "not_relevant" and any(w in reason for w in POSITIVE_REASON)):
        flags.append(FLAG_REASON)
    return flags


# ── evaluation ──────────────────────────────────────────────────────────────

def entry_texts(doc: S0Document, lines: list[str]) -> dict[str, dict]:
    out = {}
    for e in doc.experience_entries():
        owned = e.owned_lines()
        header = set(e.header_lines) | set(e.shared_header_lines)
        out[e.entry_id] = {
            "kind": e.kind,
            "title": e.title.text if e.title else None,
            "employer": e.employer.text if e.employer else None,
            "entry_text": "\n".join(lines[ln - 1] for ln in owned),
            "body_text": "\n".join(lines[ln - 1] for ln in owned if ln not in header),
            "lines": len(owned),
        }
    return out


async def run_eval(sample: list[dict], criteria, args, out: Path) -> dict:
    cache_root = Path(args.cache_dir) if args.cache_dir else out / "cache"
    s0_cache, s2_cache = FileCache(cache_root / "s0"), FileCache(cache_root / "s2")
    sem = asyncio.Semaphore(args.concurrency)
    client = llm_call.create_client()
    calls = {"s0": [], "s2": []}                       # call_logs of calls made in THIS run

    async def build(app):
        async with sem:
            cc = _CallCache(s0_cache)
            doc = await s0.build_s0(app["extracted_text"], client=client, cache=cc)
            if not cc.hit:
                calls["s0"].extend(doc.structurer.get("call_log") or [])
            return doc

    docs = await asyncio.gather(*(build(a) for a in sample))

    async def classify(app, doc, spec):
        async with sem:
            cc = _CallCache(s2_cache)
            res = await s2.classify_criterion(doc, spec, extracted_text=app["extracted_text"],
                                              client=client, cache=cc)
            if not cc.hit:
                calls["s2"].extend(res.structurer.get("call_log") or [])
            return app, doc, spec, res

    jobs = [classify(a, d, sp) for a, d in zip(sample, docs) for sp, _ in criteria]
    results = await asyncio.gather(*jobs)

    terms = {sp.criterion_id: Matcher(sp, rt) for sp, rt in criteria}
    pairs: list[dict] = []
    for app, doc, spec, res in results:
        base = {"application_id": app["application_id"], "job_title": app["job_title"],
                "criterion_id": spec.criterion_id, "criterion_text": spec.criterion_text,
                "policy": spec.policy, "targets": list(spec.targets), "setting": spec.setting,
                "required_years": spec.required_years,
                "s0_structure_status": doc.structure_status, "s0_date_status": doc.date_status,
                "s2_status": res.status, "s2_status_reason": res.status_reason,
                "s2_outcome": res.structurer.get("outcome"),
                "s2_validation_errors": res.validation.get("errors") or [],
                "s2_repair_errors": res.validation.get("repair_errors") or []}
        if not res.ok or not res.results:
            pairs.append({**base, "entry_id": None, "label": None, "flags": []})
            continue
        texts = entry_texts(doc, split_lines(app["extracted_text"]))
        for r in res.results:
            et = texts.get(r["entry_id"], {})
            row = {**base, "entry_id": r["entry_id"], **et, "label": r["label"], "basis": r["basis"],
                   "quotes": [q["original_text"] for q in r["quotes"]],
                   "quote_lines": [q["line"] for q in r["quotes"]],
                   "reason": r["reason"], "missing": r["missing"]}
            row["flags"] = review_flags(terms[spec.criterion_id], spec, row)
            pairs.append(row)

    with (out / "pairs.jsonl").open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with (out / "s2_results.jsonl").open("w", encoding="utf-8") as f:
        for app, _, spec, res in results:
            f.write(json.dumps({"application_id": app["application_id"], **res.to_dict()},
                               ensure_ascii=False) + "\n")
    with (out / "s0_summary.jsonl").open("w", encoding="utf-8") as f:
        for app, doc in zip(sample, docs):
            f.write(json.dumps({"application_id": app["application_id"], "job_title": app["job_title"],
                                **s0.summarize(doc)}, ensure_ascii=False, default=str) + "\n")
    summary = summarize(sample, docs, results, pairs, calls, s0_cache.hits, s2_cache.hits, args)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_review_csv(out / "review.csv", pairs)
    (out / "report.md").write_text(render_report(sample, criteria, docs, pairs, summary), encoding="utf-8")
    return summary


def _tokens(log: list[dict]) -> dict:
    pt = sum(c.get("prompt_tokens") or 0 for c in log)
    ct = sum(c.get("completion_tokens") or 0 for c in log)
    return {"calls": len(log), "repair_calls": sum(1 for c in log if c.get("call") == "repair"),
            "prompt_tokens": pt, "completion_tokens": ct,
            "finish_reasons": dict(Counter(str(c.get("finish_reason")) for c in log))}


def summarize(sample, docs, results, pairs, calls, s0_hits, s2_hits, args) -> dict:
    labelled = [p for p in pairs if p["label"]]
    s2_status = Counter(f"{r.status}/{r.status_reason or r.structurer.get('outcome') or '-'}"
                        for _, _, _, r in results)
    per_crit: dict[str, Counter] = defaultdict(Counter)
    per_policy: dict[str, Counter] = defaultdict(Counter)
    for p in labelled:
        per_crit[p["criterion_id"]][p["label"]] += 1
        per_policy[p["policy"]][p["label"]] += 1
    flags = Counter(f for p in labelled for f in p["flags"])
    t0, t2 = _tokens(calls["s0"]), _tokens(calls["s2"])
    cost = lambda t: (t["prompt_tokens"] * args.price_in + t["completion_tokens"] * args.price_out) / 1e6  # noqa: E731
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "s2_prompt_version": s2.S2_PROMPT_VERSION, "s2_model": s2.S2_MODEL,
        "s0_prompt_version": s0.S0C_PROMPT_VERSION,
        "applications": len(sample), "jobs": len({a["job_id"] for a in sample}),
        "criteria": len({p["criterion_id"] for p in pairs}),
        "s0_structure_status": dict(Counter(d.structure_status for d in docs)),
        "s0_status_reason": dict(Counter(str(d.status_reason) for d in docs)),
        "s0_experience_entries": sum(len(d.experience_entries()) for d in docs
                                     if d.structure_status in TRUSTED_STRUCTURE),
        "s2_criterion_results": dict(s2_status),
        "s2_repaired": sum(1 for _, _, _, r in results if r.ok and r.structurer.get("outcome") == "repaired"),
        "s2_failed": sum(1 for _, _, _, r in results if r.status == "failed"),
        "s2_not_run": sum(1 for _, _, _, r in results if r.status == "not_run"),
        "classifications": len(labelled),
        "label_distribution": {lab: sum(1 for p in labelled if p["label"] == lab) for lab in LABELS},
        "label_by_criterion": {k: dict(v) for k, v in sorted(per_crit.items())},
        "label_by_policy": {k: dict(v) for k, v in sorted(per_policy.items())},
        "flags": {f: flags.get(f, 0) for f in FLAG_ORDER},
        "flagged_classifications": sum(1 for p in labelled if p["flags"]),
        "api": {"s0": t0, "s2": t2, "s0_cache_hits": s0_hits, "s2_cache_hits": s2_hits},
        "estimated_cost_usd": {"s0": round(cost(t0), 4), "s2": round(cost(t2), 4),
                               "total": round(cost(t0) + cost(t2), 4),
                               "price_per_1m_in": args.price_in, "price_per_1m_out": args.price_out,
                               "note": "API-reported tokens of calls made in this run x assumed prices"},
    }


REVIEW_COLS = ("criterion_id", "policy", "application_id", "job_title", "entry_id", "kind", "title",
               "employer", "label", "basis", "quotes", "reason", "missing", "flags", "s2_outcome",
               "reviewer_label", "reviewer_correct_Y_N", "reviewer_note")


def write_review_csv(path: Path, pairs: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(REVIEW_COLS)
        for p in sorted((p for p in pairs if p["label"]),
                        key=lambda p: (p["criterion_id"], p["application_id"], p["entry_id"])):
            w.writerow([p["criterion_id"], p["policy"], p["application_id"], p["job_title"], p["entry_id"],
                        p.get("kind"), p.get("title"), p.get("employer"), p["label"], p.get("basis"),
                        " | ".join(p.get("quotes") or []), p.get("reason"), ";".join(p.get("missing") or []),
                        ";".join(p["flags"]), p.get("s2_outcome"), "", "", ""])


# ── report ──────────────────────────────────────────────────────────────────

def _md(s: Any) -> str:
    return str(s if s is not None else "—").replace("|", "\\|").replace("\n", " ⏎ ")


def _table(header: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_md(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _entry_block(p: dict, with_crit: bool = False) -> list[str]:
    head = f"- **{p['entry_id']}** `{p.get('kind')}` — {_md(p.get('title'))} @ {_md(p.get('employer'))}"
    if with_crit:
        head = f"- `{p['criterion_id']}` · app `{p['application_id']}` · " + head[2:]
    head += f" → **{p['label']}** ({p['basis']}, {p.get('s2_outcome')})"
    if p["flags"]:
        head += "  ⚑ " + ", ".join(p["flags"])
    out = [head]
    for q, ln in zip(p.get("quotes") or [], p.get("quote_lines") or []):
        out.append(f"  - quote L{ln}: “{_md(q)}”")
    out.append(f"  - reason: {_md(p.get('reason'))}")
    if p.get("missing"):
        out.append(f"  - missing: {', '.join(p['missing'])}")
    text = (p.get("entry_text") or "").strip()
    if text:
        clipped = text if len(text) <= 900 else text[:900] + " …"
        out.append("  - entry text:\n\n    ```\n" + "\n".join("    " + t for t in clipped.splitlines())
                   + "\n    ```")
    return out


def render_report(sample, criteria, docs, pairs, summary) -> str:
    L: list[str] = [
        "# S2 semantic evaluation report", "",
        f"Generated {summary['generated_at']} · S2 prompt `{summary['s2_prompt_version']}` "
        f"({summary['s2_model']}) · S0 prompt `{summary['s0_prompt_version']}`", "",
        "> Contains CV-derived text. Keep on the server. Review flags are keyword heuristics that only "
        "prioritise review; they are not judgements. No years, statuses or scores are computed here.", "",
        "## Summary", "",
        _table(["metric", "value"], [
            ["applications / jobs", f"{summary['applications']} / {summary['jobs']}"],
            ["criteria", summary["criteria"]],
            ["S0 structure status", summary["s0_structure_status"]],
            ["S0 experience entries (trusted)", summary["s0_experience_entries"]],
            ["S2 criterion results", summary["s2_criterion_results"]],
            ["S2 repaired / failed / not_run", f"{summary['s2_repaired']} / {summary['s2_failed']} / "
                                               f"{summary['s2_not_run']}"],
            ["classifications", summary["classifications"]],
            ["flagged classifications", summary["flagged_classifications"]],
            ["S0 calls (repairs)", f"{summary['api']['s0']['calls']} ({summary['api']['s0']['repair_calls']})"],
            ["S2 calls (repairs)", f"{summary['api']['s2']['calls']} ({summary['api']['s2']['repair_calls']})"],
            ["cache hits S0 / S2", f"{summary['api']['s0_cache_hits']} / {summary['api']['s2_cache_hits']}"],
            ["tokens in / out (S0+S2)",
             f"{summary['api']['s0']['prompt_tokens'] + summary['api']['s2']['prompt_tokens']} / "
             f"{summary['api']['s0']['completion_tokens'] + summary['api']['s2']['completion_tokens']}"],
            ["estimated cost (USD)", summary["estimated_cost_usd"]["total"]],
        ]), "",
        "### Label distribution", "",
        _table(["label", "count"], [[k, v] for k, v in summary["label_distribution"].items()]), "",
        _table(["criterion", "policy", *LABELS], [
            [sp.criterion_id, sp.policy, *[summary["label_by_criterion"].get(sp.criterion_id, {}).get(lab, 0)
                                           for lab in LABELS]] for sp, _ in criteria]), "",
        "### Review flags", "",
        _table(["flag", "count", "meaning"], [[f, summary["flags"][f], FLAG_HELP[f]] for f in FLAG_ORDER]), "",
        "## Sample", "",
        _table(["application_id", "job title", "S0 structure", "S0 dates", "experience entries"], [
            [a["application_id"], a["job_title"], d.structure_status, d.date_status,
             len(d.experience_entries()) if d.structure_status in TRUSTED_STRUCTURE else 0]
            for a, d in zip(sample, docs)]), "",
        "## Criteria (fixtures)", "",
        _table(["id", "policy", "N", "targets", "setting", "criterion text"], [
            [sp.criterion_id, sp.policy, sp.required_years, ", ".join(sp.targets), sp.setting, sp.criterion_text]
            for sp, _ in criteria]), "",
    ]
    failed = [p for p in pairs if p["s2_status"] != "ok"]
    if failed:
        L += ["## Non-ok S2 results", "",
              _table(["criterion", "application", "status", "reason", "first errors"], [
                  [p["criterion_id"], p["application_id"], p["s2_status"], p["s2_status_reason"],
                   "; ".join((p["s2_validation_errors"] + p["s2_repair_errors"])[:3])] for p in failed]), ""]
    labelled = [p for p in pairs if p["label"]]
    L += ["## Review queue (flagged)", ""]
    for f in FLAG_ORDER:
        hits = [p for p in labelled if f in p["flags"]]
        if not hits:
            continue
        L += [f"### {f} ({len(hits)})", "", f"_{FLAG_HELP[f]}_", ""]
        for p in hits:
            L += _entry_block(p, with_crit=True)
        L.append("")
    repaired = [p for p in labelled if p.get("s2_outcome") == "repaired"]
    if repaired:
        L += ["### repaired responses (first-attempt errors)", ""]
        seen = set()
        for p in repaired:
            k = (p["criterion_id"], p["application_id"])
            if k not in seen:
                seen.add(k)
                L.append(f"- `{k[0]}` · app `{k[1]}`: " + _md("; ".join(p["s2_validation_errors"][:5])))
        L.append("")
    L += ["## All classifications by criterion and candidate", ""]
    by_crit: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for p in labelled:
        by_crit[p["criterion_id"]][p["application_id"]].append(p)
    for sp, _ in criteria:
        L += [f"### {sp.criterion_id} — {sp.criterion_text}",
              f"policy `{sp.policy}` · targets {list(sp.targets)} · setting {sp.setting!r}", ""]
        for app_id, rows in by_crit.get(sp.criterion_id, {}).items():
            L.append(f"#### application `{app_id}` — {_md(rows[0]['job_title'])}")
            L.append("")
            for p in sorted(rows, key=lambda r: int(r["entry_id"][1:])):
                L += _entry_block(p)
            L.append("")
        empty = [p for p in pairs if p["criterion_id"] == sp.criterion_id and p["s2_status"] == "ok"
                 and not p["label"]]
        if empty:
            L += [f"_{len(empty)} application(s) had no experience entries._", ""]
    return "\n".join(L) + "\n"


# ── entry point ─────────────────────────────────────────────────────────────

async def main_async(args) -> int:
    criteria = load_criteria(Path(args.criteria), args.criterion)
    from config import get_settings
    db = await ReadOnlyDB.connect(_plain_dsn(get_settings().database_url))
    try:
        sample = await load_sample(db, args)
    finally:
        await db.close()
    if not sample:
        print("No applications matched the sample query.")
        return 1
    semantic = [sp for sp, _ in criteria if sp.policy != "pure_duration"]
    print(f"Sample: {len(sample)} applications across {len({a['job_id'] for a in sample})} jobs")
    for a in sample:
        print(f"  {a['application_id']}  {len(a['extracted_text']):>6} chars  {a['job_title']}")
    print(f"Criteria: {len(criteria)} ({', '.join(sp.criterion_id for sp, _ in criteria)})")
    print(f"Call budget (uncached): S0 <= {2 * len(sample)}, S2 <= {2 * len(sample) * len(semantic)} "
          f"(main + at most one repair each)")
    if args.dry_run:
        print("--dry-run: no OpenAI calls made, nothing written.")
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = await run_eval(sample, criteria, args, out)
    print(json.dumps({k: summary[k] for k in ("classifications", "label_distribution", "s2_criterion_results",
                                              "s2_repaired", "s2_failed", "flags", "api",
                                              "estimated_cost_usd")}, indent=2))
    print(f"Report: {out / 'report.md'}  (contains CV text — keep on the server)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output directory (on the server)")
    ap.add_argument("--criteria", default=str(DEFAULT_CRITERIA), help="criteria fixture JSON")
    ap.add_argument("--criterion", action="append", help="only these criterion ids (repeatable)")
    ap.add_argument("--application-id", action="append", help="explicit application ids (repeatable)")
    ap.add_argument("--max-cvs", type=int, default=25)
    ap.add_argument("--max-per-job", type=int, default=3)
    ap.add_argument("--min-text-chars", type=int, default=800)
    ap.add_argument("--seed", default="s2-eval-1", help="deterministic sample order seed")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--cache-dir", help="S0/S2 file cache (default <out>/cache)")
    ap.add_argument("--price-in", type=float, default=DEFAULT_PRICE_IN, help="USD per 1M input tokens")
    ap.add_argument("--price-out", type=float, default=DEFAULT_PRICE_OUT, help="USD per 1M output tokens")
    ap.add_argument("--dry-run", action="store_true", help="select and print the sample only")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
