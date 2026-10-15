"""Requirements-v2 duplicate / similarity warnings: the pure comparison helper (labelled fixture table, English +
Arabic) and its exposure through the requirements GET / save responses. Informational only: nothing is merged, removed,
reclassified, re-weighted or blocked. No model, no network, no database."""
from __future__ import annotations

import ast
import copy
import json
import pathlib
import time

import pytest

from test_requirements_v2_api import (  # noqa: F401  (autouse/api fixtures, the in-memory database and helpers)
    JOB, Store, _real_modules, api, client_doc, item, raw_struct, save, seed, user, view,
)
from test_requirements_v2_classification_ack import extract, flagged_job, ids_by_text, raw_item
from services.requirements_v2 import CATEGORIES, compare_items, find_similar_items
from services.requirements_v2 import similarity as sim

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PAIRS = json.loads((BACKEND / "tests" / "fixtures" / "requirements_v2_similarity" / "pairs.json").read_text(encoding="utf-8"))["pairs"]


def as_item(p):
    return {"text": p["text"], "alternatives": p["alternatives"], "experience": p["experience"]}


class TestFixtureTable:
    """The labelled pairs the thresholds were chosen on. `pytest -s` prints the scores."""

    @pytest.mark.parametrize("pair", PAIRS, ids=[p["id"] for p in PAIRS])
    def test_each_pair_gets_the_expected_label_and_differences(self, pair):
        res = compare_items(as_item(pair["a"]), as_item(pair["b"]))
        assert (res.kind if res else None) == pair["expected"], (pair["note"], res)
        assert (list(res.differences) if res else []) == pair["differences"]
        flipped = compare_items(as_item(pair["b"]), as_item(pair["a"]))
        assert (flipped.kind if flipped else None) == pair["expected"]                    # argument order is irrelevant

    def test_the_table_covers_the_required_situations(self):
        ids = {p["id"] for p in PAIRS}
        for needed in ("en-exact-cross-category", "ar-exact-alef-variants", "en-different-years", "ar-different-years",
                       "en-alternatives-differ", "en-or-vs-and", "en-negation", "ar-negation", "en-shared-keyword-sql",
                       "ar-shared-keyword", "cross-language", "en-synonyms"):
            assert needed in ids
        assert {p["expected"] for p in PAIRS} == {None, "possible_duplicate", "similar_requirement"}

    def test_thresholds_are_the_documented_ones(self):
        assert (sim.DUP_OVERLAP, sim.DUP_RATIO, sim.SIM_OVERLAP, sim.SIM_RATIO, sim.MIN_TOKENS_FOR_SIMILAR) == (0.85, 0.90, 0.60, 0.70, 2)
        doc = sim.__doc__
        for text in ("token_overlap >= 0.85", "string_ratio >= 0.90", "token_overlap >= 0.60", "string_ratio >= 0.70",
                     "Different languages are never compared", "Negation", "Number WORDS", "makes no claim of semantic accuracy"):
            assert text.lower() in doc.lower()

    def test_every_non_reported_pair_sits_below_the_similar_threshold_or_is_gated(self):
        for p in PAIRS:
            if p["expected"] is not None:
                continue
            a, b = as_item(p["a"]), as_item(p["b"])
            fa, fb = sim.features(a["text"], a["alternatives"]), sim.features(b["text"], b["alternatives"])
            gated = fa.script != fb.script or fa.negated != fb.negated or min(len(set(fa.tokens)), len(set(fb.tokens))) < 2
            overlap = sim._jaccard(set(fa.tokens), set(fb.tokens))
            assert gated or overlap < sim.SIM_OVERLAP, (p["id"], overlap)


class TestHelperRules:

    def test_identical_wording_in_one_category_is_a_possible_duplicate(self):
        r = compare_items({"text": "Docker"}, {"text": "docker"})
        assert r.kind == "possible_duplicate" and r.differences == ()

    def test_a_single_shared_keyword_is_never_enough(self):
        for a, b in (("Python scripting for data pipelines", "Python certification"), ("Excel", "Excel and financial modelling"),
                     ("Leadership of engineering teams", "Leadership"), ("Project", "Project management")):
            assert compare_items({"text": a}, {"text": b}) is None

    def test_numbers_alternatives_and_connectives_stop_a_duplicate(self):
        assert compare_items({"text": "3 years Python backend development"}, {"text": "5 years Python backend development"}).kind == "similar_requirement"
        same = {"text": "Docker or Podman", "alternatives": ["Docker", "Podman"]}
        assert compare_items(same, copy.deepcopy(same)).kind == "possible_duplicate"
        assert compare_items(same, {"text": "Docker or Podman", "alternatives": ["Docker", "Podman", "LXC"]}).differences == ("alternatives",)

    def test_structured_years_differ_even_if_the_wording_hides_it(self):
        a = {"text": "Maintenance planning", "experience": {"subject": "Maintenance planning", "min_years": 3}}
        b = {"text": "Maintenance planning", "experience": {"subject": "Maintenance planning", "min_years": 8}}
        r = compare_items(a, b)
        assert r.kind == "similar_requirement" and r.differences == ("numbers",)
        c = {"text": "Maintenance planning", "experience": {"subject": "Reliability", "min_years": 3}}
        assert "subject" in compare_items(a, c).differences

    def test_very_short_wordings_that_differ_by_one_number_are_not_reported(self):
        """Documented limitation: with only two content tokens in common the overlap (0.5) is below the threshold."""
        assert compare_items({"text": "4 years Planner"}, {"text": "7 years Planner"}) is None

    def test_empty_and_stopword_only_items_are_ignored(self):
        assert compare_items({"text": ""}, {"text": ""}) is None
        assert compare_items({"text": "of the"}, {"text": "of the"}) is None

    def test_normalisation_handles_arabic_forms_and_digits(self):
        assert sim.normalize("أَحْمَدـ") == sim.normalize("احمد")
        assert sim.features("خبرة ٥ سنوات").numbers == sim.features("خبرة 5 سنوات").numbers == frozenset({"5"})

    def test_a_mixed_arabic_english_item_is_compared_on_its_shared_tokens(self):
        r = compare_items({"text": "خبرة في استخدام Docker و Kubernetes"}, {"text": "خبرة في استخدام Docker و Kubernetes"})
        assert r.kind == "possible_duplicate"


class TestDocumentLevel:

    def doc(self):
        r = extract("Requirements:\n- SQL\n- SQL databases\n- Python scripting\n",
                    raw_struct("SQL"), raw_struct("SQL databases", importance="preferred", cue=None),
                    raw_struct("Python scripting"))
        return r.requirements

    def test_pairs_are_found_within_and_across_categories_with_ids_and_categories(self):
        r = extract("Requirements:\n- SQL\n- SQL\n- Docker\n- Docker\n",
                    raw_struct("SQL"), raw_struct("SQL", "domain_knowledge", importance="preferred"),
                    raw_struct("Docker"), raw_struct("docker"))
        doc = r.requirements
        ids = {(c, i["text"]): i["id"] for c in CATEGORIES for i in doc["categories"][c]["items"]}
        warnings = find_similar_items(doc)
        by = {w["id"]: w for w in warnings}
        cross = by[f"{ids[('skills', 'SQL')]}~{ids[('domain_knowledge', 'SQL')]}"]
        assert cross["kind"] == "possible_duplicate" and cross["same_category"] is False
        assert [(x["category"], x["importance"]) for x in cross["items"]] == [("skills", "required"), ("domain_knowledge", "preferred")]
        within = by[f"{ids[('skills', 'Docker')]}~{ids[('skills', 'docker')]}"]
        assert within["same_category"] is True and {x["item_id"] for x in within["items"]} == {ids[("skills", "Docker")], ids[("skills", "docker")]}
        assert all(w["method"] == "lexical_v1" for w in warnings) and len(warnings) == 2

    def test_the_helper_never_modifies_the_document(self):
        doc = self.doc()
        before = copy.deepcopy(doc)
        find_similar_items(doc)
        assert doc == before

    def test_arabic_document_pairs(self):
        r = extract("المتطلبات:\n- خبرة 4 سنوات في تطوير البرمجيات\n- خبرة 7 سنوات في تطوير البرمجيات\n- إدارة المشاريع\n- ادارة المشاريع\n",
                    raw_struct("خبرة 4 سنوات في تطوير البرمجيات", "experience"), raw_struct("خبرة 7 سنوات في تطوير البرمجيات", "experience"),
                    raw_struct("إدارة المشاريع", "soft_skills"), raw_struct("ادارة المشاريع", "other_requirements"))
        kinds = sorted((w["kind"], tuple(w["differences"])) for w in find_similar_items(r.requirements))
        assert kinds == [("possible_duplicate", ()), ("similar_requirement", ("numbers",))]

    def test_seventy_items_compare_quickly(self):
        doc = self.doc()
        for n, c in enumerate(CATEGORIES * 10):
            doc["categories"][c]["items"].append({"id": f"req_x{n:04d}", "text": f"Requirement number {n} about topic {n % 7}",
                                                  "importance": "preferred", "weight": None, "origin": "recruiter_added",
                                                  "source_text": None, "alternatives": None, "experience": None})
        t = time.perf_counter()
        find_similar_items(doc)
        assert time.perf_counter() - t < 2.0

    def test_the_module_is_pure_and_stdlib_only(self):
        tree = ast.parse((BACKEND / "services" / "requirements_v2" / "similarity.py").read_text(encoding="utf-8"))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                names.add((n.module or "").split(".")[0])
        assert names <= {"__future__", "re", "unicodedata", "dataclasses", "difflib", "typing", "services"}


# ══ through the API responses ═══════════════════════════════════════════════════════════════════════════════════

def dup_job():
    r = extract("Requirements:\n- Python\n- SQL\n- SQL\n",
                raw_struct("Python"), raw_struct("SQL"), raw_struct("SQL", "domain_knowledge", importance="preferred"))
    assert r.requirements
    return r


class TestApi:

    @pytest.mark.asyncio
    async def test_get_returns_the_warnings_with_ids_categories_and_method(self, api):
        store = Store()
        r = seed(store, dup_job())
        v = await view(api, store)
        ids = {(c, i["text"]): i["id"] for c in CATEGORIES for i in r.requirements["categories"][c]["items"]}
        (w,) = v["similarity_warnings"]
        assert w["kind"] == "possible_duplicate" and w["same_category"] is False
        assert [(x["item_id"], x["category"]) for x in w["items"]] == [(ids[("skills", "SQL")], "skills"), (ids[("domain_knowledge", "SQL")], "domain_knowledge")]
        assert v["similarity_method"] == "lexical_v1"

    @pytest.mark.asyncio
    async def test_a_save_returns_recomputed_warnings_and_never_blocks_or_changes_anything(self, api):
        store = Store(policy="false")
        seed(store, dup_job())
        v = await view(api, store)
        doc = client_doc(v)
        doc["categories"]["skills"]["items"].append({"text": "Python", "importance": "required", "weight": None})
        for it_, w in zip(doc["categories"]["skills"]["items"], (34, 33, 33)):
            it_["weight"] = w
        out = await save(api, store, doc, 0)                  # a second Python in the same category
        assert out["revision"] == 1 and len(out["similarity_warnings"]) == 2
        assert {w["kind"] for w in out["similarity_warnings"]} == {"possible_duplicate"}
        skills = out["requirements"]["categories"]["skills"]["items"]
        assert [(i["text"], i["importance"], i["weight"]) for i in skills] == [("Python", "required", 34), ("SQL", "required", 33), ("Python", "required", 33)]
        assert out["readiness"]["state"] == "ready"           # similarity never blocks readiness or saving; all items kept

    @pytest.mark.asyncio
    async def test_warnings_follow_item_edits_both_ways(self, api):
        store = Store()
        seed(store, dup_job())
        v = await view(api, store)
        assert len(v["similarity_warnings"]) == 1
        doc = client_doc(v)
        item(doc, "SQL")[1]["text"] = "Kubernetes orchestration"           # first SQL (skills) is renamed
        out = await save(api, store, doc, 0)
        assert out["similarity_warnings"] == []
        doc = client_doc(out)
        item(doc, "Kubernetes orchestration")[1]["text"] = "sql"
        out = await save(api, store, doc, 1)
        assert [w["kind"] for w in out["similarity_warnings"]] == ["possible_duplicate"]
        assert (await view(api, store))["similarity_warnings"] == out["similarity_warnings"]

    @pytest.mark.asyncio
    async def test_warnings_are_not_stored_audited_or_part_of_readiness(self, api):
        store = Store(policy="false")
        seed(store, dup_job())
        doc = client_doc(await view(api, store))
        item(doc, "Python")[1]["text"] = "Python 3"
        out = await save(api, store, doc, 0)
        stored = json.dumps(store.rows[JOB]["analysis"])
        assert "similar" not in stored and "possible_duplicate" not in stored
        assert not [a for a in store.actions() if "similar" in a or "duplicate" in a]
        assert all("similar" not in r["code"] and "duplicate" not in r["code"] for r in out["readiness"]["reasons"])
        assert out["similarity_warnings"] and out["readiness"]["state"] == "ready"

    @pytest.mark.asyncio
    async def test_warnings_are_independent_of_classification_acknowledgment(self, api):
        store = Store()
        r = extract("Requirements:\n- Python is required\n- Docker\n- Docker\n",
                    raw_item("Python", "Python is required"), raw_item("Docker", "Docker", "preferred", None),
                    raw_item("Docker", "Docker", "preferred", None, category="domain_knowledge"))
        seed(store, r)
        v = await view(api, store)
        assert v["readiness"]["state"] == "needs_classification_review" and len(v["similarity_warnings"]) == 1
        before = v["similarity_warnings"]
        for wid in list(v["readiness"]["unresolved_warning_ids"]):
            v = await api.acknowledge_warning(store.session(), user(), JOB, v["revision"], wid)
        assert v["readiness"]["state"] == "ready" and v["similarity_warnings"] == before      # unchanged by acknowledging
        assert not any("similar" in x["id"] for x in v["classification_warnings"])

    @pytest.mark.asyncio
    async def test_different_years_and_alternatives_are_reported_as_similar_with_differences(self, api):
        store = Store()
        r = extract("Requirements:\n- x\n",
                    raw_struct("4 years of experience as a Maintenance Planner", "experience", experience={"subject": "Maintenance Planner", "min_years": 4}),
                    raw_struct("7 years of experience as a Maintenance Planner", "experience", experience={"subject": "Maintenance Planner", "min_years": 7}),
                    raw_struct("SQL or PostgreSQL", alternatives=["SQL", "PostgreSQL"]),
                    raw_struct("SQL or MySQL", alternatives=["SQL", "MySQL"]))
        assert r.requirements
        seed(store, r)
        v = await view(api, store)
        assert [(w["kind"], w["differences"]) for w in v["similarity_warnings"]] == [("similar_requirement", ["numbers"])]
