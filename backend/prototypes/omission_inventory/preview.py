"""Renders the PROPOSED inventory-guided request for TC20 to a text file. It is never sent: no model is called.

The request gives the model the numbered requirement units (ids from the inventory) and asks for the SAME output contract as today,
plus one field: "unit_decisions", which must name every requirement unit id exactly once. Quotes stay verbatim. Headings and background
are listed for context only and are not requirement units.
Activation of any prompt, and any pilot call, need approval first (a cap before the first request).
"""
from __future__ import annotations

import json
import pathlib

import inventory as I
import replay as R

OUT = R.HERE / "preview" / "inventory_guided_request.txt"

INSTRUCTIONS = """You extract requirements from the job description below.

Output contract: one JSON object with the same categories as today, plus "unit_decisions".
- Every item must quote its source_text exactly, character for character, from ONE requirement unit. Do not paraphrase.
- Every requirement unit id listed below must appear exactly once in "unit_decisions", with one of:
    "extracted"        the unit is covered by one or more items (name them in "items")
    "condition"        the unit is a condition (location, reporting), placed in non_scoreable_requirements or informational_items
    "not_a_requirement" the unit is context with no requirement in it
- Do not invent a unit id. If a unit is unclear, say so in "warnings" and still give it a decision.
"""


def render(jd: str, inv: I.Inventory) -> str:
    units = [u for u in inv.requirements()]
    lines = [INSTRUCTIONS, "Requirement units (verbatim; quote from these):"]
    for u in units:
        lines.append(f"[{u.unit_id}] {u.text}")
    context = [u for u in inv.units if u.kind != "requirement"]
    lines.append("")
    lines.append("Context (headings and background; not requirement units, do not list them):")
    for u in context:
        lines.append(f"- {u.kind}: {u.text.strip()}")
    lines.append("")
    lines.append(f"Requirement unit count: {len(units)}. Each id above must appear exactly once in unit_decisions.")
    return "\n".join(lines) + "\n"


def main() -> int:
    jd, _ = R.load_jd()
    inv = I.build_inventory(jd, json.loads(R.ANNOTATION.read_text(encoding="utf-8")))
    text = render(jd, inv)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(inv.requirements())} requirement units)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
