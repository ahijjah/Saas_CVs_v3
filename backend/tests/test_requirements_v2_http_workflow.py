"""The requirements-v2 workflow through the REAL HTTP API on real PostgreSQL (see requirements_v2_http_driver.py for what runs where).

The driver runs in its own process so that the conftest stubs of this pytest run (database, application_intake_service, ...) do not replace
the real database session and row-level-security context. The model is a fake transport replaying the recorded v2-2 answers, or raising provider
exceptions; the prompt is the migration-108 text, checked against the approved hash by the real loader. Proves the wiring, the authority
rules and the storage contract; it does NOT prove model accuracy. The recorded answers came from v2-2, not v2-3.
"""
import json
import os
import pathlib
import subprocess
import sys
import uuid

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND / "tests"), str(BACKEND), str(BACKEND / "services")]
import realdb_helper as rh  # noqa: E402

REASON = rh.available()
pytestmark = pytest.mark.skipif(REASON is not None, reason=f"no real PostgreSQL 16 here: {REASON}")

DRIVER = BACKEND / "tests" / "requirements_v2_http_driver.py"
APPROVED_SHA = "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"
WEIGHT_COLUMNS = ("weight_skills", "weight_experience", "weight_education", "weight_certifications", "weight_soft_skills",
                  "weight_domain_knowledge", "weight_other")
T1, T2 = str(uuid.UUID(int=0x101)), str(uuid.UUID(int=0x102))
USERS = {"admin": (str(uuid.UUID(int=0x201)), T1, "admin"), "hr": (str(uuid.UUID(int=0x202)), T1, "hr_manager"),
         "viewer": (str(uuid.UUID(int=0x203)), T1, "viewer"), "other": (str(uuid.UUID(int=0x204)), T2, "admin")}


def _seed(db):
    db.q("INSERT INTO tenants (tenant_id, name, email_domain, status, subscription_status, tenant_type) VALUES (%s,'Tenant One','t1.example','active','active','organization'),(%s,'Tenant Two','t2.example','active','active','organization')", (T1, T2))
    for key, (uid, tenant, role) in USERS.items():
        db.q("INSERT INTO users (user_id, tenant_id, email, password_hash, full_name, role, status) VALUES (%s,%s,%s,'x',%s,%s,'active')",
             (uid, tenant, f"{key}-{uid[-4:]}@x.example", key, role))


@pytest.fixture(scope="module")
def obs(tmp_path_factory):
    cluster = rh.Cluster()
    db = cluster.build()
    try:
        _seed(db)
        out = tmp_path_factory.mktemp("http") / "obs.json"
        env = {**os.environ, "DATABASE_URL": db.async_url, "JWT_SECRET": "test-only-secret", "PYTHONIOENCODING": "utf-8",
               "HTTP_SEED": json.dumps({"users": USERS})}
        proc = subprocess.run([sys.executable, "-I", str(DRIVER), str(out)], env=env, capture_output=True, text=True, timeout=900, cwd=str(BACKEND))
        assert proc.returncode == 0, proc.stderr[-4000:]
        data = json.loads(out.read_text(encoding="utf-8"))
        data["_raw"] = out.read_text(encoding="utf-8")
        return data
    finally:
        db.drop()
        cluster.stop()


def S(obs, name):
    step = obs["steps"][name]
    assert "exception" not in step, step["exception"]
    return step


def weight_total(categories):
    return sum(c["weight"] for c in categories.values())


# ── creation and the feature switch ──────────────────────────────────────────────────────────────────────────────────────────────
def test_with_the_switch_off_a_v2_job_is_refused_and_nothing_is_queued(obs):
    st = S(obs, "off_refuses_creation")
    assert st["status"] == 409 and st["detail"]["code"] == "requirements_v2_disabled"
    assert st["queued"] == 0


def test_english_required_and_preferred_is_stored_served_and_detailed(obs):
    st = S(obs, "english_required_preferred")
    assert st["create_status"] == 201 and st["requirements_format_in_response"] == "v2"
    assert st["queued"] == 1
    assert st["worker"]["failed"] is False and st["worker"]["result"] == {"outcome": "completed", "code": None}
    assert st["worker"]["model_calls"] == 1 and st["remaining_script"] == 0
    assert st["get_status"] == 200 and st["revision"] == 1 and st["schema_version"] == 2
    assert st["extraction"] == {"status": "completed", "error": None, "retry_available": False}
    assert st["readiness_state"] == "ready" and st["pipeline_status"] == "ok" and st["original_present"] is True
    assert st["can_edit"] is True
    assert weight_total(st["categories"]) == 100                       # the weights add up to 100 for a job with Required items
    importances = {i for c in st["categories"].values() for i in c["importance"]}
    assert importances == {"required", "preferred"}                    # both kinds are present in the English job
    assert st["details_status"] == 200 and st["details_requirements_format"] == "v2"
    assert st["prompt_sha_recorded"] == APPROVED_SHA                   # the stored prompt provenance is the approved one


def test_the_editor_saves_reloads_and_refuses_a_stale_save(obs):
    st = S(obs, "edit_save_reload_and_authority")
    assert st["save_status"] == 200 and st["save_revision"] == 2
    assert st["stale_status"] == 409 and st["stale_code"] == "requirements_revision_conflict"
    assert st["reloaded_text"].endswith("(edited)") and st["reloaded_revision"] == 2


def test_an_edit_never_changes_the_original_snapshot(obs):
    st = S(obs, "edit_save_reload_and_authority")
    assert st["original_after"] == st["original_before"]
    assert not st["original_before"].endswith("(edited)")
    assert st["original_edited_flag"] is True                          # the edited category is flagged, the original is not


def test_authority_viewer_reads_but_cannot_write_other_tenant_sees_nothing_hr_can_write(obs):
    st = S(obs, "edit_save_reload_and_authority")
    assert st["viewer_get_status"] == 200 and st["viewer_can_edit"] is False and st["viewer_put_status"] == 403
    assert st["other_tenant_status"] == 404
    assert st["hr_get_status"] == 200 and st["hr_can_edit"] is True
    assert st["audit_saves"] >= 2                                      # the extraction and the save are both audited


# ── other JD shapes ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_preferred_only_job_is_stored_with_zero_weights_and_needs_confirmation(obs):
    st = S(obs, "preferred_only")
    assert st["create_status"] == 201 and st["worker_failed"] is False and st["extraction"]["status"] == "completed"
    assert all(c["importance"] in ([], ["preferred"]) for c in st["categories"].values())
    assert weight_total(st["categories"]) == 0
    assert all(st["weights"][col] == 0 for col in WEIGHT_COLUMNS)      # the weight columns follow the document
    assert st["weights"]["requirements_schema_version"] == 2
    assert st["readiness_state"] == "needs_confirmation" and st["preferred_only_confirmation"]["confirmed"] is False


def test_arabic_job_is_stored_with_arabic_items_and_weights_totalling_100(obs):
    st = S(obs, "arabic")
    assert st["create_status"] == 201 and st["worker_failed"] is False and st["extraction"]["status"] == "completed"
    assert st["total_items"] > 0 and st["arabic_item_texts"] == st["total_items"]
    assert weight_total(st["categories"]) == 100
    assert st["readiness_state"] == "ready"


def test_a_truncated_answer_is_a_failed_extraction_with_no_document_and_no_retry(obs):
    st = S(obs, "truncated_output_fails_without_a_document")
    assert st["model_calls"] == 1                                       # the same prompt would be cut off again: no automatic retry
    assert st["row"]["st"] == "failed" and "cut off" in st["row"]["err"]
    assert st["row"]["no_analysis"] is True and st["row"]["rev"] == 0
    assert st["get_status"] == 200 and st["extraction"]["status"] == "failed" and st["extraction"]["retry_available"] is True
    assert st["readiness_basis"] == "extraction" and st["can_edit"] is False


# ── failure, retry, stale results ────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_failed_attempt_is_terminal_for_auth_and_keeps_no_secret(obs):
    st = S(obs, "failure_retry_and_stale")
    assert st["first_worker"] == {"outcome": "failed", "code": "auth"} and st["first_model_calls"] == 1
    assert st["failed_extraction"]["status"] == "failed"
    assert st["failed_extraction"]["error"] == "The model call failed (auth, HTTP 401)."
    assert "SECRET-DO-NOT-STORE" not in obs["_raw"]                    # the provider's text is never stored or returned


def test_retry_is_editor_only_and_a_new_attempt_completes_while_the_old_one_is_refused(obs):
    st = S(obs, "failure_retry_and_stale")
    assert st["viewer_retry_available"] is False and st["viewer_retry_status"] == 403
    assert st["admin_retry_status"] == 200 and st["admin_retry_body_status"] == "pending"
    assert st["second_retry_status"] == 200                            # a queued (pending) job may be re-requested: the earlier token is superseded
    assert st["second_worker_failed"] is False and st["second_model_calls"] == 1
    assert st["stale_outcome"] == {"outcome": "stale", "code": "not_claimable"}   # the superseded attempt made no model call
    assert st["done_extraction"]["status"] == "completed" and st["done_readiness_basis"] == "pipeline"
    assert [r["request_status"] for r in st["usage_rows"]] == ["failed", "success"]


def test_a_transient_timeout_is_retried_once_then_succeeds_with_exact_usage_rows(obs):
    st = S(obs, "transient_then_success")
    assert st["worker_failed"] is False and st["model_calls"] == 2
    assert st["extraction"]["status"] == "completed"
    assert [(r["request_status"], r["error_type"], r["retry_count"]) for r in st["usage_rows"]] == [("failed", "timeout", 0), ("success", None, 1)]
    assert st["readiness_state"] == "needs_items"                      # an open-empty answer is stored for the recruiter, not a failure


# ── legacy ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_legacy_job_keeps_the_legacy_workflow_and_is_not_read_as_v2(obs):
    st = S(obs, "legacy_job_untouched")
    assert st["create_status"] == 201 and st["requirements_format_in_response"] == "legacy"
    assert st["queued_legacy"] == 1 and st["queued_v2"] == 0
    assert st["requirements_get_status"] == 409 and st["requirements_get_code"] == "not_requirements_v2"
    assert st["details_requirements_format"] == "legacy"


def test_turning_the_switch_off_refuses_a_retry_of_an_existing_failed_job(obs):
    st = S(obs, "switch_off_refuses_retry")
    assert st.get("skipped") is not True
    assert st["status"] == 409 and st["code"] == "requirements_v2_disabled"


def test_the_live_acknowledgment_setting_governs_readiness_on_every_view(obs):
    st = S(obs, "live_acknowledgment_policy_governs_readiness")
    assert st["worker_failed"] is False
    v = st["views"]
    print("POLICY VIEWS", json.dumps(v, indent=1))
    assert v["true0"]["policy_flag"] is True and v["false1"]["policy_flag"] is False and v["true2"]["policy_flag"] is True
    # the same stored document is judged differently only because of the live setting, and returning to 'true' restores the first answer
    assert v["true0"] == {**v["true2"], "classification_warnings": v["true0"]["classification_warnings"]}
    assert st["stored"]["st"] == "completed"


def test_required_and_preferred_are_per_item_inside_a_category(obs):
    """Importance belongs to each item; a category is only a grouping with its own weight. A category can hold Required and Preferred items together,
    and only Required items carry weight in the job's scoring."""
    st = S(obs, "english_required_preferred")
    mixed = [name for name, c in st["categories"].items() if set(c["importance"]) == {"required", "preferred"}]
    assert mixed, st["categories"]                                      # at least one category mixes the two kinds
    assert st["categories"]["skills"]["count"] == 3 and set(st["categories"]["skills"]["importance"]) == {"required", "preferred"}
