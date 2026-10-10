"""The numbers the editor shows for a weight finding come from the document the validator judged (services/requirements_api.py
_finding_params). The validator itself (services/requirements_v2) is frozen and is not changed here: these tests pin the payload only."""
import copy

from services import requirements_api as api
from services.requirements_v2 import Issue

from test_requirements_v2_validation import scored_doc


def _payload(doc, code, category=None, item_id=None):
    issue = Issue(code, "m", category=category, item_id=item_id)
    return api.issues_payload([issue], doc)[0]["params"]


def test_required_total_finding_carries_the_actual_expected_and_difference():
    d = scored_doc()
    d["categories"]["skills"]["items"][0]["weight"] = 60
    d["categories"]["skills"]["items"][1]["weight"] = 50
    assert _payload(d, "required_weights_total", category="skills") == {"total": 110, "expected": 100, "difference": 10}


def test_category_total_finding_carries_the_actual_expected_and_difference():
    d = scored_doc()
    d["categories"]["education"]["weight"] = 41
    assert _payload(d, "category_weights_total") == {"total": 101, "expected": 100, "difference": 1}


def test_required_weight_finding_names_the_value_it_rejected():
    d = scored_doc()
    item = d["categories"]["skills"]["items"][0]
    item["weight"] = None
    assert _payload(d, "required_weight_invalid", category="skills", item_id=item["id"]) == {"weight": None}


def test_without_the_document_no_numbers_are_invented():
    assert api.issues_payload([Issue("required_weights_total", "m", category="skills")])[0]["params"] == {}


def test_the_payload_still_has_the_original_fields():
    d = scored_doc()
    out = api.issues_payload([Issue("required_weights_total", "m", category="skills")], d)[0]
    assert set(out) == {"code", "message", "category", "item_id", "params"}
