#!/usr/bin/env python3
"""Offline census of the worked examples in the v2-3 and v2-4 prompt texts: which categories, origins, alternatives and routes
each embedded answer demonstrates. Reads the two prompt files only: no model, network, database or job.

  python scripts/requirements_v2_prompt_census.py > tests/fixtures/requirements_v2_technical_coordinator_20/audit/prompt_census.json
"""
import hashlib
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PROMPTS = {
    "v2-3": BACKEND / "prompt_candidates/criteria_extraction_v2-3/criteria_extraction_v2-3.txt",
    "v2-4": BACKEND / "prompt_candidates/criteria_extraction_v2-4/criteria_extraction_v2-4.txt",
}


def census(path: pathlib.Path) -> dict:
    text = path.read_bytes()
    lines = text.decode("utf-8").split("\n")
    answers = []
    for n, line in enumerate(lines, start=1):
        if not line.startswith('{"scoreability"'):
            continue
        obj = json.loads(line)
        cats = obj["categories"]
        items = [(c, i) for c, lst in cats.items() for i in lst]
        answers.append({
            "line": n,
            "status": obj["scoreability"]["status"],
            "items_per_category": {c: len(lst) for c, lst in cats.items()},
            "from_responsibilities_items": sum(1 for _, i in items if i["origin"] == "from_responsibilities"),
            "items_with_alternatives": sum(1 for _, i in items if i.get("alternatives")),
            "soft_skills_items": len(cats.get("soft_skills", [])),
            "domain_knowledge_items": len(cats.get("domain_knowledge", [])),
            "non_scoreable_routes": sorted({x["category"] for x in obj["non_scoreable_requirements"]}),
            "post_hiring_routes": sorted({x["category"] for x in obj["post_hiring_conditions"]}),
            "informational_routes": sorted({x["category"] for x in obj["informational_items"]}),
        })
    totals = {k: sum(a[k] for a in answers) for k in ("from_responsibilities_items", "items_with_alternatives", "soft_skills_items",
                                                      "domain_knowledge_items")}
    return {"file": str(path.relative_to(BACKEND)), "sha256": hashlib.sha256(text).hexdigest(), "answers": answers, "totals": totals,
            "reporting_line_routes": sum(1 for a in answers if "reporting_line" in a["informational_routes"]),
            "ability_statements": sum(1 for line in lines if line.strip().startswith("Ability") or '"text":"Ability' in line)}


def main() -> int:
    json.dump({name: census(p) for name, p in PROMPTS.items()}, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
