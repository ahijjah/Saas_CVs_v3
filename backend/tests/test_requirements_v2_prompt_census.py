"""The stored prompt census (audit evidence) must equal a fresh offline census of the two prompt files, and the totals that the
audit report cites must hold. Offline: reads the prompt files only."""
import json
import pathlib

import scripts.requirements_v2_prompt_census as C

STORED = pathlib.Path(__file__).parent / "fixtures" / "requirements_v2_technical_coordinator_20" / "audit" / "prompt_census.json"


def test_the_stored_census_equals_a_fresh_census():
    stored = json.loads(STORED.read_text(encoding="utf-8"))
    fresh = {name: C.census(path) for name, path in C.PROMPTS.items()}
    assert stored == fresh


def test_the_census_totals_the_audit_cites():
    stored = json.loads(STORED.read_text(encoding="utf-8"))
    for version in ("v2-3", "v2-4"):
        d = stored[version]
        assert d["totals"]["from_responsibilities_items"] == 6          # duties shown in the five answers, in total
        assert d["totals"]["soft_skills_items"] == 0                    # no soft-skill item is shown anywhere
        assert d["totals"]["items_with_alternatives"] == 3
        assert d["reporting_line_routes"] == 0                          # no example routes a reporting statement
        assert d["ability_statements"] == 0                             # no "Ability" statement is shown
        assert len(d["answers"]) == 5


def test_the_prompts_are_the_pinned_files():
    stored = json.loads(STORED.read_text(encoding="utf-8"))
    assert stored["v2-3"]["sha256"] == "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"
    assert stored["v2-4"]["sha256"] == "dd2651bcda14ae816c89d33dd08ecb4aefa5d63e2e51dfbe7d2b39f8b1989a9e"
