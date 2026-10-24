"""CSV export of the configured matrix.

Exactly the visible columns, in the visible order, for exactly the filtered and sorted rows. Statuses are words, in the requested
language. The file is UTF-8 with a byte-order mark (so spreadsheet software reads Arabic correctly). Evidence quotes, confidence values
and the contents of the CV are never columns here. A cell that begins with = + - @ or a control character is prefixed with an
apostrophe so a spreadsheet cannot run it as a formula.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services.matrix import labels as L
from services.matrix import registry as R

BOM = "﻿"
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _summary(lang: str, counts: Sequence[tuple[str, Any]], total: Any) -> str:
    words = L.SUMMARY["ar" if lang == "ar" else "en"]
    return " · ".join([f"{words[k]} {v}" for k, v in counts] + [f"{words['total']} {total}"])


def cell(lang: str, column: R.Column, row: Mapping[str, Any]) -> str:
    cid = column.id
    if cid == R.NAME:
        return row["candidate_name"] or ""
    if cid == "candidate.applied_at":
        return (row["applied_at"] or "")[:10]
    if cid == "candidate.source":
        return (row["source"] or "").replace("_", " ")
    if cid == "candidate.assigned_to":
        return (row["assigned_to"] or {}).get("name") or ""
    if cid == "candidate.tags":
        return "; ".join(t["name"] for t in row["tags"])
    if cid == "decision.eligibility":
        return L.status(lang, row["eligibility"])
    if cid == "decision.score":
        return "" if row["score"] is None else str(row["score"])
    if cid == "decision.scoring":
        s = row["scoring"]
        return f"{L.status(lang, s['state'])} ({L.status(lang, s['method'])})" if s["method"] != "none" else L.status(lang, "none")
    if cid == "decision.flags":
        return "; ".join(f"{L.FLAG['ar' if lang == 'ar' else 'en'][e['kind']]} {e['count']}" for e in row["flags"]["items"])
    if cid == "workflow.status":
        return L.workflow(lang, row["workflow_status"])
    if cid == "mandatory.summary":
        m = row["mandatory"]
        return L.status(lang, "not_assessed") if not m["assessed"] else _summary(
            lang, [("met", m["met"]), ("partially_met", m["partially_met"]), ("not_met", m["not_met"]), ("unclear", m["unclear"])], m["total"])
    if cid == "preferred.summary":
        p = row["preferred"]
        return L.status(lang, "not_assessed") if not p["assessed"] else _summary(
            lang, [("yes", p["yes"]), ("no", p["no"]), ("unclear", p["unclear"])], p["total"])
    if cid == "gates.summary":
        g = row["gates"]
        return L.status(lang, "not_assessed") if not g["assessed"] else _summary(
            lang, [("passed", g["passed"]), ("failed", g["failed"]), ("review_required", g["review_required"]), ("not_assessed", g["not_assessed"])], g["total"])
    found = row["cells"].get(cid)
    return L.status(lang, found["status"]) if found else L.status(lang, "not_assessed")


def title(lang: str, column: R.Column) -> str:
    return column.label_ar if lang == "ar" else column.label_en


def describe_filters(lang: str, filters: Mapping[str, Any]) -> str:
    parts = []
    for key, value in filters.items():
        if value in (None, [], ""):
            continue
        if isinstance(value, list):
            value = ", ".join(str(v.get("item_id") or v.get("gate_key")) + "=" + "/".join(v["results"]) if isinstance(v, dict) else str(v) for v in value)
        parts.append(f"{key}={value}")
    return "; ".join(parts) or L.META["ar" if lang == "ar" else "en"]["none"]


def build_csv(*, lang: str, columns: Sequence[R.Column], rows: Sequence[Mapping[str, Any]], meta: Mapping[str, Any] | None) -> bytes:
    words = L.META["ar" if lang == "ar" else "en"]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    if meta is not None:
        writer.writerow([safe(words["title"])])
        writer.writerow([words["job"], safe(meta.get("job"))])
        if meta.get("client"):
            writer.writerow([words["client"], safe(meta["client"])])
        if meta.get("organization"):
            writer.writerow([words["organization"], safe(meta["organization"])])
        writer.writerow([words["exported"], datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")])
        writer.writerow([words["filters"], safe(meta.get("filters"))])
        writer.writerow([words["sort"], safe(meta.get("sort"))])
        writer.writerow([words["rows"], len(rows)])
        writer.writerow([safe(words["note"])])
        writer.writerow([])
    writer.writerow([safe(title(lang, c)) for c in columns])
    for row in rows:
        writer.writerow([safe(cell(lang, c, row)) for c in columns])
    return (BOM + out.getvalue()).encode("utf-8")
