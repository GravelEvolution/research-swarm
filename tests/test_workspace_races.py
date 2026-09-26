"""Conversation cancellation races against real engines and delayed boundary calls."""

import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path

from research_swarm.engine import Engine
from research_swarm.runner import ResearchRunner
from research_swarm.server import ResearchApplication
from research_swarm.workspace import WorkspaceApplication
from test_engine import sample_library


def wait_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Conversation did not reach the expected state")


class WorkspaceRaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = WorkspaceApplication(self.root / "missing-source", self.root / "workspace", import_existing=False)
        self.workspace.settings.update({"provider": {"clearKey": True}})
        self.task_id = self.workspace.post("/api/tasks", {})["task"]["id"]
        self.prefix = f"/api/tasks/{self.task_id}"
        self.workspace.post(self.prefix + "/messages", {"text": "OLD research question"})
        wait_until(lambda: not self.workspace.detail(self.task_id)["document"]["polishing"])
        self.releases = []
        self.threads = []

    def tearDown(self):
        for event in self.releases:
            event.set()
        for thread in self.threads:
            thread.join(timeout=4)
        self.workspace.close()
        self.temp.cleanup()

    def attach_engine(self, runner=None):
        app = ResearchApplication.__new__(ResearchApplication)
        app.state_dir = self.root / "runtime"
        app.state_dir.mkdir(exist_ok=True)
        app.settings = self.workspace.settings
        app.mutation_lock = self.workspace._mutation_lock
        app.operations = []
        app.operation_revision = 0
        app.operation_lock = threading.RLock()
        app.engine = Engine(sample_library(), self.root / "engine.sqlite",
                            runner=runner or ResearchRunner(None, self.root), workflow="autonomous", max_workers=2)
        self.workspace._apps[self.task_id] = app
        requirements = self.workspace._records[self.task_id]["compiled"]["requirements"]
        app.engine.command("start-autonomous", {"requirements": requirements})
        self.workspace._records[self.task_id]["phase"] = "researching"
        return app

    def completed_engine(self):
        app = self.attach_engine()
        wait_until(lambda: app.engine.snapshot()["status"] == "completed")
        self.assertEqual(self.workspace.detail(self.task_id)["phase"], "completed")
        return app

    def post_in_thread(self, suffix, payload):
        outcome = {}
        def execute():
            try:
                outcome["result"] = self.workspace.post(self.prefix + suffix, payload)
            except Exception as error:
                outcome["error"] = error
        thread = threading.Thread(target=execute)
        self.threads.append(thread)
        thread.start()
        return thread, outcome

    def new_document(self):
        self.workspace.post(self.prefix + "/messages", {"text": "NEW completely different question"})
        wait_until(lambda: not self.workspace.detail(self.task_id)["document"]["polishing"])
        detail = self.workspace.detail(self.task_id)
        self.assertEqual(detail["phase"], "requirements")
        self.assertIn("NEW completely different question", detail["document"]["markdown"])
        return detail

    def test_late_deepening_from_completed_task_cannot_restart_old_requirements(self):
        app = self.completed_engine()
        state = app.engine.snapshot()
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        incoming = copy.deepcopy(sample_library())
        incoming["papers"].append({"id": "99", "title": "Late retrieved paper"})
        def retrieve(payload, node=None):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("Test retrieval was not released")
            return {"library": incoming, "retrieval": {"resultPaperIds": ["99"]}}
        app._retrieve = retrieve
        thread, outcome = self.post_in_thread("/deepen", {
            "nodeId": "central", "text": "deepen OLD question", "query": "old topic",
            "expectedRevision": state["revision"],
        })
        self.assertTrue(entered.wait(2))
        self.new_document()
        release.set()
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(outcome.get("error"), ValueError)
        detail = self.workspace.detail(self.task_id)
        self.assertEqual(detail["phase"], "requirements")
        self.assertTrue(detail["state"]["paused"])
        self.assertNotIn("99", {paper["id"] for paper in detail["state"]["papers"]})
        self.assertFalse(any(item["type"] == "intervention" for item in detail["state"]["history"]))

    def test_late_success_response_cannot_overwrite_new_document_phase(self):
        app = self.completed_engine()
        revision = app.engine.snapshot()["revision"]
        committed, release = threading.Event(), threading.Event()
        self.releases.append(release)
        original_post = app.post
        def delayed_response(path, payload):
            result = original_post(path, payload)
            if path == "/api/deepen":
                committed.set()
                if not release.wait(4):
                    raise RuntimeError("Test response was not released")
            return result
        app.post = delayed_response
        thread, outcome = self.post_in_thread("/deepen", {
            "nodeId": "central", "text": "additional OLD question", "expectedRevision": revision,
        })
        self.assertTrue(committed.wait(2))
        self.new_document()
        release.set()
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", outcome)
        self.assertEqual(outcome["result"]["phase"], "requirements")
        self.assertEqual(self.workspace.detail(self.task_id)["phase"], "requirements")
        self.assertTrue(app.engine.snapshot()["paused"])

    def test_edit_in_failed_phase_invalidates_sibling_still_running(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        def runner(node, context, log):
            if node["id"] == "central" and node["phase"] == "plan":
                return {"summary": "Two independent checks", "evidenceIds": [], "claims": [], "structured": {},
                        "children": [{"title": title, "description": title, "acceptance": "return evidence", "kind": "evidence"}
                                     for title in ("fail first", "late sibling")]}
            if node["title"] == "fail first":
                raise RuntimeError("Intentional provider failure")
            entered.set()
            if not release.wait(4):
                raise RuntimeError("Test sibling was not released")
            return {"summary": "Late obsolete result", "evidenceIds": [], "claims": [], "structured": {}}
        app = self.attach_engine(runner)
        self.assertTrue(entered.wait(2))
        wait_until(lambda: self.workspace.detail(self.task_id)["phase"] == "failed")
        sibling = next(n for n in app.engine.snapshot()["nodes"] if n["title"] == "late sibling")
        self.assertEqual(sibling["status"], "running")
        self.new_document()
        current = next(n for n in app.engine.snapshot()["nodes"] if n["id"] == sibling["id"])
        self.assertTrue(app.engine.snapshot()["paused"])
        self.assertGreater(current["version"], sibling["version"])
        self.assertIsNone(current["output"])
        self.assertEqual(current["status"], "pending")
        release.set()


if __name__ == "__main__":
    unittest.main()
