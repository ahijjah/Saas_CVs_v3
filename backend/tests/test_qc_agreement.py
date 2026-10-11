"""
P4c deterministic context resolver (services.qualifying_context.agreement, qcr-1): canonical comparator and
the approved truth table. Pure data in, ContextResolution-shaped dict out. No model, no DB. JDs SYNTHETIC.
"""
import ast
import copy
import inspect
from pathlib import Path

import pytest

from services.qualifying_context import agreement as ag
from services.qualifying_context.runner import jd_sha256
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.schema import ContextResolution

BACKEND = Path(__file__).resolve().parent.parent

JD = ("Requirements\n"
      "- Minimum 5 years as an Internal Auditor in Islamic banking institutions in the GCC region.\n"
      "- Experience with government entities on infrastructure projects.\n"
      "- خبرة 5 سنوات في البنوك الإسلامية وبالبنوك التجارية.")
OTHER_JD = JD + "\n- Fluent English."
CUR = jd_sha256(JD)
FULL = "Islamic banking institutions in the GCC region"


def reading(*cands, status="ok", compound=False):
    """S1 reading: positional (text, scope) pairs; settings = the scope-"all" texts."""
    cs = [{"text": t, "scope": s} for t, s in cands]
    return {"status": status, "settings": [c["text"] for c in cs if c["scope"] == "all"], "candidates": cs,
            "compound": compound}


def analysis(state=None, contexts=(), *, source="analysis", jd_hash=CUR, provenance=None, latest="ok",
             qc=None):
    a = {"experience": {"minimum_years": 5, "relevant_roles": ["Internal Auditor"]}}
    if qc is not None:
        a["experience"]["qualifying_context"] = qc
    elif state is not None:
        a["experience"]["qualifying_context"] = {"state": state, "contexts": list(contexts), "source": source}
    cur = {"source": source}
    if jd_hash is not None:
        cur["jd_sha256"] = jd_hash
    if provenance is not None:
        cur["provenance"] = provenance
    a["qualifying_context_audit"] = {"schema": "qc_audit_v1", "current": cur if state or qc else None,
                                     "latest_run": {"status": latest}}
    return a


def res(r, a, jd=JD):
    out = ag.resolve_context(r, a, jd)
    ContextResolution.from_dict(out)                       # always a valid ContextResolution
    return out


def sd(out):
    return out["status"], out["detail"]


# ── comparator ───────────────────────────────────────────────────────────────

class TestCanon:
    @pytest.mark.parametrize("a,b", [
        ("GCC region", "the GCC region"), ("GCC region", "in the GCC region"), ("GCC  Region", "gcc region"),
        ("GCC region.", "(GCC region)"), ("government entities", "within government entities"),
        ("البنوك الإسلامية", "في البنوك الإسلامية"),
    ])
    def test_equal(self, a, b):
        assert ag.canon(a) == ag.canon(b)

    @pytest.mark.parametrize("a,b", [
        ("GCC region", "GCC regions"),                        # no plural folding
        ("GCC region", "region"),                             # only LEADING closed-list tokens are stripped
        ("GCC region", "GCC region in"),                      # never trailing
        ("government entities", "public sector"),             # no synonyms
        ("banks", "البنوك"),                                  # no translation
        ("Internal audit of banks", "audit of banks"),
    ])
    def test_not_equal(self, a, b):
        assert ag.canon(a) != ag.canon(b)

    def test_arabic_proclitic_needs_grounding(self):
        jd = JDText(JD)
        assert ag.same_phrase("البنوك التجارية", "بالبنوك التجارية", jd)        # both in the JD
        assert not ag.same_phrase("البنوك الاسلامية", "بالبنوك الاسلامية", jd)   # not grounded (different spelling)
        assert not ag.same_phrase("البنوك", "مالبنوك", jd)                         # not a proclitic chain
        assert not ag.same_phrase("banks", "lbanks", jd)                           # Latin script unaffected

    @pytest.mark.parametrize("qc,s1,rel", [
        ([FULL], [FULL], "equal"),
        (["GCC region", "government entities"], ["government entities", "the GCC region"], "equal"),
        ([], [], "equal"),
        ([FULL], ["Islamic banking institutions", "GCC region"], "split_of"),
        (["Islamic banking institutions", "GCC region"], [FULL], "merge_of"),
        (["government entities", "infrastructure projects"], ["government entities"], "subset"),
        (["government entities"], ["government entities", "infrastructure projects"], "superset"),
        (["government entities", "GCC region"], ["government entities", "infrastructure projects"], "overlap"),
        (["GCC region"], ["infrastructure projects"], "disjoint"),
        (["GCC region"], [], "identified_vs_none"),
        ([], ["GCC region"], "none_vs_identified"),
        (None, ["GCC region"], "not_compared"),
        (["GCC region"], None, "not_compared"),
    ])
    def test_relations(self, qc, s1, rel):
        assert ag.compare(qc, s1, JDText(JD)) == rel


# ── truth table ──────────────────────────────────────────────────────────────

class TestNoUsableObject:
    @pytest.mark.parametrize("a", [
        {}, None, "garbage", {"experience": {}}, analysis(),
        analysis(qc="garbage"), analysis(qc={"state": "none", "contexts": []}),              # missing key
        analysis(qc={"state": "none", "contexts": [], "source": "analysis", "x": 1}),        # extra key
        analysis(qc={"state": "identified", "contexts": [], "source": "analysis"}),          # inconsistent
        analysis(qc={"state": "none", "contexts": ["x"], "source": "analysis"}),
        analysis(qc={"state": "identified", "contexts": [" "], "source": "analysis"}),
        analysis(qc={"state": "identified", "contexts": ["x", "x"], "source": "analysis"}),
        analysis(qc={"state": "maybe", "contexts": [], "source": "analysis"}),
        analysis(qc={"state": "none", "contexts": [], "source": "ai"}),
        analysis(qc={"state": "identified", "contexts": "GCC", "source": "analysis"}),
    ])
    def test_absent_or_malformed_is_unassessed_never_none(self, a):
        out = res(reading(), a)
        assert sd(out) == ("unconfirmed", "unassessed") and out["effective"] is None

    @pytest.mark.parametrize("latest", ["failed_technical", "failed_validation"])
    def test_failed_run_without_object_is_qc_failed(self, latest):
        out = res(reading(), analysis(latest=latest))
        assert sd(out) == ("unconfirmed", "qc_failed") and out["effective"] is None

    def test_failed_run_with_a_kept_object_is_judged_as_that_object(self):
        out = res(reading(), analysis("none", latest="failed_technical"))
        assert sd(out) == ("resolved", "agreed_none")
        out = res(reading(), analysis("none", latest="failed_technical", jd_hash="0" * 64))
        assert sd(out) == ("unconfirmed", "stale")


class TestAnalysisSource:
    @pytest.mark.parametrize("h,flag", [("0" * 64, "jd_changed_since_decision"), (None, "jd_version_unknown"),
                                        ("", "jd_version_unknown"), (jd_sha256(JD.strip() + "\n"),
                                                                     "jd_changed_since_decision")])
    def test_stale(self, h, flag):
        out = res(reading((FULL, "all")), analysis("identified", [FULL], jd_hash=h))
        assert sd(out) == ("unconfirmed", "stale") and out["effective"] is None
        assert out["record"]["flags"] == [flag]

    def test_the_raw_description_hash_is_used(self):
        a = analysis("identified", [FULL])
        assert sd(res(reading((FULL, "all")), a, JD)) == ("resolved", "agreed")
        assert sd(res(reading((FULL, "all")), a, OTHER_JD)) == ("unconfirmed", "stale")
        assert res(reading(), a)["record"]["current_jd_sha256"] == CUR

    def test_uncertain(self):
        for r in (reading(), reading((FULL, "all")), reading(status="unavailable")):
            assert sd(res(r, analysis("uncertain"))) == ("unconfirmed", "uncertain")

    @pytest.mark.parametrize("r", [reading(status="unavailable"), {"status": "pending"}, None, "x",
                                   {"status": "ok", "settings": None, "candidates": []},
                                   {"status": "ok", "settings": [], "candidates": [{"text": "x"}]}])
    def test_s1_unavailable_is_never_none(self, r):
        for a in (analysis("none"), analysis("identified", [FULL])):
            out = res(r, a)
            assert sd(out) == ("unconfirmed", "s1_unavailable") and out["effective"] is None
            assert out["record"]["comparison"]["relation"] == "not_compared"
            assert out["record"]["comparison"]["s1_canonical"] is None

    def test_agreed_uses_the_stored_text(self):
        out = res(reading(("the GCC region", "all")), analysis("identified", ["GCC region"]))
        assert sd(out) == ("resolved", "agreed")
        assert out["effective"] == {"state": "identified", "contexts": ["GCC region"], "provenance": "jd_verified"}

    def test_agreed_several_contexts_order_free(self):
        out = res(reading(("infrastructure projects", "all"), ("government entities", "all")),
                  analysis("identified", ["government entities", "infrastructure projects"]))
        assert sd(out) == ("resolved", "agreed")
        assert out["effective"]["contexts"] == ["government entities", "infrastructure projects"]

    @pytest.mark.parametrize("cands", [
        [("Islamic banking institutions", "all"), ("GCC region", "all")],       # split -> disagreement
        [(FULL, "all"), ("government entities", "all")],                        # superset
        [],                                                                     # S1 found nothing
        [(FULL, "softened")],                                                   # same text, unclear scope
        [(FULL, "one_alternative")],
        [(FULL, "part_duration")],
        [(FULL, "all"), ("government entities", "softened")],                  # equal "all" set + a scoped extra
    ])
    def test_identified_disagreement(self, cands):
        out = res(reading(*cands), analysis("identified", [FULL]))
        assert sd(out) == ("unconfirmed", "disagreement") and out["effective"] is None

    def test_none_agrees_only_with_no_candidate_at_all(self):
        assert sd(res(reading(), analysis("none"))) == ("resolved", "agreed_none")
        assert res(reading(), analysis("none"))["effective"] == {"state": "none", "contexts": [],
                                                                 "provenance": "jd_verified"}
        for cands in ([("GCC region", "all")], [("GCC region", "softened")], [("GCC region", "one_alternative")],
                      [("GCC", "part_duration")]):
            out = res(reading(*cands), analysis("none"))
            assert sd(out) == ("unconfirmed", "disagreement"), cands

    def test_compound_flag_does_not_create_agreement(self):
        # a compound reading with no candidate still agrees on "none" here; the compound reason blocks the view
        out = res(reading(compound=True), analysis("none"))
        assert sd(out) == ("resolved", "agreed_none") and out["record"]["s1"]["compound"] is True


class TestRecruiter:
    @pytest.mark.parametrize("prov", ["recruiter_confirmed", "recruiter_edited"])
    def test_identified_governs_whatever_s1_says(self, prov):
        for r in (reading(), reading(("infrastructure projects", "all")), reading(status="unavailable"),
                  reading((FULL, "softened"))):
            out = res(r, analysis("identified", ["GCC region"], source="recruiter", provenance=prov))
            assert sd(out) == ("resolved", "recruiter")
            assert out["effective"] == {"state": "identified", "contexts": ["GCC region"], "provenance": prov}
            assert out["record"]["flags"] == []

    def test_comparison_is_recorded_for_recruiter_decisions(self):
        out = res(reading(("infrastructure projects", "all")),
                  analysis("identified", ["GCC region"], source="recruiter", provenance="recruiter_edited"))
        assert out["record"]["comparison"]["relation"] == "disjoint"

    @pytest.mark.parametrize("h,flag", [("0" * 64, "jd_changed_since_decision"), (None, "jd_version_unknown")])
    def test_identified_after_a_jd_change_stays_resolved_with_a_flag(self, h, flag):
        out = res(reading(), analysis("identified", ["GCC region"], source="recruiter",
                                      provenance="recruiter_edited", jd_hash=h))
        assert sd(out) == ("resolved", "recruiter") and out["record"]["flags"] == [flag]

    def test_none_on_the_current_jd(self):
        out = res(reading((FULL, "all")), analysis("none", source="recruiter", provenance="recruiter_edited"))
        assert sd(out) == ("resolved", "recruiter")
        assert out["effective"] == {"state": "none", "contexts": [], "provenance": "recruiter_edited"}
        assert out["record"]["comparison"]["relation"] == "none_vs_identified"

    @pytest.mark.parametrize("h,flag", [("0" * 64, "jd_changed_since_decision"), (None, "jd_version_unknown")])
    def test_none_after_a_jd_change_or_without_hash_is_stale(self, h, flag):
        out = res(reading(), analysis("none", source="recruiter", provenance="recruiter_confirmed", jd_hash=h))
        assert sd(out) == ("unconfirmed", "stale") and out["effective"] is None
        assert flag in out["record"]["flags"]

    def test_unknown_provenance_is_flagged_and_still_recruiter(self):
        out = res(reading(), analysis("identified", ["GCC region"], source="recruiter"))
        assert sd(out) == ("resolved", "recruiter")
        assert out["effective"]["provenance"] == "recruiter_edited"
        assert "recruiter_provenance_unknown" in out["record"]["flags"]

    def test_recruiter_uncertain_is_never_resolved(self):
        out = res(reading(), analysis("uncertain", source="recruiter", provenance="recruiter_edited"))
        assert sd(out) == ("unconfirmed", "uncertain")


class TestProperties:
    def test_record_shape(self):
        out = res(reading((FULL, "all")), analysis("identified", [FULL]))
        rec = out["record"]
        assert rec["schema"] == "qc_resolution_v1" and rec["resolver_version"] == "qcr-1"
        assert set(rec) == {"schema", "resolver_version", "analysis", "analysis_valid", "analysis_present",
                            "latest_run_status", "s1", "comparison", "current_jd_sha256", "flags"}
        assert rec["comparison"]["qc_canonical"] == [ag.canon(FULL)]

    def test_deterministic_and_pure(self):
        a = analysis("identified", [FULL])
        r = reading((FULL, "all"))
        a0, r0 = copy.deepcopy(a), copy.deepcopy(r)
        assert ag.resolve_context(r, a, JD) == ag.resolve_context(r, a, JD)
        assert a == a0 and r == r0

    def test_exhaustive_never_none_without_agreement_or_recruiter(self):
        """Over every QC object x S1 reading x hash in the grid, an effective "none" only ever comes from an
        agreed_none (fresh analysis none + no S1 candidate) or a fresh recruiter none."""
        qcs = [None, "garbage"] + [{"state": s, "contexts": c, "source": src}
                                   for s, c in (("identified", [FULL]), ("none", []), ("uncertain", []))
                                   for src in ("analysis", "recruiter")]
        reads = [reading(), reading((FULL, "all")), reading((FULL, "softened")), reading(status="unavailable"),
                 None]
        for qc in qcs:
            for r in reads:
                for h in (CUR, "0" * 64, None):
                    for latest in ("ok", "failed_technical"):
                        a = analysis(qc=qc, jd_hash=h, latest=latest, provenance="recruiter_edited") \
                            if qc is not None else analysis(latest=latest)
                        out = res(r, a)
                        eff = out["effective"]
                        if eff is not None and eff["state"] == "none":
                            assert isinstance(qc, dict) and qc["state"] == "none" and h == CUR
                            if qc["source"] == "analysis":
                                assert out["detail"] == "agreed_none" and r == reading()
                        if out["status"] == "resolved" and out["detail"] in ("agreed", "agreed_none"):
                            assert h == CUR and isinstance(qc, dict) and qc["source"] == "analysis"

    def test_signature_is_plain_data(self):
        assert list(inspect.signature(ag.resolve_context).parameters) == ["s1_reading", "analysis_json",
                                                                          "current_jd_text"]

    def test_module_imports(self):
        tree = ast.parse((BACKEND / "services" / "qualifying_context" / "agreement.py").read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert mods == {"__future__", "unicodedata", "typing", "services.qualifying_context.runner",
                        "services.s1_requirements.jd_text"}
