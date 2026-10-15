"""Offline tests of the no-examples control (criteria_extraction_v2-3-noex) on the TC20 JD. No network, no paid calls: the paid
path is only reached through a fake client here, and the dry-run is the only mode that runs from the environment.

They prove: the variant is the exact byte prefix of v2-3 before the EXAMPLES block (rules and output contract byte-identical),
the removed span is the pinned one, the user message and labels are the stored ones, the reservation fits the $0.03 cap, the
run makes five calls with no retries, stops at the first failure, keeps sanitized errors, never overwrites output and
never calls the network in dry-run mode, and the education-alternative retention is read apart from the official checks.
"""
import json
import pathlib

import pytest

import scripts.requirements_v2_tc20_eval as ev
import scripts.requirements_v2_tc20_noexamples as N

FIX = pathlib.Path(__file__).parent / "fixtures" / "requirements_v2_technical_coordinator_20"
LABELS_SHA256 = "6a87da80e142640af1327c4a3fd5a21adb648726a9733a699fa8d3ec67135ef9"
SCORER_SHA256 = "1c40def472760b9b79b6878cfe3f3186080786d07668d4eadbfe7ae0f5825129"
USER_MESSAGE_SHA256 = "ff6b334ad32300a05933cf96284c6c253d86ed9442ffcac37a8b543a8c240473"
EXAMPLES_MARKER = "EXAMPLES (illustrative only; never copy them into your answer)"


@pytest.fixture(scope="module")
def jd_and_labels():
    return ev.load_inputs()


@pytest.fixture(scope="module")
def stored_rows():
    return [json.loads(x) for x in (FIX / "audit" / "run_tc20_v2-3_v2-4" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


class FakeBadRequest(Exception):
    def __init__(self, message, body=None, status_code=400):
        super().__init__(message)
        self.body = body
        self.status_code = status_code


def fake_client(raw_by_call, usage=None, fail_on=None, exc=None):
    """Returns a client that answers in order; `fail_on` (1-based) raises `exc` on that call and records nothing for it."""
    seen = []

    def call(messages):
        seen.append(messages)
        n = len(seen)
        if fail_on == n:
            raise exc
        return {"raw": raw_by_call[(n - 1) % len(raw_by_call)], "finish_reason": "stop", "model": ev.MODEL,
                "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 500}}
    call.seen = seen
    return call


def test_variant_is_the_exact_prefix_of_v23_before_the_examples():
    base = N.BASE.read_bytes()
    raw = N.check_variant()
    assert base[:len(raw)] == raw
    assert base.index(EXAMPLES_MARKER.encode()) == len(raw)
    assert raw.endswith(b"an empty array if none.\n\n")
    assert EXAMPLES_MARKER not in raw.decode("utf-8")
    assert '{"scoreability"' not in raw.decode("utf-8")


def test_removed_span_is_exactly_the_examples_block():
    base = N.BASE.read_bytes()
    removed = base[len(N.check_variant()):]
    assert N.sha256_bytes(removed) == N.REMOVED_SHA256
    text = removed.decode("utf-8")
    assert text.startswith(EXAMPLES_MARKER) and "ADDITIONAL EXAMPLES" in text
    assert text.count("\n") == 84


def test_rules_and_contract_are_byte_identical_to_v23():
    raw = N.check_variant().decode("utf-8")
    base = N.BASE.read_text(encoding="utf-8")
    head = base[:base.index(EXAMPLES_MARKER)]
    assert raw == head
    # the output contract and rules 1-13 are present, in their v2-3 wording
    for line in ('"scoreability": {"status"', "13. Write text, reason and warnings", "1. ", "7. Responsibilities."):
        assert line in raw
        assert line in base


def test_the_inline_for_example_illustrations_of_the_rules_are_kept():
    raw = N.check_variant().decode("utf-8")
    assert raw.count("for example") >= 5                       # rules 3, 4, 6, 7 and 13 keep their own illustrations


def test_variant_file_is_refused_when_altered(tmp_path, monkeypatch):
    altered = tmp_path / "v.txt"
    altered.write_bytes(N.VARIANT.read_bytes() + b"x")
    monkeypatch.setattr(N, "VARIANT", altered)
    with pytest.raises(ev.PlanError, match="sha256 does not match"):
        N.check_variant()


def test_base_file_is_refused_when_altered(monkeypatch, tmp_path):
    altered = tmp_path / "base.txt"
    altered.write_bytes(N.BASE.read_bytes() + b"\n")
    monkeypatch.setattr(N, "BASE", altered)
    with pytest.raises(ev.PlanError, match="v2-3 prompt file sha256"):
        N.check_variant()


def test_user_message_and_labels_are_the_stored_ones(jd_and_labels):
    jd, labels = jd_and_labels
    assert N.sha256_bytes(ev.user_message(jd).encode("utf-8")) == USER_MESSAGE_SHA256
    assert N.sha256_bytes(ev.LABELS.read_bytes()) == LABELS_SHA256
    assert labels["jd_sha256"] == "c0c7132c6bd1373ad1be5fed31db87932dda7717c4a57023b189335e1c02d265"


def test_the_scorer_is_the_frozen_one():
    assert N.sha256_bytes(pathlib.Path(ev.__file__).read_bytes()) == SCORER_SHA256


def test_messages_send_the_variant_and_the_stored_user_message(jd_and_labels):
    jd, _ = jd_and_labels
    msgs = N.messages(jd)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == N.VARIANT.read_text(encoding="utf-8")
    assert msgs[1]["content"] == ev.user_message(jd)
    assert msgs[1]["content"].startswith("Job Context:\nJob Title: Technical Coordinator 20\n\nJob Description (verbatim, between the markers):\n<<<JD\n")


def test_plan_reserves_five_calls_under_the_three_cent_cap(jd_and_labels):
    jd, _ = jd_and_labels
    p = N.plan(jd)
    assert len(p["calls"]) == N.CALLS == 5
    assert [c["call"] for c in p["calls"]] == [1, 2, 3, 4, 5]
    assert p["cap_usd"] == 0.03 and N.CAP_USD == 0.03
    assert p["retries"] == 0 and p["model"] == ev.MODEL and p["temperature"] == 0.1 and p["max_tokens"] == 6000 and p["timeout_s"] == 90
    assert 0 < p["worst_case_total_usd"] <= 0.03
    assert p["worst_case_total_usd"] == pytest.approx(0.02484, abs=1e-6)


def test_plan_refuses_a_total_above_the_cap(jd_and_labels):
    jd, _ = jd_and_labels
    with pytest.raises(ev.PlanError, match="exceeds the cap"):
        N.plan(jd, cap=0.01)


def test_success_path_writes_evidence_and_makes_five_calls(tmp_path, jd_and_labels, stored_rows):
    jd, labels = jd_and_labels
    client = fake_client([r["raw"] for r in stored_rows])
    out = tmp_path / "run"
    res = N.run(out, jd, labels, client)
    assert res == {"spent_usd": pytest.approx(5 * (1000 * ev.PRICE_IN + 500 * ev.PRICE_OUT), abs=1e-6), "calls_attempted": 5,
                   "successful_calls": 5, "stopped": None}
    assert len(client.seen) == 5
    rows = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["call"] for r in rows] == [1, 2, 3, 4, 5] and all(r["error"] is None for r in rows)
    assert all(r["input"]["user_sha256"] == USER_MESSAGE_SHA256 for r in rows)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["prompt"]["sha256"] == N.VARIANT_SHA256 and manifest["prompt"]["base_sha256"] == N.BASE_SHA256
    assert manifest["labels_sha256"] == LABELS_SHA256 and manifest["context_sha256"] == ev.context_sha256(ev.JOB_METADATA)
    assert manifest["policy"].startswith("no retries")
    assert json.loads((out / "run.json").read_text(encoding="utf-8")) == json.loads(json.dumps(res))


def test_the_run_stops_at_the_first_failure_and_keeps_only_a_sanitized_error(tmp_path, jd_and_labels, stored_rows):
    jd, labels = jd_and_labels
    echo = "The request body was: " + jd[:200]                        # a provider message that echoes the JD
    exc = FakeBadRequest(echo, body={"error": {"message": echo}})
    client = fake_client([r["raw"] for r in stored_rows], fail_on=2, exc=exc)
    out = tmp_path / "run"
    res = N.run(out, jd, labels, client)
    assert len(client.seen) == 2                                     # no call after the failure, no retry
    assert res["calls_attempted"] == 2 and res["successful_calls"] == 1 and res["stopped"] == "error:FakeBadRequest"
    rows = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    err = rows[1]["error"]
    assert err == {"kind": "FakeBadRequest", "http_status": 400, "code": None, "type": None, "param": None,
                   "message": "[withheld: the message echoes request content]"}
    assert jd[:200] not in json.dumps(rows)


def test_a_flat_provider_error_keeps_its_identifiers_and_no_message(tmp_path, jd_and_labels, stored_rows):
    jd, labels = jd_and_labels
    exc = FakeBadRequest("Bad request", body={"code": "unsupported_parameter", "type": "invalid_request_error", "param": "max_tokens"})
    client = fake_client([r["raw"] for r in stored_rows], fail_on=1, exc=exc)
    out = tmp_path / "run"
    N.run(out, jd, labels, client)
    err = json.loads((out / "calls.jsonl").read_text(encoding="utf-8").splitlines()[0])["error"]
    assert (err["code"], err["type"], err["param"], err["message"]) == ("unsupported_parameter", "invalid_request_error", "max_tokens", None)


def test_the_cap_guard_stops_before_a_call_that_could_exceed_the_cap(tmp_path, jd_and_labels, stored_rows):
    jd, labels = jd_and_labels
    client = fake_client([r["raw"] for r in stored_rows], usage={"prompt_tokens": 2_000_000, "completion_tokens": 0})
    out = tmp_path / "run"
    res = N.run(out, jd, labels, client)
    assert res["stopped"] == "cap_guard" and res["calls_attempted"] == 1 and len(client.seen) == 1


def test_an_existing_output_directory_is_never_overwritten(tmp_path, jd_and_labels, stored_rows):
    jd, labels = jd_and_labels
    out = tmp_path / "run"
    out.mkdir()
    (out / "calls.jsonl").write_text("previous evidence\n", encoding="utf-8")
    client = fake_client([r["raw"] for r in stored_rows])
    with pytest.raises(ev.PlanError, match="not empty"):
        N.run(out, jd, labels, client)
    assert client.seen == []
    assert (out / "calls.jsonl").read_text(encoding="utf-8") == "previous evidence\n"


def test_dry_run_makes_no_network_call(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise AssertionError("the dry-run must not reach the network")
    monkeypatch.setattr(ev, "openai_call", boom)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert N.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run" and out["plan"]["worst_case_total_usd"] <= 0.03
    assert out["stored_comparison"]["user_message_identical"] is True


def test_execute_needs_an_output_directory_and_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        N.main(["--execute", "--out", str(tmp_path / "x")])
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    with pytest.raises(SystemExit):
        N.main(["--execute"])


def test_education_retention_is_read_from_the_education_item():
    kept = {"categories": {"education": [{"source_text": "Bachelor’s degree in Computer Science, Information Technology, Software Engineering", "alternatives": ["Computer Science", "Information Technology"]}]}}
    dropped = {"categories": {"education": [{"source_text": "Bachelor’s degree in Computer Science, Information Technology, Software Engineering", "alternatives": None}]}}
    absent = {"categories": {"education": []}}
    assert N.education_retained(json.dumps(kept)) is True
    assert N.education_retained(json.dumps(dropped)) is False
    assert N.education_retained(json.dumps(absent)) is False
    assert N.education_retained("not json") is None
    assert N.education_retained("[]") is None


def test_report_keeps_official_checks_and_education_apart(jd_and_labels, stored_rows):
    jd, labels = jd_and_labels
    records = [dict(r) for r in stored_rows if r["arm"] == "v2-3"]
    rep = N.report(records, jd, labels)
    assert rep["status"] == "scored" and rep["successful_calls"] == 5 and rep["errors"] == []
    assert set(rep["pass_per_check"]) == set(labels["checks"])
    assert rep["pass_per_check"]["C_LOCATION"] == 3 and sum(v for k, v in rep["pass_per_check"].items() if k != "C_LOCATION") == 0
    edu = rep["education_alternative_retention"]
    assert edu["official_score"] is False and edu["reported_separately"] is True and edu["retained"] == 5


def test_report_with_no_success_is_unavailable(jd_and_labels):
    jd, labels = jd_and_labels
    rep = N.report([{"arm": N.ARM, "call": 1, "error": {"kind": "X"}}], jd, labels)
    assert rep["status"] == "unavailable" and rep["successful_calls"] == 0


def test_stored_comparison_reads_the_five_v23_answers_offline(jd_and_labels):
    jd, _ = jd_and_labels
    cmp = N.stored_comparison(jd)
    assert cmp["stored_calls"] == 5 and cmp["stored_errors"] == []
    assert cmp["user_message_identical"] is True and cmp["current_user_message_sha256"] == USER_MESSAGE_SHA256
    assert cmp["stored_prompt_sha256"] == N.BASE_SHA256 and cmp["variant_prompt_sha256"] == N.VARIANT_SHA256
    assert cmp["stored_pass_per_check"]["C_LOCATION"] == 3
    assert cmp["stored_education_alternative_retention"]["retained"] == 5
