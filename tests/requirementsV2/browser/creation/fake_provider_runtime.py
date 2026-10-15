"""Verification-only runtime, imported by the API and the Celery worker of the creation-flow run. It makes the model transport FAKE:

  * openai.AsyncOpenAI cannot be constructed (a real provider client would raise), so no network call to a provider is possible;
  * the registry's API-key read returns a placeholder that is never sent anywhere, and the client constructor returns the fake below;
  * the fake replays the RECORDED v2-2 answers (benchmark_results/requirements_v2/v2-2_run1), chosen by the job description it is sent. Markers in
    the description drive it: [[SLOW-n]] waits n seconds (so queued/processing can be observed), [[FAIL-ONCE]] raises a terminal authentication
    error the first time that exact request is made (a later retry gets the recorded answer);
  * the legacy criteria task body is replaced by a no-op (the legacy AI extraction is not run here).

Every model call is appended to FAKE_LOG as one JSON line (model requested, settings, case). Nothing here calls a paid API or the network.
"""
import asyncio
import hashlib
import json
import os
import pathlib
import re
import sys
from types import SimpleNamespace

BACKEND = pathlib.Path(os.environ["REQ_V2_BACKEND"])
sys.path[:0] = [str(BACKEND)]

import httpx  # noqa: E402
import openai  # noqa: E402


def _refuse_real_clients(self, *a, **k):
    raise RuntimeError("real provider clients are disabled in the creation-flow verification run")


openai.AsyncOpenAI.__init__ = _refuse_real_clients

CASES = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((BACKEND / "tests" / "fixtures" / "requirements_v2_benchmark" / "cases").glob("B*.json"))]
RECORDED = {}
for line in (BACKEND / "benchmark_results" / "requirements_v2" / "v2-2_run1" / "calls.jsonl").read_text(encoding="utf-8").splitlines():
    rec = json.loads(line)
    if rec["run"] == "run1":
        RECORDED[rec["case"]] = rec
LOG = pathlib.Path(os.environ["FAKE_LOG"])
STATE = pathlib.Path(os.environ["FAKE_STATE"])
STATE.mkdir(parents=True, exist_ok=True)


def _log(entry):
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


class _Completions:
    async def create(self, **kw):
        user = kw["messages"][-1]["content"]
        case = next((c for c in CASES if c["jd"] in user), CASES[0])
        entry = {"case": case["id"], "model": kw["model"], "temperature": kw["temperature"], "max_tokens": kw["max_tokens"],
                 "response_format": kw["response_format"], "marker": None}
        slow = re.search(r"\[\[SLOW-(\d+)\]\]", user)
        if slow:
            entry["marker"] = f"SLOW-{slow.group(1)}"
            await asyncio.sleep(int(slow.group(1)))
        if "[[FAIL-ONCE]]" in user:
            key = hashlib.sha256(user.encode("utf-8")).hexdigest()[:16]
            if not (STATE / key).exists():
                (STATE / key).touch()
                entry["outcome"] = "auth error (fake, terminal)"
                _log(entry)
                raise openai.AuthenticationError("fake: invalid key", response=httpx.Response(401, request=httpx.Request("POST", "https://fake.invalid")), body=None)
        rec = RECORDED[case["id"]]
        entry["outcome"] = "recorded answer"
        _log(entry)
        return SimpleNamespace(model=kw["model"], choices=[SimpleNamespace(message=SimpleNamespace(content=rec["raw"]), finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=6400, completion_tokens=900))


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_Completions())

    def with_options(self, **_):
        return self


import services.ai_model_registry_service as registry  # noqa: E402


async def _placeholder_key(db, key):
    return "test-key-never-sent"


registry._get_api_key = _placeholder_key
registry._build_client = lambda provider, api_key, base_url, *a, **k: None if base_url == "https://fail.invalid" else FakeClient()

import workers.criteria_worker as _legacy  # noqa: E402

_legacy.extract_criteria_task.run = lambda *a, **k: {"stubbed": "legacy AI extraction is not run in the creation-flow verification"}
