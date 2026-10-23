"""HTTP and database helpers for the E2E journeys. Every call goes to the real API; reads of the processing state use the database
directly (read-only assertions, the same tables the workers write)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "backend" / "tests" / "fixtures" / "requirements_v2_cv_smoke.pdf"


class ApiError(AssertionError):
    pass


def _request(method, url, token=None, body=None, raw=None, headers=None):
    hdrs = {"Content-Type": "application/json"} | (headers or {})
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


class Api:
    def __init__(self, stack):
        self.stack = stack
        self.base = stack.backend_base

    def login(self, role: str):
        user = self.stack.users[role]
        status, body = _request("POST", f"{self.base}/auth/login", body={"email": user["email"], "password": user["password"]})
        if status != 200:
            raise ApiError(f"login as {role} failed: {status} {body}")
        return body["token"], body["user"]

    def create_job(self, token, title, description):
        status, body = _request("POST", f"{self.base}/jobs", token, {"title": title, "description": description})
        if status != 201:
            raise ApiError(f"create job failed: {status} {body}")
        return body["job_id"]

    def update_description(self, token, job, description):
        status, body = _request("PUT", f"{self.base}/jobs/{job}", token, {"description": description})
        if status != 200:
            raise ApiError(f"update description failed: {status} {body}")

    def retry_status(self, token, job):
        """POST the retry route and return the HTTP status (202 queued, 400 refused for an unchanged description)."""
        status, _ = _request("POST", f"{self.base}/jobs/{job}/criteria/retry", token, {})
        return status

    def requirements(self, token, job, timeout=60):
        """The stored document, once extraction has produced one."""
        status = body = None
        for _ in range(timeout):
            status, body = _request("GET", f"{self.base}/jobs/{job}/requirements", token)
            if status == 200:
                return body
            time.sleep(1)
        raise ApiError(f"requirements never became available for {job}: {status} {body}")

    def edit(self, token, job, revision, operations):
        return _request("POST", f"{self.base}/jobs/{job}/requirements/edits", token,
                        {"client_revision": revision, "operations": operations})

    def item_id(self, token, job, category, index):
        return self.requirements(token, job)["document"]["categories"][category]["items"][index]["id"]

    def upload_cv(self, token, job, name):
        boundary = "----e2e"
        parts = [(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
                 for k, v in (("job_id", job), ("candidate_name", name), ("candidate_email", f"{name.lower()}@e2e-candidate.com"))]
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="cv.pdf"\r\n'
                      f'Content-Type: application/pdf\r\n\r\n').encode() + FIXTURE.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        status, body = _request("POST", f"{self.base}/applications/upload", token, raw=b"".join(parts),
                                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        if status != 202:
            raise ApiError(f"upload failed: {status} {body}")
        return body

    def application_id(self, job, timeout=60):
        """The id of the job's first application, once the upload has created it."""
        for _ in range(timeout):
            with self._worker_connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT application_id::text FROM applications WHERE job_id = %s ORDER BY created_at LIMIT 1", (job,))
                row = cur.fetchone()
            if row:
                return row[0]
            time.sleep(1)
        raise ApiError(f"no application for {job}")

    def gates(self, token, application):
        return _request("GET", f"{self.base}/applications/{application}/gates", token)

    def gate_status(self, token, application, wanted, timeout=60):
        """The Gate Summary once its eligibility status is `wanted` (the scoring worker evaluates the gates after it scores)."""
        body = None
        for _ in range(timeout):
            status, body = self.gates(token, application)
            if status == 200 and body["eligibility_status"] == wanted:
                return body
            time.sleep(1)
        raise ApiError(f"gates never reached {wanted} for {application}: {body}")

    def _connect(self, dsn=None):
        conn = psycopg2.connect(dsn or self.stack.psycopg_dsn, options="-c search_path=cv_analyzer")
        conn.autocommit = True
        return conn

    def _worker_connect(self):
        """The scoring worker's output is read through the worker login, which owns that data path."""
        return self._connect(self.stack.worker_psycopg_dsn)

    def score_row(self, job, timeout=60):
        """requirements_revision_used and final_score of the job's first score, once the scoring worker has written it. A Requirements V2 job is
        scored per item (application_scoring_runs; final_score is None while Review Required or Not scored); an older job in application_scores."""
        for _ in range(timeout):
            with self._worker_connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT r.requirements_revision, r.final_score FROM application_scoring_runs r"
                            " JOIN applications a ON a.application_id = r.application_id WHERE a.job_id = %s AND r.is_current", (job,))
                row = cur.fetchone()
                if not row:
                    cur.execute("SELECT s.requirements_revision_used, s.final_score FROM application_scores s"
                                " JOIN applications a ON a.application_id = s.application_id WHERE a.job_id = %s", (job,))
                    row = cur.fetchone()
            if row:
                return {"requirements_revision_used": row[0], "final_score": row[1]}
            time.sleep(1)
        return None

    def resolve_item(self, token, application, item_id, result, reason):
        return _request("POST", f"{self.base}/applications/{application}/item-scoring/items/{item_id}/resolve", token,
                        {"result": result, "reason": reason})

    def item_scoring(self, token, application):
        return _request("GET", f"{self.base}/applications/{application}/item-scoring", token)

    def retry_state(self, job, retries, timeout=60):
        """(status, retry_count) once the job has reached `retries` retries and the extraction has settled to failed."""
        row = None
        for _ in range(timeout):
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT criteria_extraction_status, criteria_extraction_retry_count FROM job_criteria WHERE job_id = %s",
                            (job,))
                row = cur.fetchone()
            if row and row[0] == "failed" and row[1] >= retries:
                return row
            time.sleep(1)
        raise ApiError(f"job {job} did not reach failed with {retries} retries: {row}")

    def criteria_state(self, job, timeout=60):
        """(criteria_extraction_status, analysis_json IS NULL) once extraction has settled to failed."""
        row = None
        for _ in range(timeout):
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT criteria_extraction_status, analysis_json IS NULL FROM job_criteria WHERE job_id = %s", (job,))
                row = cur.fetchone()
            if row and row[0] == "failed":
                break
            time.sleep(1)
        return row
