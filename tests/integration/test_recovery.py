"""Offline failure/ownership checks; no infrastructure or model throughput evidence."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import recovery


def state(name, running=True, paused=False):
    return {"id": name, "image": "sha256:fixture", "running": running, "paused": paused}


class RecoveryTests(unittest.TestCase):
    def test_help_and_invalid_arguments_never_touch_infrastructure(self):
        with patch.object(recovery, "Run", side_effect=AssertionError("preflight")), \
                patch.object(recovery, "private_output", side_effect=AssertionError("filesystem")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, recovery.main(["--HELP", "ignored"]))
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(1, recovery.main(["--unknown", "sensitive-argument"]))
        self.assertNotIn("sensitive-argument", error.getvalue())

    def test_failure_restores_only_owned_baseline_services_in_order(self):
        run = recovery.Run(Path("unused"))
        run.baseline = {name: state(name) for name in recovery.SERVICES}
        run.baseline["candidates"]["running"] = False
        run.baseline["otel-collector"]["paused"] = True
        run.changed = set(recovery.SERVICES) - {"postgres"}
        run.paused = {"ollama"}
        run.temporary = {"own-worker"}
        calls = []

        def operation(*args):
            calls.append(args)
            # Simulate a response failure after the stop request reached the daemon.
            if args == ("stop", "screening"):
                raise OSError("private daemon detail")
            return ""

        def remove(name):
            calls.append(("remove", name))
            run.temporary.remove(name)

        with patch.object(recovery, "snapshot", side_effect=lambda name: state(name)), \
                patch.object(recovery, "compose", side_effect=operation), patch.object(run, "remove", side_effect=remove):
            with self.assertRaises(OSError):
                run.service("stop", "screening")
            self.assertIn("screening", run.changed)
            calls.clear()
            self.assertEqual([], run.restore())
        self.assertEqual([("remove", "own-worker"), ("unpause", "ollama"),
                          ("start", "kafka"), ("start", "ollama"), ("start", "screening")], calls)

    def test_unowned_or_uncleared_temporary_container_blocks_conflicting_restore(self):
        run = recovery.Run(Path("unused"))
        with patch.object(recovery, "docker", return_value="existing") as operation:
            with self.assertRaises(recovery.CheckFailure):
                run.create("preexisting", "image")
        self.assertFalse(run.temporary)
        self.assertEqual(1, operation.call_count)
        run.temporary = {"own-worker"}
        run.baseline = {"screening": state("screening")}
        run.changed = {"screening"}
        with patch.object(recovery, "docker", side_effect=["existing", "another-run"]) as operation:
            with self.assertRaises(recovery.CheckFailure):
                run.remove("own-worker")
        self.assertFalse(any(call.args[:2] == ("rm", "-f") for call in operation.call_args_list))
        with patch.object(run, "remove", side_effect=recovery.CheckFailure("identity")), \
                patch.object(recovery, "compose") as compose:
            self.assertEqual(["temporary_cleanup", "service_restore"], run.restore())
        compose.assert_not_called()

    def test_ambiguous_acceptance_and_later_failure_clean_only_fresh_owners(self):
        candidate, unrelated = str(uuid.uuid4()), str(uuid.uuid4())
        calls = []

        class Browser:
            owner = str(uuid.uuid4())
            api = recovery.API

            def request(self, path, method="GET", *args, **kwargs):
                calls.append((path, method))
                if method == "POST":
                    raise OSError("lost response after durable commit")
                if path == "/api/candidates":
                    return {"items": [{"id": candidate}], "nextCursor": None}
                return None

        run = recovery.Run(Path("unused"))
        browser = Browser()
        run.browsers = [browser]
        with patch.object(run, "save"), patch.object(recovery, "cleared", return_value=True):
            with self.assertRaises(OSError):
                recovery.submit(run, browser)
            run.clean_records()
        self.assertEqual({candidate}, set(run.records))
        self.assertIn(("/api/candidates/" + candidate, "DELETE"), calls)
        self.assertNotIn(("/api/candidates/" + unrelated, "DELETE"), calls)
        # A failure cleaning one owned record does not skip independent owned records.
        calls.clear()
        second = str(uuid.uuid4())
        run.records[second] = browser
        with patch.object(run, "save"), patch.object(recovery, "cleared", side_effect=[OSError("private"), True]), \
                patch.object(recovery, "eventually", side_effect=lambda check, **kwargs: check()):
            with self.assertRaises(recovery.CheckFailure):
                run.clean_records()
        self.assertIn(("/api/candidates/" + second, "DELETE"), calls)

    def test_case_failure_runs_final_cleanup_and_persistence_failure_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            run = recovery.Run(Path(directory) / "evidence.json")
            candidate = str(uuid.uuid4())

            def fail(fixture):
                fixture.track(object(), {"id": candidate})
                raise ValueError("sensitive narrative")

            with patch.object(run, "preflight"), patch.object(run, "cleanup", return_value=True) as cleanup, \
                    patch.object(recovery, "retention", fail):
                self.assertFalse(recovery.execute(run))
            cleanup.assert_called_once()
            evidence = json.loads(run.output.read_text())
            self.assertEqual([candidate], evidence["ids"])
            self.assertEqual("failed", evidence["status"])
            self.assertNotIn("sensitive", json.dumps(evidence))
            with patch.object(run, "preflight", side_effect=OSError("private")), \
                    patch.object(run, "cleanup", return_value=True) as cleanup, \
                    patch.object(run, "save", side_effect=OSError("disk full")):
                self.assertFalse(recovery.execute(run))
            cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
