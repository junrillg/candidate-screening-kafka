#!/usr/bin/env python3
"""Opt-in faults against this local lab only; preserve volumes and unrelated records."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import signal
import subprocess
import sys
import tempfile
import threading
import concurrent.futures
import http.client
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from flow import API, ORIGIN, Browser, wait_status

ROOT = Path(__file__).absolute().parents[2]
PROJECT = "candidate-screening-kafka"
SERVICES = ("postgres", "kafka", "ollama", "otel-collector", "candidates", "screening")
TEMPORARY = ("candidate-screening-integration-api", "candidate-screening-integration-failure")
HELP = """Run opt-in faults against the running local lab, preserving volumes and unrelated records.
Usage: python3 tests/integration/recovery.py --output PRIVATE_JSON
  --output PATH  Required new JSON file outside the repository; parent must be owned mode 0700.
  help | --help | -h  First argument: print help and stop without preflight or writes.
Requires the ordinary local stack, idle business queue, local Ollama and no-cloud configuration.
Runs retention, admission, broker/API restart, model failure, handler retry, active-lease interruption
and collector outage checks. Restores only services changed by this run and deletes its own records.
Diagnostics contain safe categories and opaque IDs. Real GUI ingestion verification remains separate.
"""


class CheckFailure(Exception):
    """A safe category; never include provider or subprocess exception text."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise CheckFailure("redirect_refused")


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise CheckFailure("invalid_arguments")


def local_endpoint(value):
    uri = urllib.parse.urlsplit(value)
    if (uri.scheme != "http" or uri.hostname != "127.0.0.1" or not uri.port
            or uri.username or uri.password or uri.path not in ("", "/") or uri.query or uri.fragment):
        raise CheckFailure("nonlocal_endpoint")


def private_output(value):
    path = Path(os.path.abspath(value))
    if (path.is_relative_to(ROOT.resolve()) or any((part / ".git").exists() for part in path.parents)
            or any(part.is_symlink() for part in (path, *path.parents))):
        raise CheckFailure("unsafe_output_path")
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700 or path.exists():
        raise CheckFailure("unsafe_output_path")
    return path


def opened(browser):
    browser.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                               urllib.request.HTTPCookieProcessor(browser.jar))
    return browser


def snapshot(service):
    container = compose("ps", "--all", "-q", service)
    if not container or "\n" in container:
        raise CheckFailure("service_identity")
    # Never return Config.Env, credentials, mounts, or application data.
    value = json.loads(docker("inspect", "--format",
        '{"id":{{json .Id}},"image":{{json .Image}},"running":{{.State.Running}},"paused":{{.State.Paused}}}', container))
    if docker("inspect", "--format", '{{index .Config.Labels "com.docker.compose.project"}}', container) != PROJECT:
        raise CheckFailure("service_identity")
    return value


def environment_matches(container, expected, optional=()):
    # Project NAMES first, then booleans for exact expected literals; never return values.
    names = docker("inspect", "--format", '{{range .Config.Env}}{{println (index (split . "=") 0)}}{{end}}', container).splitlines()
    for key, value in expected.items():
        aliases = [name for name in names if name.lower().removeprefix("dotnet_").removeprefix("aspnetcore_")
                   .replace("__", ":") == key.replace("__", ":").lower()]
        if not aliases and key in optional:
            continue  # Verified Local source default; absence is not an observed literal.
        if aliases != [key]:
            raise CheckFailure("local_provider_configuration")
        literal = json.dumps(key + "=" + value)
        answer = docker("inspect", "--format", '{{range .Config.Env}}{{if eq . ' + literal + '}}true{{end}}{{end}}', container)
        if answer != "true":
            raise CheckFailure("local_provider_configuration")


class Run:
    def __init__(self, output):
        self.output = output
        self.lock = threading.Lock()
        self.baseline, self.changed, self.paused, self.temporary = {}, set(), set(), set()
        self.browsers, self.records, self.expired = [], {}, set()
        self.report = {"runId": str(uuid.uuid4()), "status": "running", "cases": {}, "errors": [],
                       "cleanupErrors": [], "ids": [], "guiIngestionVerified": False}

    def save(self):
        with self.lock:
            fd, name = tempfile.mkstemp(prefix=".recovery-", dir=self.output.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    json.dump(self.report, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.output)
            finally:
                if os.path.exists(name):
                    os.unlink(name)

    def preflight(self):
        local_endpoint(API)
        local_endpoint(ORIGIN)
        if os.environ.get("DOCKER_HOST") and os.environ.get("DOCKER_CONTEXT"):
            raise CheckFailure("ambiguous_docker_context")
        host = os.environ.get("DOCKER_HOST") or docker("context", "inspect", "--format", '{{(index .Endpoints "docker").Host}}')
        if not host.startswith("unix://"):
            raise CheckFailure("nonlocal_docker")
        for service in SERVICES:
            value = snapshot(service)
            if not value["running"] or value["paused"]:
                raise CheckFailure("baseline_not_running")
            self.baseline[service] = value
        if docker("network", "inspect", "--format", "{{.Internal}}", PROJECT + "_runtime") != "true":
            raise CheckFailure("runtime_network_not_internal")
        for service in ("candidates", "screening"):
            container = self.baseline[service]["id"]
            if docker("inspect", "--format", "{{json .Config.Entrypoint}} {{json .Config.Cmd}}", container) not in (
                    '["dotnet","service.dll"] null', '["dotnet","service.dll"] []'):
                raise CheckFailure("runtime_command_override")
            environment_matches(container, {"Lab__Deployment": "Local",
                "Lab__KafkaBootstrapServers": "kafka:9092", "Lab__CandidatesUrl": "http://candidates:8080",
                "Lab__ScreeningUrl": "http://screening:8080", "Lab__OtlpEndpoint": "http://otel-collector:4318/v1/logs"},
                ("Lab__Deployment",))
        environment_matches(self.baseline["candidates"]["id"], {"Lab__PublicOrigin": ORIGIN.rstrip("/")})
        environment_matches(self.baseline["screening"]["id"], {"Lab__AiProvider": "Ollama",
            "Lab__OllamaUrl": "http://ollama:11434", "Lab__Model": "qwen2.5:1.5b-instruct"})
        environment_matches(self.baseline["ollama"]["id"], {"OLLAMA_NO_CLOUD": "1"})
        self.report["images"] = {name: value["image"] for name, value in self.baseline.items()}
        self.report["sourceSha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.report["localProviderPreflight"] = True
        if docker("port", self.baseline["candidates"]["id"], "8080/tcp") != API.removeprefix("http://").rstrip("/"):
            raise CheckFailure("api_publication_mismatch")
        for name in TEMPORARY:
            if docker("container", "ls", "-aq", "--filter", "name=^/" + name + "$"):
                raise CheckFailure("temporary_name_in_use")
        self.idle()
        for service in ("postgres", "candidates", "screening", "kafka", "ollama"):
            if not healthy(service):
                raise CheckFailure("baseline_not_healthy")
        self.save()

    def idle(self):
        if sql("candidates", "SELECT count(*) FROM candidates WHERE deleted_at IS NULL AND expires_at>now() AND status IN ('Queued','Screening')") != "0":
            raise CheckFailure("unrelated_queued_work")

    def service(self, action, service):
        if service == "postgres" or action not in {"stop", "start", "restart", "pause", "unpause"}:
            raise CheckFailure("unsupported_service_action")
        if service not in self.baseline or not self.baseline[service]["running"] or self.baseline[service]["paused"]:
            raise CheckFailure("baseline_mutation_refused")
        if snapshot(service)["id"] != self.baseline[service]["id"]:
            raise CheckFailure("service_identity_changed")
        self.changed.add(service)  # Record intent before a daemon operation with an ambiguous response.
        if action == "pause":
            self.paused.add(service)
        compose(action, service)
        if action == "unpause":
            self.paused.discard(service)

    def browser(self, endpoint=API):
        local_endpoint(endpoint)
        browser = opened(Browser(endpoint))
        self.browsers.append(browser)
        return browser

    def track(self, browser, record):
        candidate = str(uuid.UUID(record["id"]))
        with self.lock:
            self.records[candidate] = browser
            self.report["ids"] = sorted(self.records)
        self.save()
        return record

    def create(self, name, *arguments):
        if docker("container", "ls", "-aq", "--filter", "name=^/" + name + "$"):
            raise CheckFailure("temporary_name_in_use")
        self.temporary.add(name)
        docker("run", "-d", "--name", name, "--label", "lab.recovery.run=" + self.report["runId"], *arguments)

    def remove(self, name):
        if name not in self.temporary:
            raise CheckFailure("unowned_temporary_container")
        found = docker("container", "ls", "-aq", "--filter", "name=^/" + name + "$")
        if found:
            if docker("inspect", "--format", '{{index .Config.Labels "lab.recovery.run"}}', found) != self.report["runId"]:
                raise CheckFailure("temporary_identity_changed")
            docker("rm", "-f", found)
        if docker("container", "ls", "-aq", "--filter", "name=^/" + name + "$"):
            raise CheckFailure("temporary_cleanup_failed")
        self.temporary.discard(name)

    def restore(self):
        failures = []
        # Extra consumers first, release only our pause, then dependencies before app services.
        operations = [("temporary_cleanup", lambda name=name: self.remove(name)) for name in sorted(self.temporary)]
        operations += [("service_unpause", lambda name=name: self.service("unpause", name)) for name in sorted(self.paused)]
        operations += [("service_restore", lambda name=name: self.restore_service(name))
                       for name in SERVICES if name in self.changed and self.baseline.get(name, {}).get("running")
                       and not self.baseline[name]["paused"]]
        for category, operation in operations:
            try:
                operation()
            except (Exception, KeyboardInterrupt):
                failures.append(category)
        return failures

    def restore_service(self, name):
        if name in {"candidates", "screening"} and self.temporary:
            raise CheckFailure("temporary_cleanup_required_before_app_restore")
        if name in self.paused:
            raise CheckFailure("unpause_required_before_restore")
        self.service("start", name)

    def clean_records(self):
        # Fresh empty-cookie sessions are the sole discovery scope; no global SQL deletion.
        failures = []
        for browser in self.browsers:
            if not browser.owner or browser.owner in self.expired:
                continue
            browser.api = API
            cursor = None
            try:
                for _ in range(6):
                    page = browser.request("/api/candidates" + ("?cursor=" + urllib.parse.quote(cursor, safe="") if cursor else ""))
                    for record in page["items"]:
                        self.track(browser, record)
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                else:
                    raise CheckFailure("cleanup_page_bound")
            except (Exception, KeyboardInterrupt):
                failures.append("owner_discovery_failed")
        for candidate, browser in list(self.records.items()):
            try:
                if browser.owner not in self.expired:
                    browser.api = API
                    browser.request("/api/candidates/" + candidate, "DELETE", expected=202)
                eventually(lambda candidate=candidate: cleared(candidate), timeout=90)
            except (Exception, KeyboardInterrupt):
                failures.append("owned_record_cleanup_failed")
        if failures:
            self.report["cleanupErrors"].extend(failures)
            raise CheckFailure("owned_record_cleanup_failed")

    def cleanup(self):
        errors = self.restore()
        if self.baseline and (self.changed or self.browsers):
            try:
                eventually(lambda: all(snapshot(name)["id"] == value["id"] and snapshot(name)["running"]
                    and not snapshot(name)["paused"] for name, value in self.baseline.items()), timeout=90)
                eventually(lambda: all(healthy(name) for name in ("postgres", "candidates", "screening", "kafka", "ollama")), timeout=90)
            except (Exception, KeyboardInterrupt):
                errors.append("health_cleanup_failed")
        # A dependency health failure must not prevent an ordinary durable delete if the API is available.
        try:
            self.clean_records()
        except (Exception, KeyboardInterrupt):
            errors.append("record_cleanup_failed")
        self.report["cleanupErrors"].extend(errors)
        self.report["cleanupPassed"] = not self.report["cleanupErrors"]
        return self.report["cleanupPassed"]


def cleared(candidate):
    # Normal retention may already have aged this run's metadata away.
    api = sql("candidates", f"SELECT NOT EXISTS(SELECT 1 FROM candidates WHERE id='{candidate}' AND (display_name IS NOT NULL OR profile IS NOT NULL OR requirements IS NOT NULL OR result IS NOT NULL OR NOT cleanup_confirmed)) AND NOT EXISTS(SELECT 1 FROM notifications WHERE candidate_id='{candidate}')")
    worker = sql("screening", f"SELECT NOT EXISTS(SELECT 1 FROM screening_jobs WHERE id='{candidate}' AND (state<>'Deleted' OR result IS NOT NULL OR lease_id IS NOT NULL OR lease_until IS NOT NULL))")
    return api == "t" and worker == "t"


def docker(*args, data=None):
    result = subprocess.run(["docker", *args], input=data, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, timeout=90)
    if result.returncode:
        raise AssertionError("Local fixture Docker operation failed; private diagnostics required")
    return result.stdout.strip()


def compose(*args):
    return docker("compose", "-p", PROJECT, "-f", str(ROOT / "compose.yaml"), *args)


def sql(database, query):
    return compose("exec", "-T", "postgres", "psql", "--no-psqlrc", "-U", "lab_admin",
                   "-d", database, "-At", "-v", "ON_ERROR_STOP=1", "-c", query)


def eventually(check, timeout=45):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            if check():
                return
        except (urllib.error.URLError, TimeoutError, http.client.RemoteDisconnected):
            pass
        time.sleep(1)
    raise AssertionError("Durable fixture condition did not become true before deadline")


def healthy(service):
    container = compose("ps", "-q", service)
    return docker("inspect", "--format", "{{.State.Health.Status}}", container) == "healthy"


def body():
    return {"displayName": "Synthetic recovery candidate", "profile": "I built Kafka consumers in a service.",
            "requirements": ["Kafka"]}


def submit(run, browser, key=None, expected=202):
    record = browser.request("/api/candidates", "POST", body(), expected,
                           {"Idempotency-Key": key or str(uuid.uuid4())})
    return run.track(browser, record) if expected == 202 else record


def retention(run):
    browser = run.browser()
    owner = uuid.UUID(browser.session())
    key = str(uuid.uuid4())
    record = submit(run, browser, key)
    candidate = uuid.UUID(record["id"])
    browser.request(f"/api/candidates/{candidate}", "DELETE", expected=202)
    eventually(lambda: sql("candidates", f"SELECT cleanup_confirmed FROM candidates WHERE id='{candidate}'") == "t")
    # Synthetic acknowledged tombstones exercise the owner metadata bound without running many inferences.
    sql("candidates", f"""INSERT INTO candidates(id,owner_id,request_key,payload_hash,status,expires_at,deleted_at,cleanup_confirmed)
        SELECT gen_random_uuid(),'{owner}',gen_random_uuid(),decode(repeat('00',32),'hex'),'Deleting',
          now()+interval '7 days',now(),true FROM generate_series(1,99)""")
    assert sql("candidates", f"SELECT count(*) FROM candidates WHERE owner_id='{owner}'") == "100"
    submit(run, browser, expected=429)
    submit(run, browser, key, expected=410)
    # Advance only this fixture's deadlines; the normal periodic cleanup must remove its acknowledged metadata.
    run.expired.add(str(owner))
    sql("candidates", f"UPDATE owner_sessions SET expires_at=now()-interval '9 days' WHERE id='{owner}'")
    sql("candidates", f"UPDATE candidates SET expires_at=now()-interval '9 days' WHERE owner_id='{owner}'")
    eventually(lambda: sql("candidates", f"SELECT count(*) FROM candidates WHERE owner_id='{owner}'") == "0")
    eventually(lambda: sql("candidates", f"SELECT count(*) FROM owner_sessions WHERE id='{owner}'") == "0")
    print("Retained owner metadata cap and acknowledged expiry cleanup passed", flush=True)

    expiring = run.browser()
    expired_owner = uuid.UUID(expiring.session())
    expired_key = str(uuid.uuid4())
    expired_id = uuid.UUID(submit(run, expiring, expired_key)["id"])
    sql("candidates", f"UPDATE candidates SET expires_at=now()-interval '1 second' WHERE id='{expired_id}'")
    expiring.request(f"/api/candidates/{expired_id}", expected=404)
    expiring.request(f"/api/candidates/{expired_id}/result", expected=404)
    eventually(lambda: sql("candidates", f"SELECT profile IS NULL AND requirements IS NULL AND result IS NULL AND cleanup_confirmed FROM candidates WHERE id='{expired_id}'") == "t")
    eventually(lambda: sql("screening", f"SELECT state='Deleted' AND result IS NULL FROM screening_jobs WHERE id='{expired_id}'") == "t")
    late_event = uuid.uuid4()
    sql("screening", f"""INSERT INTO flow_outbox(id,topic,payload,expires_at)
        VALUES('{late_event}','screening-completed',jsonb_build_object('version',1,'eventId','{late_event}',
          'candidateId','{expired_id}','ownerId','{expired_owner}','kind','Completed','occurredAt',now(),
          'expiresAt',now()+interval '1 day'),now()+interval '1 day')""")
    eventually(lambda: sql("candidates", f"SELECT count(*) FROM flow_inbox WHERE id='{late_event}'") == "1")
    assert sql("candidates", f"SELECT profile IS NULL AND result IS NULL FROM candidates WHERE id='{expired_id}'") == "t"
    assert sql("candidates", f"SELECT count(*) FROM notifications WHERE candidate_id='{expired_id}'") == "0"
    submit(run, expiring, expired_key, expected=410)
    print("Active source expiry, worker cleanup and late completion fence passed", flush=True)


def admission(run):
    temporary = "candidate-screening-integration-api"
    owners = []
    accepted = []
    try:
        run.idle()
        run.service("stop", "screening")
        assert sql("candidates", "SELECT count(*) FROM candidates WHERE deleted_at IS NULL AND expires_at>now() AND status IN ('Queued','Screening')") == "0", "Unrelated queued work exists; stop fixture"
        volume = docker("volume", "ls", "--filter", "label=com.docker.compose.project=candidate-screening-kafka",
                        "--filter", "label=com.docker.compose.volume=keys", "--format", "{{.Name}}")
        assert volume and "\n" not in volume
        run.create(temporary, "--network", "candidate-screening-kafka_inspection", "--network", "candidate-screening-kafka_runtime",
               "-p", "127.0.0.1::8080", "--memory", "128m", "--cpus", "0.5", "--read-only", "--tmpfs", "/tmp:uid=1654,gid=1654,mode=0700",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--env-file", ".secrets/candidates.env",
               "-v", volume + ":/keys", "-e", "Lab__ServiceName=candidates", "-e", "Lab__MaxQueued=2",
               "-e", "Lab__KafkaBootstrapServers=kafka:9092", "-e", "Lab__OtlpEndpoint=http://otel-collector:4318/v1/logs",
               "-e", "Lab__Deployment=Local", "-e", "Lab__AiProvider=Ollama",
               run.baseline["candidates"]["image"])
        eventually(lambda: docker("inspect", "--format", "{{.State.Health.Status}}", temporary) == "healthy")
        endpoint = "http://" + docker("port", temporary, "8080/tcp")
        unicode_browser = run.browser(endpoint)
        unicode_browser.session()
        owners.append(unicode_browser)
        unicode_record = unicode_browser.request("/api/candidates", "POST", {
            "displayName": "Synthetic Unicode boundary", "profile": "漢" * 12000,
            "requirements": [str(index) + "漢" * 199 for index in range(8)]
        }, 202, {"Idempotency-Key": str(uuid.uuid4())})
        run.track(unicode_browser, unicode_record)
        unicode_browser.request("/api/candidates/" + unicode_record["id"], "DELETE", expected=202)
        for _ in range(8):
            browser = run.browser(endpoint)
            browser.session()
            owners.append(browser)

        def create(browser):
            try:
                return browser, submit(run, browser)
            except AssertionError as error:
                assert ": 429, expected 202" in str(error), "Concurrent admission returned unexpected failure"
                return browser, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(create, owners[1:]))
        accepted = [(browser, record) for browser, record in results if record]
        assert 1 <= len(accepted) <= 2, "Global pending admission bound was exceeded"
        assert int(sql("candidates", "SELECT count(*) FROM candidates WHERE deleted_at IS NULL AND expires_at>now() AND status IN ('Queued','Screening')")) == len(accepted)
        browser, record = accepted[0]
        # Existing protected identity and antiforgery token must work on the other replica.
        primary = Browser()
        primary.jar = browser.jar
        opened(primary)
        primary.owner, primary.csrf = browser.owner, browser.csrf
        assert primary.session() == browser.owner
        assert primary.request("/api/candidates/" + record["id"])["status"] == "Queued"
        # Streams are bounded per owner while ordinary reads retain capacity.
        streams = []
        try:
            for _ in range(4):
                streams.append(primary.http.open(primary.api + "/api/events", timeout=5))
            primary.request("/api/events", expected=429)
            primary.request("/api/candidates/" + record["id"])
        finally:
            for stream in streams:
                stream.close()
        print("Concurrent global admission, shared replica owner keys and owner SSE bound passed", flush=True)
    finally:
        try:
            if temporary in run.temporary:
                run.remove(temporary)
        finally:
            if "screening" in run.changed:
                run.restore_service("screening")
                eventually(lambda: healthy("screening"))



def broker_recovery(run):
    browser = run.browser()
    owner = browser.session()
    key = str(uuid.uuid4())
    stopped = False
    try:
        run.idle()
        stopped = True
        run.service("stop", "kafka")
        record = submit(run, browser, key)
        candidate = uuid.UUID(record["id"])
        assert browser.request(f"/api/candidates/{candidate}")["status"] == "Queued"
        assert submit(run, browser, key) == record
        assert sql("candidates", f"SELECT count(*) FROM flow_outbox WHERE payload->>'candidateId'='{candidate}' AND topic='candidate-submitted' AND sent_at IS NULL") == "1"
        # Restart API while broker is down: committed acceptance and protected ownership must survive.
        run.service("restart", "candidates")
        eventually(lambda: browser.session() == owner, timeout=30)
        assert browser.request(f"/api/candidates/{candidate}")["status"] == "Queued"
    finally:
        if stopped:
            run.service("start", "kafka")
            eventually(lambda: healthy("kafka"), timeout=90)
    assert wait_status(browser, str(candidate), {"Completed", "Failed"})["status"] == "Completed"
    matching = [item for item in browser.request("/api/notifications")["items"] if item["candidateId"] == str(candidate)]
    assert len(matching) == 1
    # Replay the same durable business events; inbox and unique notice must keep effects singular.
    sql("candidates", f"UPDATE flow_outbox SET sent_at=NULL,due_at=now(),lease_until=NULL WHERE payload->>'candidateId'='{candidate}' AND topic='candidate-submitted'")
    sql("screening", f"UPDATE flow_outbox SET sent_at=NULL,due_at=now(),lease_until=NULL WHERE payload->>'candidateId'='{candidate}' AND topic='screening-completed'")
    time.sleep(5)
    assert len([item for item in browser.request("/api/notifications")["items"] if item["candidateId"] == str(candidate)]) == 1
    browser.request(f"/api/candidates/{candidate}", "DELETE", expected=202)
    print("Broker outage durable acceptance, API restart and duplicate event effects passed", flush=True)


def failure(run):
    browser = run.browser()
    browser.session()
    temporary = "candidate-screening-integration-failure"
    try:
        run.idle()
        run.service("stop", "screening")
        run.create(temporary, "--network", "candidate-screening-kafka_runtime",
               "--memory", "128m", "--cpus", "0.5", "--read-only", "--tmpfs", "/tmp:uid=1654,gid=1654,mode=0700", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges:true", "--env-file", ".secrets/screening.env",
               "-e", "Lab__ServiceName=screening", "-e", "Lab__KafkaBootstrapServers=kafka:9092",
               "-e", "Lab__OtlpEndpoint=http://otel-collector:4318/v1/logs",
               "-e", "Lab__Model=synthetic-nonexistent-local-fixture", "-e", "Lab__OllamaUrl=http://ollama:11434",
               "-e", "Lab__Deployment=Local", "-e", "Lab__AiProvider=Ollama",
               run.baseline["screening"]["image"])
        record = submit(run, browser)
        candidate = uuid.UUID(record["id"])
        assert wait_status(browser, str(candidate), {"Completed", "Failed"}, timeout=90)["status"] == "Failed"
        assert sql("screening", f"SELECT attempts FROM screening_jobs WHERE id='{candidate}'") == "3"
        run.report["failureCandidateId"] = str(candidate)
        run.report["failureGuiSignal"] = "screening_retry"
        run.save()
        assert len([item for item in browser.request("/api/notifications")["items"] if item["candidateId"] == str(candidate)]) == 1
        browser.request(f"/api/candidates/{candidate}/result", expected=409)
        browser.request(f"/api/candidates/{candidate}", "DELETE", expected=202)
    finally:
        try:
            if temporary in run.temporary:
                run.remove(temporary)
        finally:
            run.restore_service("screening")
            eventually(lambda: healthy("screening"))
    print("Real unavailable-model bounded retries and durable Failed notice passed", flush=True)


def handler_recovery(run):
    browser = run.browser()
    owner = uuid.UUID(browser.session())
    event = uuid.uuid4()
    try:
        run.idle()
        run.service("stop", "screening")
        candidate = uuid.UUID(submit(run, browser)["id"])
        # Fault injection is confined to this fixture's row; it does not replace the real model-flow proof.
        sql("screening", f"""INSERT INTO screening_jobs(id,owner_id,expires_at,state,result)
            VALUES('{candidate}','{owner}',now()+interval '7 days','Completed',jsonb_build_object(
              'candidateId','{candidate}','items',jsonb_build_array(jsonb_build_object('requirement','Kafka',
              'evidence','Synthetic quote absent from source','state','EvidenceFound')),
              'reviewRequired',true,'provider','Ollama','model','synthetic-fault-fixture'));
            INSERT INTO flow_outbox(id,topic,payload,expires_at) VALUES('{event}','screening-completed',
              jsonb_build_object('version',1,'eventId','{event}','candidateId','{candidate}','ownerId','{owner}',
              'kind','Completed','occurredAt',now(),'expiresAt',now()+interval '7 days'),now()+interval '7 days')""")
    finally:
        run.restore_service("screening")
        eventually(lambda: healthy("screening"))
    eventually(lambda: sql("candidates", f"SELECT attempts=10 AND exhausted_at IS NOT NULL FROM flow_handler_failures WHERE id='{event}'") == "t", timeout=60)
    failed = browser.request(f"/api/candidates/{candidate}")
    assert failed["status"] == "Failed" and failed["errorCode"] == "inference_failed"
    assert sql("candidates", f"SELECT count(*) FROM notifications WHERE candidate_id='{candidate}' AND status='Failed'") == "1"
    listing = compose("exec", "-T", "candidates", "dotnet", "service.dll", "--event-failures")
    assert str(event) in listing and "screening-completed" in listing and "Synthetic quote" not in listing
    sql("screening", f"""UPDATE screening_jobs SET result=jsonb_set(result,'{{items,0,evidence}}',
        to_jsonb('I built Kafka consumers in a service.'::text)) WHERE id='{candidate}'""")
    compose("exec", "-T", "candidates", "dotnet", "service.dll", "--retry-event", str(event))
    assert wait_status(browser, str(candidate), {"Completed", "Failed"}, timeout=30)["status"] == "Completed"
    assert sql("candidates", f"SELECT count(*) FROM notifications WHERE candidate_id='{candidate}'") == "1"
    assert sql("candidates", f"SELECT status='Completed' AND NOT read FROM notifications WHERE candidate_id='{candidate}'") == "t"
    assert sql("candidates", f"SELECT count(*) FROM flow_handler_failures WHERE id='{event}'") == "0"
    browser.request(f"/api/candidates/{candidate}", "DELETE", expected=202)
    print("Invalid completion quarantined after bounded durable attempts; trusted retry recovered exactly once", flush=True)


def completed(browser, candidate, timeout=240):
    assert wait_status(browser, candidate, {"Completed", "Failed"}, timeout=timeout)["status"] == "Completed"
    result = browser.request("/api/candidates/" + candidate + "/result")
    assert result["candidateId"] == candidate and result["reviewRequired"] is True
    assert result["provider"] == "Ollama" and result["model"] == "qwen2.5:1.5b-instruct"
    assert len(result["items"]) == 1 and result["items"][0]["requirement"] == "Kafka"
    item = result["items"][0]
    assert item["state"] in {"EvidenceFound", "NeedsReview", "NotEvidenced"}
    assert (item["evidence"] is None) == (item["state"] == "NotEvidenced")
    assert item["evidence"] is None or item["evidence"] in body()["profile"]
    assert len([notice for notice in browser.request("/api/notifications")["items"]
                if notice["candidateId"] == candidate]) == 1


def active_lease(run):
    run.idle()
    browser = run.browser()
    browser.session()
    try:
        run.service("pause", "ollama")
        candidate = submit(run, browser)["id"]
        eventually(lambda: sql("screening", f"SELECT state='Screening' AND lease_id IS NOT NULL AND lease_until>now() AND result IS NULL FROM screening_jobs WHERE id='{candidate}'") == "t")
        lease, attempt = sql("screening", f"SELECT lease_id,attempts FROM screening_jobs WHERE id='{candidate}'").split("|")
        lease, attempt = str(uuid.UUID(lease)), int(attempt)
        run.service("stop", "screening")
        assert not snapshot("screening")["running"]
        assert sql("screening", f"SELECT state='Screening' AND lease_id='{lease}' AND result IS NULL AND attempts={attempt} FROM screening_jobs WHERE id='{candidate}'") == "t"
        run.report["activeLease"] = {"candidateId": candidate, "attemptBeforeRestart": attempt,
                                     "persistedAcrossStop": True}
        run.save()
    finally:
        if "ollama" in run.paused:
            run.service("unpause", "ollama")
        if "screening" in run.changed:
            run.restore_service("screening")
            eventually(lambda: healthy("screening"))
    completed(browser, candidate, timeout=360)
    assert sql("screening", f"SELECT attempts>{attempt} AND lease_id IS NULL AND lease_until IS NULL FROM screening_jobs WHERE id='{candidate}'") == "t"
    run.report["activeLease"]["reclaimedAfterRestart"] = True
    browser.request("/api/candidates/" + candidate, "DELETE", expected=202)
    print("Persisted active lease survived worker interruption and recovered with one result and notice", flush=True)


def console_signals(service, since, candidate):
    selected = []
    raw = docker("logs", "--since", since, "--tail", "256", snapshot(service)["id"])
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        state = value.get("State")
        if (not isinstance(state, dict) or state.get("CandidateId") != candidate
                or state.get("State") not in {"screening_started", "screening_completed", "outbox_published"}):
            continue
        # No message, exceptions, source text, provider payload, or arbitrary attributes escape this allowlist.
        selected.append({"service": service, "state": state["State"], "candidateId": candidate})
    return selected


def collector_outage(run):
    run.idle()
    browser = run.browser()
    browser.session()
    since = datetime.now(timezone.utc).isoformat()
    try:
        run.service("stop", "otel-collector")
        assert not snapshot("otel-collector")["running"]
        candidate = submit(run, browser)["id"]
        completed(browser, candidate)
        assert not snapshot("otel-collector")["running"]
        signals = console_signals("screening", since, candidate)
        assert any(signal["state"] == "screening_completed" for signal in signals)
        run.report["collectorOutage"] = {"candidateId": candidate, "businessCompletedWhileStopped": True,
            "stdoutSignals": signals, "outageSince": since, "postRestartGuiVerified": False}
        run.save()
    finally:
        if "otel-collector" in run.changed:
            run.service("start", "otel-collector")
            eventually(lambda: snapshot("otel-collector")["running"])
    # A new ordinary deletion after collector restart supplies a fresh correlated GUI-query boundary.
    restarted = datetime.now(timezone.utc).isoformat()
    browser.request("/api/candidates/" + candidate, "DELETE", expected=202)
    eventually(lambda: cleared(candidate), timeout=90)
    event = sql("candidates", f"SELECT id FROM flow_outbox WHERE payload->>'candidateId'='{candidate}' AND topic='candidate-submitted' AND payload->>'kind'='Deleted' ORDER BY created_at DESC LIMIT 1")
    event = str(uuid.UUID(event))
    eventually(lambda: sql("candidates", f"SELECT sent_at IS NOT NULL FROM flow_outbox WHERE id='{event}'") == "t")
    run.report["collectorOutage"].update({"freshDeletionEventId": event, "freshDeletionAfter": restarted,
                                        "freshDeletionPublished": True})
    print("Collector outage preserved real business completion and console signals; GUI ingestion remains pending", flush=True)


def execute(run):
    try:
        run.preflight()
        for case in (retention, admission, broker_recovery, failure, handler_recovery, active_lease, collector_outage):
            run.report["currentCase"] = case.__name__
            run.report["cases"][case.__name__] = "running"
            run.save()
            case(run)
            run.clean_records()
            run.report["cases"][case.__name__] = "passed"
            run.save()
        run.report["status"] = "passed"
    except (Exception, KeyboardInterrupt):
        run.report["status"] = "failed"
        run.report["errors"].append("recovery_check_failed")
        if run.report.get("currentCase"):
            run.report["cases"][run.report["currentCase"]] = "failed"
    finally:
        try:
            if not run.cleanup():
                run.report["status"] = "failed"
        except (Exception, KeyboardInterrupt):
            run.report["status"] = "failed"
            run.report["cleanupErrors"].append("cleanup_failed")
        try:
            run.save()
        except (Exception, KeyboardInterrupt):
            run.report["status"] = "failed"  # Persistence failure can never produce a passing exit.
    return run.report["status"] == "passed"


def interrupted(signum, frame):
    raise KeyboardInterrupt()


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    if arguments and arguments[0].lower() in {"help", "--help", "-h"}:
        print(HELP)
        return 0
    try:
        parser = Arguments(add_help=False)
        parser.add_argument("--output", required=True)
        args = parser.parse_args(arguments)
        output = private_output(args.output)
        # Reserve a new owner-only evidence file before any infrastructure access.
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            passed = execute(Run(output))
        finally:
            signal.signal(signal.SIGTERM, previous)
        print("Recovery checks passed; GUI ingestion remains pending" if passed else
              "Recovery checks failed; inspect the private evidence categories", flush=True)
        return 0 if passed else 1
    except (Exception, KeyboardInterrupt):
        print("Recovery preflight or evidence setup failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
