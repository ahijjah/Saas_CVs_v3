"""Audit of the stored TC20 comparison run (v2-3 vs v2-4): the manifest matches the frozen inputs, the stored answers re-score to
the stored scored.json with the unchanged scorer, and the cost adds up to the recorded total. Offline: no network, no model."""
import hashlib
import json
import pathlib

import scripts.requirements_v2_tc20_eval as E

RUN = pathlib.Path(__file__).parent / "fixtures" / "requirements_v2_technical_coordinator_20" / "audit" / "run_tc20_v2-3_v2-4"


def load():
    calls = [json.loads(x) for x in (RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return calls, json.loads((RUN / "scored.json").read_text(encoding="utf-8")), json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))


def test_the_manifest_matches_the_frozen_inputs():
    _, _, man = load()
    jd, labels = E.load_inputs()
    assert man["labels_sha256"] == E.sha256_bytes(E.LABELS.read_bytes()) == "6a87da80e142640af1327c4a3fd5a21adb648726a9733a699fa8d3ec67135ef9"
    assert man["jd_sha256"] == labels["jd_sha256"] == hashlib.sha256(jd.encode("utf-8")).hexdigest()
    assert man["prompts"] == {a: E.ARMS[a]["sha256"] for a in E.ARMS}
    assert man["context_sha256"] == E.context_sha256(E.JOB_METADATA)
    assert man["user_message_sha256"] == E.sha256_bytes(E.user_message(jd).encode("utf-8"))
    assert man["plan"]["worst_case_total_usd"] == E.plan(jd)["worst_case_total_usd"] <= E.CAP_USD


def test_the_stored_answers_reproduce_the_stored_scores_exactly():
    calls, stored, _ = load()
    jd, labels = E.load_inputs()
    for r in calls:
        r["scored"] = E.score_answer(r["raw"], jd, labels) if not r.get("error") else None
    assert E.report(calls, labels) == stored["report"]


def test_the_recorded_cost_adds_up():
    calls, stored, _ = load()
    assert round(sum(r["cost_usd"] for r in calls), 6) == stored["run"]["spent_usd"] == 0.017366
    assert stored["run"]["calls_attempted"] == stored["run"]["successful_calls"] == len(calls) == 10
    assert all(r["finish_reason"] == "stop" and r["model"] == E.MODEL and r["error"] is None for r in calls)


def test_no_answer_contains_an_invented_source_text():
    calls, _, _ = load()
    jd, _ = E.load_inputs()
    for r in calls:
        obj = json.loads(r["raw"])
        for lst in obj["categories"].values():
            for i in lst:
                assert i["source_text"] in jd
