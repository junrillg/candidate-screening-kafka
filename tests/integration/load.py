#!/usr/bin/env python3
"""Opt-in local HTTP/SSE measurements; inputs and results stay owner-private."""
import argparse
import concurrent.futures
import copy
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HELP = """Usage: load.py --config PRIVATE_JSON --output PRIVATE_JSON --local-ollama-confirmed
Purpose: measure a bounded synthetic flow through actual local API/SSE replicas.
--config PATH   Required strict JSON outside the repository, private parent/file.
--output PATH   Required new private result file outside the repository.
--local-ollama-confirmed  Required operator confirmation of running Ollama configuration.
                No provider discovery occurs; this flag is not runtime/provider proof.
help | --help | -h  First argument prints help and stops without file/network access.
No workload defaults. Required JSON fields:
  endpoints (distinct literal-loopback HTTP(S) origins), origin (loopback origin),
  expectedProvider (Ollama), owners/jobs/concurrency/maxPages/maxFrameBytes (positive ints),
  idleSeconds/requestTimeoutSeconds/streamReadTimeoutSeconds/terminalTimeoutSeconds/
  pollSeconds/deliveryTimeoutSeconds/cleanupTimeoutSeconds (positive finite seconds),
  thresholds: acceptanceP95Seconds/completionP95Seconds (positive seconds),
              maxErrorRate/minCompletedFraction (fractions in [0,1]).
Concurrency cannot exceed jobs; cleanup timeout must cover stream and request timeouts.
Examples (paths are illustrative; create private inputs before a real run):
  python3 tests/integration/load.py help
  python3 tests/integration/load.py --help
  python3 tests/integration/load.py --config /private/run/workload.json --output /private/run/result.json --local-ollama-confirmed
  python3 -m unittest discover -s tests/integration -p test_load.py
Will ask: nothing. Invalid input or failed checks exit unsuccessfully; no Docker/cloud calls.
Measurements are client observations, not provider-only throughput or full-capacity proof.
Companion host/DB/queue/UI/failure probes must be collected separately.
"""


class CheckFailure(Exception):
    """Fixed diagnostic category; never include HTTP bodies or exception messages."""


def require(condition, category):
    if not condition:
        raise CheckFailure(category)


def unique_object(pairs):
    require(len(dict(pairs)) == len(pairs), "duplicate_json_field")
    return dict(pairs)


def loopback(value):
    require(isinstance(value, str), "invalid_endpoint")
    try:
        url = urllib.parse.urlsplit(value)
        valid = (url.scheme in ("http", "https") and url.port is not None and url.port > 0
                 and ipaddress.ip_address(url.hostname).is_loopback
                 and "@" not in url.netloc and not url.query
                 and not url.fragment and url.path in ("", "/"))
    except (ValueError, TypeError):
        valid = False
    require(valid, "invalid_endpoint")
    return value.rstrip("/")


def positive(value, integer=False):
    try:
        return (type(value) is int if integer else type(value) in (int, float)) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def config(value):
    counts = {"owners", "jobs", "concurrency", "maxPages", "maxFrameBytes"}
    seconds = {"idleSeconds", "requestTimeoutSeconds", "streamReadTimeoutSeconds", "terminalTimeoutSeconds",
               "pollSeconds", "deliveryTimeoutSeconds", "cleanupTimeoutSeconds"}
    require(isinstance(value, dict) and set(value) == counts | seconds |
            {"endpoints", "origin", "expectedProvider", "thresholds"}, "invalid_config_fields")
    require(all(positive(value[k], True) for k in counts) and
            all(positive(value[k]) for k in seconds), "invalid_bounds")
    require(value["concurrency"] <= value["jobs"] and value["pollSeconds"] <= value["terminalTimeoutSeconds"]
            and value["cleanupTimeoutSeconds"] >= value["streamReadTimeoutSeconds"] + value["requestTimeoutSeconds"], "invalid_bounds")
    endpoints = value["endpoints"]
    require(isinstance(endpoints, list) and endpoints, "invalid_endpoint")
    endpoints = [loopback(url) for url in endpoints]
    require(len(set(endpoints)) == len(endpoints), "duplicate_endpoint")
    result = dict(value, endpoints=endpoints, origin=loopback(value["origin"]))
    require(result["expectedProvider"] == "Ollama", "invalid_provider")
    thresholds = value["thresholds"]
    require(isinstance(thresholds, dict) and set(thresholds) ==
            {"acceptanceP95Seconds", "completionP95Seconds", "maxErrorRate", "minCompletedFraction"}, "invalid_thresholds")
    require(all(positive(thresholds[k]) for k in ("acceptanceP95Seconds", "completionP95Seconds")), "invalid_thresholds")
    require(all(type(thresholds[k]) in (int, float) and math.isfinite(thresholds[k]) and 0 <= thresholds[k] <= 1
                for k in ("maxErrorRate", "minCompletedFraction")), "invalid_thresholds")
    return result


def private_path(path, repo, existing):
    path = Path(os.path.abspath(path))
    require(not path.is_relative_to(repo.resolve()), "path_inside_repository")
    require(all(not part.is_symlink() for part in (path, *path.parents)), "symlink_path")
    parent = path.parent.stat()
    require(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700, "nonprivate_parent")
    if existing:
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid() and
                stat.S_IMODE(info.st_mode) == 0o600, "nonprivate_file")
    else:
        require(not path.exists(), "output_exists")
    return path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        if response is not None:
            response.close()
        raise CheckFailure("redirect_rejected")


def client(endpoint, cfg, source=None):
    # Lazy import preserves the side-effect-free help path.
    from flow import Browser
    browser = Browser(endpoint)
    browser.deadline = None
    browser.last_http_status = None
    if source is not None:
        for cookie in source.jar:
            browser.jar.set_cookie(copy.copy(cookie))
        browser.owner, browser.csrf = source.owner, source.csrf
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                        urllib.request.HTTPCookieProcessor(browser.jar), NoRedirect())

    class BoundedOpener:
        def open(self, request, timeout=None):
            browser.last_http_status = None
            require(urllib.parse.urlsplit(request.full_url).netloc == urllib.parse.urlsplit(endpoint).netloc
                    and urllib.parse.urlsplit(request.full_url).scheme == urllib.parse.urlsplit(endpoint).scheme,
                    "nonlocal_request")
            request.add_header("Origin", cfg["origin"])
            timeout = cfg["requestTimeoutSeconds"]
            if browser.deadline is not None:
                timeout = min(timeout, browser.deadline - time.monotonic())
                require(timeout > 0, "request_deadline")
            try:
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                browser.last_http_status = error.code
                raise
            browser.last_http_status = response.status
            return response

    browser.http = BoundedOpener()
    browser.open_stream = lambda: opener.open(urllib.request.Request(endpoint + "/api/events",
                                               headers={"Origin": cfg["origin"]}),
                                             timeout=cfg["streamReadTimeoutSeconds"])
    return browser


def job_error(error, http_status):
    """Fixed categories only: exception messages and HTTP content are never evidence."""
    fields = {"error": "job_failed"}
    if type(http_status) is int and 100 <= http_status <= 599:
        fields["httpStatus"] = http_status
    if isinstance(error, CheckFailure):
        fields["exceptionCategory"] = "check_failed"
        if error.args and type(error.args[0]) is str and error.args[0] in {
                "result_contract", "terminal_timeout", "request_deadline", "nonlocal_request", "redirect_rejected"}:
            fields["checkCode"] = error.args[0]
    elif fields.get("httpStatus", 0) >= 300:
        fields["exceptionCategory"] = "unexpected_http_status"
    elif isinstance(error, TimeoutError) or (
            isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)):
        fields["exceptionCategory"] = "request_timeout"
    elif isinstance(error, urllib.error.URLError):
        fields["exceptionCategory"] = "connection_failure"
    elif isinstance(error, AssertionError):
        fields["exceptionCategory"] = "assertion_failed"
    elif isinstance(error, (ValueError, KeyError)):
        fields["exceptionCategory"] = "response_contract"
    elif isinstance(error, OSError):
        fields["exceptionCategory"] = "transport_error"
    else:
        fields["exceptionCategory"] = "unexpected_exception"
    return fields


class Stream:
    def __init__(self, browser, cfg):
        self.browser, self.cfg = browser, cfg
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self._terminal, self._seen = set(), set()
        self.error = None
        self.thread = threading.Thread(target=self.read, daemon=True)

    @property
    def terminal(self):
        with self.lock:
            return self._terminal.copy()

    @property
    def seen(self):
        with self.lock:
            return self._seen.copy()

    def read(self):
        try:
            with self.browser.open_stream() as response:
                require(response.headers.get_content_type() == "text/event-stream", "invalid_sse_type")
                event, data = None, []
                while not self.stop.is_set():
                    raw = response.readline(self.cfg["maxFrameBytes"] + 1)
                    require(raw and len(raw) <= self.cfg["maxFrameBytes"], "sse_closed_or_oversized")
                    line = raw.decode("utf-8").rstrip("\r\n")
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        data.append(line[6:])
                        require(sum(map(len, data)) <= self.cfg["maxFrameBytes"], "sse_oversized")
                    elif not line and data:
                        payload = json.loads("\n".join(data))
                        if event == "reconcile":
                            require(payload == {}, "invalid_sse")
                            self.ready.set()
                        elif event == "change":
                            require(set(payload) == {"candidateId"}, "invalid_sse")
                            candidate = str(uuid.UUID(payload["candidateId"]))
                            with self.lock:
                                self._seen.add(candidate)
                            current = self.browser.request("/api/candidates/" + candidate)
                            if current["status"] in ("Completed", "Failed"):
                                with self.lock:
                                    self._terminal.add(candidate)
                        else:
                            raise CheckFailure("invalid_sse")
                        event, data = None, []
        except Exception:
            if not self.stop.is_set():
                self.error = "sse_failure"


def close_streams(streams, timeout):
    until = time.monotonic() + timeout
    for stream in streams:
        stream.stop.set()
    for stream in streams:
        if stream.thread.ident is not None:
            stream.thread.join(max(0, until - time.monotonic()))
    return all(not stream.thread.is_alive() for stream in streams)


def pages(browser, path, limit):
    cursor, seen = None, set()
    for _ in range(limit):
        value = browser.request(path + ("?" + urllib.parse.urlencode({"cursor": cursor}) if cursor else ""))
        require(isinstance(value.get("items"), list), "invalid_page")
        yield from value["items"]
        cursor = value.get("nextCursor")
        if cursor is None:
            return
        require(isinstance(cursor, str) and cursor and cursor not in seen, "invalid_cursor")
        seen.add(cursor)
    raise CheckFailure("page_bound_exceeded")


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def summarize(rows, cfg, elapsed):
    accepted = [r["acceptanceSeconds"] for r in rows if "acceptanceSeconds" in r]
    completed = [r["completionSeconds"] for r in rows if r.get("completed")]
    errors = sum("error" in r or r.get("terminal") == "Failed" for r in rows)
    metrics = {"elapsedSeconds": elapsed, "attempted": len(rows), "accepted": len(accepted), "completed": len(completed),
               "errorRate": errors / len(rows), "completedFraction": len(completed) / len(rows),
               "acceptanceP50Seconds": percentile(accepted, .5),
               "acceptanceP95Seconds": percentile(accepted, .95),
               "acceptanceP99Seconds": percentile(accepted, .99),
               "completionP50Seconds": percentile(completed, .5),
               "completionP95Seconds": percentile(completed, .95),
               "completionP99Seconds": percentile(completed, .99),
               "observedEndToEndCompletionsPerSecond": len(completed) / elapsed if elapsed > 0 else None}
    thresholds = cfg["thresholds"]
    passed = (all(metrics[k] is not None and metrics[k] <= thresholds[k]
                  for k in ("acceptanceP95Seconds", "completionP95Seconds")) and
              metrics["errorRate"] <= thresholds["maxErrorRate"] and
              metrics["completedFraction"] >= thresholds["minCompletedFraction"])
    return {"metrics": metrics, "thresholdsPassed": passed,
            "claims": {"fullCapacityProven": False, "providerInferenceThroughputMeasured": False,
                       "crossReplicaAuthScopeAvailable": len(cfg["endpoints"]) > 1},
            "unmeasured": ["provider-only service time/throughput", "server CPU/memory/file descriptors",
                           "database queries/connections/locks", "queue age/relay lag", "browser navigation",
                           "slow-reader resource bounds", "dependency outage/restore", "sustained capacity",
                           "cross-service deletion acknowledgment", "closed-browser completion"]}


def concurrent_jobs(job, cfg, rows):
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=cfg["concurrency"])
    futures = []
    try:
        for index in range(cfg["jobs"]):
            futures.append(pool.submit(job, index))
        for future in futures:
            rows.append(future.result())
    finally:
        # Interrupts cancel queued work; active requests/jobs retain their configured deadlines.
        pool.shutdown(wait=True, cancel_futures=True)


def cleanup(owners, pending, cfg):
    failures, until = [], time.monotonic() + cfg["cleanupTimeoutSeconds"]
    for browser in owners:
        browser.deadline = until
        own = pending[browser.owner]
        discovered = False
        try:
            # Discover responses lost after commit only within fresh fixture owners and exact own markers.
            for record in pages(browser, "/api/candidates", cfg["maxPages"]):
                if record.get("displayName") in own["markers"]:
                    own["ids"].add(str(uuid.UUID(record["id"])))
            discovered = True
        except Exception:
            failures.append("cleanup_discovery_failed")
        for candidate in list(own["ids"]):
            try:
                require(time.monotonic() < until, "cleanup_deadline")
                browser.request("/api/candidates/" + candidate, "DELETE", expected=202)
                browser.request("/api/candidates/" + candidate, expected=404)
                own["ids"].remove(candidate)
            except Exception:
                failures.append("record_cleanup_failed")
        if discovered and not own["ids"]:
            try:
                browser.request("/api/session", "DELETE", expected=204)
            except Exception:
                failures.append("session_cleanup_failed")
    return failures


def run(cfg, make_client=client):
    owners, streams, pending, rows, checks, failures = [], [], {}, [], {}, []
    pending_lock = threading.Lock()
    report = {"status": "failed", "errors": failures,
              "claims": {"fullCapacityProven": False, "providerInferenceThroughputMeasured": False}}
    try:
        for index in range(cfg["owners"]):
            browser = make_client(cfg["endpoints"][index % len(cfg["endpoints"])], cfg)
            owners.append(browser)
            browser.session()
            pending[browser.owner] = {"ids": set(), "markers": set()}
            for endpoint in cfg["endpoints"]:
                replica = make_client(endpoint, cfg, browser)
                require(replica.session() == browser.owner, "replica_auth_mismatch")
                stream = Stream(replica, cfg)
                streams.append(stream)
                stream.thread.start()
        checks["sharedOwnerAuthAcrossSuppliedEndpoints"] = True
        until = time.monotonic() + cfg["deliveryTimeoutSeconds"]
        for stream in streams:
            require(stream.ready.wait(max(0, until - time.monotonic())), "sse_initial_reconciliation")
        time.sleep(cfg["idleSeconds"])
        require(all(s.error is None and s.thread.is_alive() for s in streams), "idle_sse_failed")
        checks["idleAuthorizedStreams"] = True
        started = time.monotonic()

        def job(index):
            owner = owners[index % len(owners)]
            browser = make_client(cfg["endpoints"][index % len(cfg["endpoints"])], cfg, owner)
            marker = "Synthetic load " + str(uuid.uuid4())
            with pending_lock:
                pending[owner.owner]["markers"].add(marker)
            row, began, stage = {}, time.monotonic(), "submission"
            browser.deadline = began + cfg["terminalTimeoutSeconds"]
            try:
                profile = "I implemented Kafka consumers in a service."
                record = browser.request("/api/candidates", "POST", {"displayName": marker,
                    "profile": profile, "requirements": ["Kafka"]}, 202, {"Idempotency-Key": str(uuid.uuid4())})
                candidate = str(uuid.UUID(record["id"]))
                row["candidateId"] = candidate
                with pending_lock:
                    pending[owner.owner]["ids"].add(candidate)
                row["acceptanceSeconds"] = time.monotonic() - began
                until = began + cfg["terminalTimeoutSeconds"]
                while time.monotonic() < until:
                    stage = "status"
                    state = browser.request("/api/candidates/" + candidate)["status"]
                    if state in ("Completed", "Failed"):
                        row["terminal"] = state
                        row["completionSeconds"] = time.monotonic() - began
                        if state == "Completed":
                            stage = "result"
                            result = browser.request("/api/candidates/" + candidate + "/result")
                            require(result["provider"] == cfg["expectedProvider"] and result["reviewRequired"] is True
                                    and result["candidateId"] == candidate and len(result["items"]) == 1,
                                    "result_contract")
                            item = result["items"][0]
                            evidence = item["evidence"]
                            require(item["requirement"] == "Kafka" and item["state"] in
                                    ("EvidenceFound", "NeedsReview", "NotEvidenced") and
                                    ((evidence is None) == (item["state"] == "NotEvidenced")) and
                                    (evidence is None or isinstance(evidence, str) and evidence in profile), "result_contract")
                            row["completed"] = True
                        return row
                    time.sleep(min(cfg["pollSeconds"], max(0, until - time.monotonic())))
                raise CheckFailure("terminal_timeout")
            except Exception as error:
                row.update(job_error(error, getattr(browser, "last_http_status", None)))
                row.update(errorStage=stage, failureElapsedSeconds=time.monotonic() - began)
                return row

        concurrent_jobs(job, cfg, rows)
        report.update(summarize(rows, cfg, time.monotonic() - started))
        until = time.monotonic() + cfg["deliveryTimeoutSeconds"]
        while time.monotonic() < until and not all(pending[s.browser.owner]["ids"] <= s.terminal for s in streams):
            time.sleep(cfg["pollSeconds"])
        require(all(s.error is None and pending[s.browser.owner]["ids"] <= s.terminal and
                    s.seen <= pending[s.browser.owner]["ids"] for s in streams), "sse_delivery_or_isolation")
        checks["liveTerminalChangesAcrossSuppliedReplicas"] = True
        require(close_streams(streams, cfg["cleanupTimeoutSeconds"]), "stream_cancellation_failed")
        # A new owner is deliberately never assigned this run's records.
        foreign = make_client(cfg["endpoints"][0], cfg)
        owners.append(foreign)
        foreign.session()
        pending[foreign.owner] = {"ids": set(), "markers": set()}
        for owner in owners[:-1]:
            replica = make_client(cfg["endpoints"][-1], cfg, owner)
            require(replica.session() == owner.owner, "replica_auth_mismatch")
            reconnect = Stream(replica, cfg)
            streams.append(reconnect)
            reconnect.thread.start()
            require(reconnect.ready.wait(cfg["deliveryTimeoutSeconds"]), "reconnect_failed")
            notices = list(pages(replica, "/api/notifications", cfg["maxPages"]))
            for candidate in pending[owner.owner]["ids"]:
                require(sum(n.get("candidateId") == candidate for n in notices) == 1, "notice_catchup_failed")
                foreign.request("/api/candidates/" + candidate, expected=404)
                foreign.request("/api/candidates/" + candidate + "/result", expected=404)
            require(not list(pages(foreign, "/api/notifications", cfg["maxPages"])), "notice_owner_leak")
        checks["reconnectNoticeCatchupAndCrossOwnerDenial"] = True
        report["status"] = "passed" if report["thresholdsPassed"] else "failed"
    except (Exception, KeyboardInterrupt) as error:
        failures.append(str(error) if isinstance(error, CheckFailure) else "run_failed")
    finally:
        checks["clientStreamReadersCancelled"] = close_streams(streams, cfg["cleanupTimeoutSeconds"])
        failures.extend(cleanup([o for o in owners if o.owner in pending], pending, cfg))
        if not checks["clientStreamReadersCancelled"]:
            failures.append("stream_cancellation_failed")
        report.update(checks=checks, jobs=rows, cleanupPendingIds=sorted(i for p in pending.values() for i in p["ids"]))
        report["fixtureOwnerIds"] = list(pending)
        report["observedReconciledStreamConnections"] = sum(s.ready.is_set() for s in streams)
        report["observedTerminalStreamSignals"] = sum(len(s.terminal) for s in streams)
        if failures:
            report["status"] = "failed"
    return report


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0].lower() in ("help", "--help", "-h"):
        print(HELP)
        return 0
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            raise CheckFailure("invalid_arguments")

    parser = SafeParser(add_help=False, usage=HELP.splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--local-ollama-confirmed", action="store_true", required=True)
    descriptor = None
    try:
        args = parser.parse_args(argv)
        repo = Path(__file__).resolve().parents[2]
        source = private_path(args.config, repo, True)
        target = private_path(args.output, repo, False)
        raw = source.read_bytes()
        cfg = config(json.loads(raw, object_pairs_hook=unique_object))
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        report = run(cfg)
        report["configSha256"] = hashlib.sha256(raw).hexdigest()
        report["sourceSha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        report["flowSourceSha256"] = hashlib.sha256(Path(__file__).with_name("flow.py").read_bytes()).hexdigest()
        report["workload"] = cfg
        report["providerPrecondition"] = "Operator-confirmed local Ollama; no runtime discovery; provider checked only on completed results."
        with os.fdopen(descriptor, "w") as stream:
            descriptor = None
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print("Scoped local check " + report["status"] + "; private evidence written.")
        return 0 if report["status"] == "passed" else 1
    except Exception:
        print("Scoped local check rejected or failed; inspect private inputs and prerequisite state.", file=sys.stderr)
        return 1
    finally:
        if descriptor is not None:
            os.close(descriptor)


if __name__ == "__main__":
    sys.exit(main())
