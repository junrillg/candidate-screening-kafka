#!/usr/bin/env python3
"""Exercise the real local API, Kafka handoff and model; synthetic records only."""
import http.cookiejar
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

API = os.environ.get("LAB_API_URL", "http://127.0.0.1:8095")
ORIGIN = os.environ.get("LAB_PUBLIC_ORIGIN", "http://127.0.0.1:8094")


class Browser:
    def __init__(self, api=API):
        self.api = api
        self.jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.owner = None
        self.csrf = None

    def request(self, path, method="GET", body=None, expected=200, headers=None):
        fields = {"Origin": ORIGIN}
        if self.csrf:
            fields["X-CSRF-TOKEN"] = self.csrf
        if body is not None:
            fields["Content-Type"] = "application/json"
        fields.update(headers or {})
        request = urllib.request.Request(self.api + path, method=method, headers=fields,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with self.http.open(request, timeout=15) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        assert status == expected, f"Unexpected HTTP status: {method} {path}: {status}, expected {expected}"
        result = None if not raw else json.loads(raw)
        if status >= 400:
            assert isinstance(result.get("error", {}).get("code"), str), "Missing safe error contract"
        return result

    def session(self):
        value = self.request("/api/session")
        uuid.UUID(value["ownerId"])
        assert value["csrfToken"]
        self.owner, self.csrf = value["ownerId"], value["csrfToken"]
        return self.owner


def wait_status(browser, candidate, terminal, timeout=240):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = browser.request("/api/candidates/" + candidate)
        if value["status"] in terminal:
            return value
        time.sleep(2)
    raise AssertionError("Real background flow did not reach expected state before deadline")


def run():
    first, other = Browser(), Browser()
    owner = first.session()
    assert first.session() == owner, "Session refresh changed valid owner"
    assert other.session() != owner
    assert Browser().request("/api/candidates", expected=401)["error"]["code"] == "session_expired"
    submission = {"displayName": "Synthetic candidate", "profile": "I implemented Kafka consumers in a .NET service. I have never used React.", "requirements": ["Kafka"]}
    key = str(uuid.uuid4())
    no_csrf = first.csrf
    first.csrf = None
    first.request("/api/candidates", "POST", submission, 403, {"Idempotency-Key": key})
    first.csrf = no_csrf
    first.request("/api/candidates", "POST", submission, 403,
                  {"Idempotency-Key": key, "Origin": "http://different.invalid"})
    first.request("/api/candidates", "POST", dict(submission, extra="rejected"), 400,
                  {"Idempotency-Key": key})
    accepted = first.request("/api/candidates", "POST", submission, 202, {"Idempotency-Key": key})
    candidate = accepted["id"]
    print("Durable acceptance received", flush=True)
    replay = first.request("/api/candidates", "POST", submission, 202, {"Idempotency-Key": key})
    assert accepted == replay, "Replay changed original acceptance"
    first.request("/api/candidates", "POST", dict(submission, profile="Different synthetic text"), 409,
                  {"Idempotency-Key": key})
    other.request("/api/candidates/" + candidate, expected=404)
    other.request("/api/candidates/" + candidate + "/result", expected=404)
    other.request("/api/candidates/" + candidate, "DELETE", expected=404)
    assert not other.request("/api/candidates")["items"]

    request = urllib.request.Request(API + "/api/events")
    stream = first.http.open(request, timeout=25)
    retry = stream.readline().decode().strip()
    assert retry.startswith("retry: ") and 1500 <= int(retry[7:]) <= 5000, "SSE reconnect delay was not bounded"
    assert stream.readline().decode().strip() == ""
    assert stream.readline().decode().strip() == "event: reconcile", "SSE did not remain open for initial reconciliation"
    assert stream.readline().decode().strip() == "data: {}"
    changes = []
    terminal_changes = []

    def listen():
        try:
            while not terminal_changes:
                line = stream.readline().decode()
                if not line:
                    break
                if line.startswith("data: "):
                    value = json.loads(line[6:])
                    if value.get("candidateId") == candidate:
                        changes.append(value)
                        if first.request("/api/candidates/" + candidate)["status"] == "Completed":
                            terminal_changes.append(value)
        except (TimeoutError, OSError, ValueError):
            pass

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()
    print("Ownership, CSRF, replay/conflict and initial SSE checks passed", flush=True)
    finished = wait_status(first, candidate, {"Completed", "Failed"})
    assert finished["status"] == "Completed", "Real inference failed; inspect safe correlated service diagnostics"
    print("Real model reached Completed", flush=True)
    result = first.request("/api/candidates/" + candidate + "/result")
    assert result["candidateId"] == candidate and result["reviewRequired"] is True
    assert result["provider"] == "Ollama" and len(result["items"]) == 1
    assert result["items"][0]["requirement"] == "Kafka"
    evidence = result["items"][0]["evidence"]
    assert evidence is None or evidence in submission["profile"], "Result fabricated evidence"
    state = result["items"][0]["state"]
    assert state in {"EvidenceFound", "NeedsReview", "NotEvidenced"}, "Invalid deterministic evidence state"
    assert (evidence is None) == (state == "NotEvidenced"), "Evidence/state relationship was inconsistent"
    notices = first.request("/api/notifications")["items"]
    matching = [notice for notice in notices if notice["candidateId"] == candidate]
    assert len(matching) == 1
    notice = matching[0]
    other.request("/api/notifications/" + notice["id"] + "/read", "POST", expected=404)
    first.request("/api/notifications/" + notice["id"] + "/read", "POST", expected=204)
    assert next(item for item in first.request("/api/notifications")["items"] if item["id"] == notice["id"])["read"] is True
    listener.join(timeout=10)
    assert terminal_changes, "No owner-scoped Kafka change reached the SSE stream"
    stream.close()
    first.request("/api/candidates/" + candidate, "DELETE", expected=202)
    first.request("/api/candidates/" + candidate, expected=404)
    first.request("/api/candidates/" + candidate + "/result", expected=404)
    first.request("/api/candidates", "POST", submission, 410, {"Idempotency-Key": key})
    assert all(item["candidateId"] != candidate for item in first.request("/api/notifications")["items"])
    first.request("/api/session", "DELETE", expected=204)
    first.request("/api/candidates", expected=401)
    assert first.session() != owner
    print("Real local flow passed: protected ownership, CSRF, canonical replay/conflict, model evidence, Kafka/SSE, durable notice/read, deletion fence and session reset")
    return candidate


if __name__ == "__main__":
    run()
