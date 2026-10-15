"""No-database unit tests for the requirements-v2 extraction service (services/requirements_v2_extraction.py).

What these prove: the approved prompt text and settings are the reviewed ones; provider errors are classified without keeping the provider's
message; the recorded v2-2 responses (replayed through the approved production pipeline) are accepted or refused as the pipeline decides.
What they do NOT prove: anything about model accuracy. The recorded responses were produced by v2-2, not by v2-3, and a fake client is a fake.
The real-PostgreSQL behaviour is in test_requirements_v2_extraction_postgres.py; the worker and the browser path are tested there and in the
full-application run.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import re
import sys
from types import SimpleNamespace

import httpx
import openai
import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND)]

from services import requirements_v2_extraction as ext  # noqa: E402
from services.requirements_v2.extraction.prompt import EXTRACTION_CONFIG  # noqa: E402

PROMPT_FILE = BACKEND / "prompt_candidates" / "criteria_extraction_v2-3" / "criteria_extraction_v2-3.txt"
MIGRATION_108 = BACKEND / "db" / "migrations" / "108_requirements_v2_extraction.sql"
RUN = BACKEND / "benchmark_results" / "requirements_v2" / "v2-2_run1" / "calls.jsonl"
CASES = {c["id"]: c for c in (json.loads(p.read_text(encoding="utf-8"))
                              for p in sorted((BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases").glob("B*.json")))}
RECORDS = [json.loads(x) for x in RUN.read_text(encoding="utf-8").splitlines()]


# the reviewed v2-3 text (the migration-108 seed). A TEST reference only: production never compares a stored prompt with a code list.
SEED_V23_SHA256 = "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"


def _prompt(text: str = "SYSTEM", *, version: int = 3, temperature: float = 0.1, max_tokens: int = 6000) -> ext.PromptRef:
    return ext.PromptRef(prompt_id="p", version=version, label=f"criteria_extraction_v2-{version}",
                         sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), system_prompt=text, temperature=temperature, max_tokens=max_tokens)


def _call(raw: str, finish: str = "stop") -> ext.CallResult:
    return ext.CallResult(raw=raw, finish_reason=finish, requested_model="gpt-4o-mini-2024-07-18", returned_model="gpt-4o-mini-2024-07-18",
                          prompt_tokens=10, completion_tokens=20, latency_ms=5)


# ── the seed text and the configuration surface ────────────────────────────────────────────────────────────────────────────────────
def test_the_reviewed_prompt_file_is_the_v23_benchmark_text():
    assert hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest() == SEED_V23_SHA256


def test_migration_108_seeds_exactly_the_reviewed_prompt_text():
    sql = MIGRATION_108.read_text(encoding="utf-8")
    m = re.search(r"\$prompt\$(.*?)\$prompt\$", sql, re.S)
    assert m, "migration 108 must carry the prompt text in $prompt$ quotes"
    assert hashlib.sha256(m.group(1).encode("utf-8")).hexdigest() == SEED_V23_SHA256
    assert m.group(1) == PROMPT_FILE.read_text(encoding="utf-8")


def test_migration_108_registers_the_prompt_inactive_and_the_switch_off():
    sql = MIGRATION_108.read_text(encoding="utf-8")
    assert "is_active" in sql and re.search(r"'criteria_extraction_v2'.*?\bFALSE\b", sql, re.S)
    assert "'requirements_v2.enabled'" in sql and re.search(r"'requirements_v2\.enabled',\s*'false'", sql)


def test_only_the_output_contract_and_the_timeout_are_code_constants():
    # temperature and max_tokens are configured on the prompt row; the JSON response format is the parser's contract
    assert ext.RESPONSE_FORMAT == dict(EXTRACTION_CONFIG["response_format"])
    assert ext.PER_CALL_TIMEOUT_S == 90
    assert not hasattr(ext, "MODEL_SETTINGS") and not hasattr(ext, "APPROVED_PROMPTS")


def test_the_prompt_reports_the_settings_it_will_send():
    p = _prompt("T", temperature=0.3, max_tokens=5000)
    assert p.settings == {"temperature": 0.3, "max_tokens": 5000, "response_format": {"type": "json_object"}}


def test_a_configured_model_fallback_is_never_used_by_the_stage(monkeypatch):
    from services import ai_model_registry_service as registry
    async def primary_failed_fallback_answered(db, stage):
        return registry.ResolvedModel(provider="openai", model_name="fallback-model", client=object(), model_id="x", is_fallback=True,
                                      fallback_reason="primary has no key")
    monkeypatch.setattr(registry, "resolve_stage_client", primary_failed_fallback_answered)
    with pytest.raises(ext.ExtractionUnavailable) as info:
        asyncio.run(ext.resolve_model(None))
    assert info.value.code == "primary_model_unavailable"


def test_no_configured_stage_is_refused_without_a_call(monkeypatch):
    from services import ai_model_registry_service as registry
    async def nothing(db, stage):
        return None
    monkeypatch.setattr(registry, "resolve_stage_client", nothing)
    with pytest.raises(ext.ExtractionUnavailable) as info:
        asyncio.run(ext.resolve_model(None))
    assert info.value.code == "model_not_configured"


def test_terminal_and_retryable_error_kinds_do_not_overlap():
    assert not set(ext.TERMINAL_CALL_KINDS) & set(ext.RETRYABLE_CALL_KINDS)
    assert ext.TransportError("rate_limit").retryable and not ext.TransportError("auth").retryable


# ── provider errors: classified, the provider's message never kept ───────────────────────────────────────────────────────────────
def _resp(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"))


SECRET = "sk-SECRET-do-not-store"
PROVIDER_ERRORS = [
    (openai.AuthenticationError(SECRET, response=_resp(401), body=None), ("auth", 401)),
    (openai.PermissionDeniedError(SECRET, response=_resp(403), body=None), ("auth", 403)),
    (openai.NotFoundError(SECRET, response=_resp(404), body=None), ("model_unavailable", 404)),
    (openai.BadRequestError(SECRET, response=_resp(400), body=None), ("bad_request", 400)),
    (openai.RateLimitError(SECRET, response=_resp(429), body=None), ("rate_limit", 429)),
    (openai.InternalServerError(SECRET, response=_resp(503), body=None), ("http_error_5xx", 503)),
    (openai.APIStatusError(SECRET, response=_resp(418), body=None), ("http_error", 418)),
    (openai.APITimeoutError(request=httpx.Request("POST", "https://example.invalid")), ("timeout", None)),
    (openai.APIConnectionError(message=SECRET, request=httpx.Request("POST", "https://example.invalid")), ("transport", None)),
    (RuntimeError(SECRET), ("transport", None)),
]


@pytest.mark.parametrize("exc,expected", PROVIDER_ERRORS, ids=[type(e).__name__ for e, _ in PROVIDER_ERRORS])
def test_each_provider_error_maps_to_its_kind(exc, expected):
    assert ext._kind_of(exc) == expected


class _FakeCompletions:
    def __init__(self, outcome):
        self.outcome, self.kwargs = outcome, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeClient:
    def __init__(self, outcome):
        self.completions = _FakeCompletions(outcome)
        self.chat = SimpleNamespace(completions=self.completions)
        self.options = None

    def with_options(self, **kw):
        self.options = kw
        return self


def _response(content: str, finish: str = "stop", model: str = "gpt-4o-mini-2024-07-18"):
    return SimpleNamespace(model=model, choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
                           usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45))


def test_call_model_sends_the_configured_settings_and_records_what_was_sent():
    client = _FakeClient(_response('{"ok": true}'))
    prompt = _prompt("S", temperature=0.3, max_tokens=5000)
    res = asyncio.run(ext.call_model(client, prompt, [{"role": "user", "content": "x"}], "gpt-4o-mini-2024-07-18", timeout_s=90))
    sent = client.completions.kwargs
    assert sent["model"] == "gpt-4o-mini-2024-07-18" and sent["temperature"] == 0.3 and sent["max_tokens"] == 5000
    assert sent["response_format"] == {"type": "json_object"}
    assert client.options == {"timeout": 90, "max_retries": 0}           # the worker, not the SDK, owns retries
    assert res.raw == '{"ok": true}' and res.finish_reason == "stop" and res.returned_model == "gpt-4o-mini-2024-07-18"
    assert (res.prompt_tokens, res.completion_tokens) == (123, 45)
    assert res.settings == {"temperature": 0.3, "max_tokens": 5000, "response_format": {"type": "json_object"}, "timeout_s": 90}


def test_call_model_error_keeps_no_provider_text():
    client = _FakeClient(openai.RateLimitError(f"quota for {SECRET}", response=_resp(429), body=None))
    with pytest.raises(ext.TransportError) as info:
        asyncio.run(ext.call_model(client, _prompt(), [], "gpt-4o-mini-2024-07-18", timeout_s=90))
    err = info.value
    assert (err.kind, err.status, err.retryable) == ("rate_limit", 429, True)
    assert SECRET not in str(err) and SECRET not in repr(err) and SECRET not in err.kind


def test_build_messages_puts_the_approved_system_prompt_first_and_the_job_text_in_the_user_turn():
    msgs = ext.build_messages(_prompt("THE SYSTEM TEXT"), "Python developer, 3 years.", {"title": "Dev"})
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == "THE SYSTEM TEXT"
    assert "Python developer, 3 years." in msgs[1]["content"]


# ── the recorded v2-2 responses through the approved pipeline ────────────────────────────────────────────────────────────────────
def test_the_recorded_run_covers_twelve_cases_twice():
    assert len(RECORDS) == 24 and {r["case"] for r in RECORDS} == set(CASES)
    assert all(r["finish_reason"] == "stop" and r["error"] in (None, "None") for r in RECORDS)


@pytest.mark.parametrize("rec", RECORDS, ids=[f"{r['case']}-{r['run']}" for r in RECORDS])
def test_each_recorded_response_is_accepted_by_the_production_pipeline(rec):
    state = ext.analyse(CASES[rec["case"]]["jd"], _call(rec["raw"], rec["finish_reason"]), _prompt(), require_classification_acknowledgment=True)
    assert state["ok"] is True, [e["code"] for e in state.get("errors", [])]
    assert not state.get("errors")


def _content(obj):
    """The state with the generated requirement ids and the digests computed over them removed: what must be the same on every run."""
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    text = re.sub(r"req_[0-9a-f]{12}", "REQ", text)
    return re.sub(r'"[a-z_]*digest[a-z_]*": "[0-9a-f]+"', '"DIGEST"', text)


def test_the_pipeline_gives_the_same_content_on_every_run_apart_from_generated_ids():
    # requirement ids are generated per run (and the digests are computed over them); everything a recruiter sees must be identical
    rec = RECORDS[0]
    a = ext.analyse(CASES[rec["case"]]["jd"], _call(rec["raw"]), _prompt(), require_classification_acknowledgment=True)
    b = ext.analyse(CASES[rec["case"]]["jd"], _call(rec["raw"]), _prompt(), require_classification_acknowledgment=True)
    assert _content(a) == _content(b)
    assert a["ok"] is True


def test_the_pipeline_records_the_prompt_provenance_it_was_given():
    rec = RECORDS[0]
    state = ext.analyse(CASES[rec["case"]]["jd"], _call(rec["raw"]), _prompt(PROMPT_FILE.read_text(encoding="utf-8")),
                        require_classification_acknowledgment=True)
    assert state["component_versions"]["extraction_prompt"] == {"version": "criteria_extraction_v2-3", "sha256": SEED_V23_SHA256}


def test_a_truncated_response_is_refused_by_the_pipeline_and_never_stored_as_a_document():
    rec = RECORDS[0]
    state = ext.analyse(CASES[rec["case"]]["jd"], _call(rec["raw"], finish="length"), _prompt(), require_classification_acknowledgment=True)
    assert state["ok"] is False and state["errors"]


@pytest.mark.parametrize("raw", ["", "not json at all", "[1, 2, 3]", '{"categories": 5}'])
def test_malformed_output_is_refused_by_the_pipeline(raw):
    rec = RECORDS[0]
    state = ext.analyse(CASES[rec["case"]]["jd"], _call(raw), _prompt(), require_classification_acknowledgment=True)
    assert state["ok"] is False and state["errors"]
