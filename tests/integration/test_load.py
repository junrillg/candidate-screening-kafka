"""Small offline unit fixtures, not a workload profile or infrastructure evidence."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import load


def fixture():
    # Arbitrary unit-test inputs; the runner has no runtime workload defaults.
    return {"endpoints": ["http://127.0.0.1:1234"], "origin": "http://127.0.0.1:1235",
            "expectedProvider": "Ollama", "owners": 1, "jobs": 1, "concurrency": 1,
            "maxPages": 2, "maxFrameBytes": 128, "idleSeconds": .001,
            "requestTimeoutSeconds": .01, "streamReadTimeoutSeconds": .01,
            "terminalTimeoutSeconds": .1, "pollSeconds": .001,
            "deliveryTimeoutSeconds": .01, "cleanupTimeoutSeconds": .1,
            "thresholds": {"acceptanceP95Seconds": 1, "completionP95Seconds": 1,
                           "maxErrorRate": 0, "minCompletedFraction": 1}}


class LoadTests(unittest.TestCase):
    def test_help_precedes_access_and_ignores_trailing_arguments(self):
        with patch.object(Path, "read_bytes", side_effect=AssertionError("file access")), \
                patch.object(load, "run", side_effect=AssertionError("network")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, load.main(["--HELP", "ignored"]))
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(1, load.main(["--unknown", "sensitive-argument"]))
        self.assertNotIn("sensitive-argument", error.getvalue())

    def test_strict_config_and_local_endpoints(self):
        self.assertEqual(fixture(), load.config(fixture()))
        for mutate in [lambda c: c.update(unknown=True), lambda c: c.pop("jobs"),
                       lambda c: c.update(jobs=True), lambda c: c.update(concurrency=0),
                       lambda c: c.update(idleSeconds=float("nan")),
                       lambda c: c.update(expectedProvider="Bedrock"),
                       lambda c: c.update(cleanupTimeoutSeconds=.001),
                       lambda c: c["thresholds"].update(maxErrorRate=2),
                       lambda c: c.update(endpoints=["http://example.com"]),
                       lambda c: c.update(endpoints=["http://127.0.0.1:1234@outside.invalid"]),
                       lambda c: c.update(endpoints=["http://127.0.0.1:1234/a"]),
                       lambda c: c.update(endpoints=["http://localhost:1234"]),
                       lambda c: c.update(endpoints=[c["endpoints"][0]] * 2)]:
            value = copy.deepcopy(fixture())
            mutate(value)
            with self.assertRaises(load.CheckFailure):
                load.config(value)
        with self.assertRaises(load.CheckFailure):
            json.loads('{"jobs":1,"jobs":2}', object_pairs_hook=load.unique_object)
        with self.assertRaises(load.CheckFailure):
            load.NoRedirect().redirect_request(None, None, 302, "", {}, "https://outside.invalid")

    def test_private_paths_and_output_mode_without_infrastructure(self):
        repo = Path(load.__file__).resolve().parents[2]
        with self.assertRaises(load.CheckFailure):
            load.private_path(repo / "unwritten-private-output.json", repo, False)
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            source, target = directory / "input.json", directory / "result.json"
            source.write_text(json.dumps(fixture()))
            os.chmod(source, 0o600)
            self.assertEqual(source, load.private_path(source, repo, True))
            link = directory / "link.json"
            link.symlink_to(source)
            with self.assertRaises(load.CheckFailure):
                load.private_path(link, repo, True)
            os.chmod(source, 0o644)
            with self.assertRaises(load.CheckFailure):
                load.private_path(source, repo, True)
            os.chmod(source, 0o600)
            with patch.object(load, "run", return_value={"status": "failed"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1, load.main(["--config", str(source), "--output", str(target),
                                               "--local-ollama-confirmed"]))
            self.assertEqual(0o600, stat.S_IMODE(target.stat().st_mode))
            with self.assertRaises(load.CheckFailure):
                load.private_path(target, repo, False)

    def test_percentile_missing_samples_and_claim_limits(self):
        self.assertEqual(3, load.percentile([3, 1, 2], .95))
        self.assertIsNone(load.percentile([], .95))
        failed = load.summarize([{"error": "job_failed"}], fixture(), 1)
        self.assertFalse(failed["thresholdsPassed"])
        passed = load.summarize([{"acceptanceSeconds": .01, "completionSeconds": .1,
                                  "terminal": "Completed", "completed": True}], fixture(), 1)
        self.assertTrue(passed["thresholdsPassed"])
        self.assertEqual(1, passed["metrics"]["elapsedSeconds"])
        self.assertFalse(passed["claims"]["fullCapacityProven"])
        self.assertFalse(passed["claims"]["providerInferenceThroughputMeasured"])
        self.assertFalse(passed["claims"]["crossReplicaAuthScopeAvailable"])

    def test_interrupt_cancels_queued_jobs_before_cleanup(self):
        class InterruptedFuture:
            def result(self):
                raise KeyboardInterrupt()

        class Pool:
            shutdown_args = None

            def submit(self, job, index):
                return InterruptedFuture()

            def shutdown(self, **kwargs):
                self.shutdown_args = kwargs

        pool = Pool()
        with patch.object(load.concurrent.futures, "ThreadPoolExecutor", return_value=pool):
            with self.assertRaises(KeyboardInterrupt):
                load.concurrent_jobs(lambda index: None, fixture(), [])
        self.assertEqual({"wait": True, "cancel_futures": True}, pool.shutdown_args)

    def test_failure_after_acceptance_cleans_only_fixture_records(self):
        records, deleted = {}, []
        lose_response = False

        class FakeBrowser:
            def __init__(self, owner=None):
                self.owner = owner or str(uuid.uuid4())

            def session(self):
                return self.owner

            def request(self, path, method="GET", body=None, expected=200, headers=None):
                if path == "/api/candidates" and method == "POST":
                    candidate = str(uuid.uuid4())
                    records[candidate] = {"id": candidate, "owner": self.owner, "displayName": body["displayName"]}
                    if lose_response:
                        raise OSError("Ambiguous response after actual fixture commit")
                    return {"id": candidate}
                if path == "/api/candidates":
                    return {"items": [r for r in records.values() if r["owner"] == self.owner], "nextCursor": None}
                if path == "/api/notifications":
                    return {"items": [{"candidateId": r["id"]} for r in records.values() if r["owner"] == self.owner], "nextCursor": None}
                if path == "/api/session":
                    return None
                candidate = path.split("/")[3]
                if candidate not in records or records[candidate]["owner"] != self.owner:
                    assert expected == 404
                    return None
                if method == "DELETE":
                    deleted.append(candidate)
                    del records[candidate]
                    return None
                if path.endswith("/result"):
                    raise ValueError("Sensitive synthetic narrative must not appear in diagnostics")
                return {"status": "Completed"}

        class FakeThread:
            ident = None
            alive = False

            def start(self):
                self.ident, self.alive = 1, True

            def is_alive(self):
                return self.alive

            def join(self, timeout):
                self.alive = False

        class FakeStream:
            def __init__(self, browser, cfg):
                self.browser, self.error = browser, None
                self.stop, self.ready = threading.Event(), threading.Event()
                self.ready.set()
                self.thread = FakeThread()
                self.seen = set()

            @property
            def terminal(self):
                return set(records)

        with patch.object(load, "Stream", FakeStream):
            result = load.run(fixture(), lambda endpoint, cfg, source=None: FakeBrowser(source.owner if source else None))
        self.assertEqual("failed", result["status"])
        self.assertTrue(deleted)
        self.assertFalse(records)
        self.assertFalse(result["cleanupPendingIds"])
        self.assertNotIn("Sensitive", json.dumps(result))
        self.assertTrue(result["checks"]["clientStreamReadersCancelled"])
        deleted.clear()
        lose_response = True
        with patch.object(load, "Stream", FakeStream):
            result = load.run(fixture(), lambda endpoint, cfg, source=None: FakeBrowser(source.owner if source else None))
        self.assertEqual("failed", result["status"])
        self.assertTrue(deleted, "Ambiguous acceptance must be discovered by the fixture-owned marker")
        self.assertFalse(records)
        self.assertFalse(result["cleanupPendingIds"])


if __name__ == "__main__":
    unittest.main()
