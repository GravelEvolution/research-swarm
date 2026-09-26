"""Integration tests for durable scheduling; runners are injected boundary doubles."""

import copy
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from research_swarm.engine import Engine


def sample_library():
    papers = []
    evidence = []
    for number, facet in ((1, "10"), (2, "20")):
        papers.append({
            "id": str(number), "title": f"论文 {number}", "abstract": "可核对摘要",
            "year": 2025, "venue": "Test", "doi": "", "authors": [],
            "codeUrl": "", "pdfAvailable": False, "pdfPath": None,
            "score": 60, "scores": {key: 60 for key in (
                "novelty", "relevance", "impact", "reproducibility", "urgency")},
            "reason": "摘要相关", "reproducibility": "没有实验产物",
            "workerStatus": "pending", "facetNodeIds": [facet],
            "evidenceIds": [str(100 + number)], "facts": [], "scoreBasis": [],
            "feedback": None,
        })
        evidence.append({"id": str(100 + number), "paperId": str(number),
                         "quote": "摘要中的明确陈述", "locator": "abstract",
                         "type": "abstract", "confidence": 0.7, "extractor": "fixture"})
    return {
        "sourcePath": "read-only-source", "sourceDb": "source.sqlite",
        "topic": {"id": "topic", "title": "科研课题", "description": "比较两种方法",
                  "successCriteria": "每个结论都提供证据和局限"},
        "papers": papers, "evidence": evidence, "relations": [], "stats": {},
        "facetNodes": [
            {"id": "10", "parentId": None, "title": "方法甲", "facetId": "1",
             "facetName": "方法", "topology": "tree", "paperIds": ["1"]},
            {"id": "11", "parentId": "10", "title": "甲的子切面", "facetId": "1",
             "facetName": "方法", "topology": "tree", "paperIds": ["1"]},
            {"id": "20", "parentId": None, "title": "方法乙", "facetId": "1",
             "facetName": "方法", "topology": "tree", "paperIds": ["2"]},
        ],
    }


class ResearchRunner:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.block = None
        self.entered = threading.Event()
        self.fail_once = False
        self.active = 0
        self.peak = 0

    def __call__(self, node, context, log):
        with self.lock:
            self.calls.append((copy.deepcopy(node), copy.deepcopy(context)))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            log("真实调度调用已进入测试执行器")
            if node["phase"] == "plan":
                if node["id"] == "central":
                    children = [{"title": title, "description": f"核对{title}",
                                 "acceptance": "附摘要证据", "constraints": "不推断全文",
                                 "kind": "research", "sourceNodeId": source,
                                 "paperIds": [paper]}
                                for title, source, paper in (("方法甲", "10", "1"),
                                                             ("方法乙", "20", "2"))]
                else:
                    children = [{"title": node["title"] + "证据", "description": "整理原始证据",
                                 "acceptance": "证据有定位", "constraints": "只使用给定资料",
                                 "kind": "evidence", "paperIds": node["input"].get("paperIds", [])}]
                return {"summary": "候选任务分解", "evidenceIds": [], "claims": [],
                        "structured": {}, "children": children}
            if node["phase"] == "execute":
                if self.block is not None:
                    gate = self.block
                    self.entered.set()
                    if not gate.wait(5):
                        raise RuntimeError("test gate timeout")
                with self.lock:
                    fail = self.fail_once
                    self.fail_once = False
                if fail:
                    raise RuntimeError("可重试的测试失败")
                ids = ["101"] if node["sourceNodeId"] == "10" else ["102"]
                return {"summary": "已核对摘要", "evidenceIds": ids,
                        "claims": [{"id": node["id"] + ":claim", "text": "摘要支持的候选判断",
                                    "evidenceIds": ids, "status": "candidate"}],
                        "structured": {"basis": "abstract"}, "unresolved": ["尚未运行实验"]}
            assert context["children"], "aggregation must have children"
            assert all(child["status"] == "completed" for child in context["children"])
            outputs = [child["output"] for child in context["children"]]
            return {"summary": "合并证据后的候选对比", "evidenceIds": sorted({
                        item for output in outputs for item in output["evidenceIds"]}),
                    "claims": [claim for output in outputs for claim in output["claims"]],
                    "structured": {"childCount": len(outputs)}, "unresolved": ["尚未运行实验"]}
        finally:
            with self.lock:
                self.active -= 1


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite"
        self.runner = ResearchRunner()
        self.engines = []
        self.engine = self.make_engine()

    def tearDown(self):
        if self.runner.block is not None:
            self.runner.block.set()
        for engine in self.engines:
            engine.close()
        self.temp.cleanup()

    def make_engine(self, runner=None, path=None, workers=2, workflow="gated"):
        engine = Engine(sample_library(), path or self.path,
                        runner=runner or self.runner, max_workers=workers, workflow=workflow)
        self.engines.append(engine)
        return engine

    def wait(self, predicate, engine=None, timeout=4):
        engine = engine or self.engine
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = engine.snapshot()
            if predicate(state):
                return state
            time.sleep(0.01)
        self.fail("调度未到达预期状态: " + json.dumps(engine.snapshot(), ensure_ascii=False)[-2500:])

    def confirm(self, engine=None):
        engine = engine or self.engine
        return engine.command("checkpoint", {"id": engine.snapshot()["activeCheckpointId"],
                                              "decision": "confirm"})

    def start(self):
        self.confirm()
        self.confirm()

    def comparison(self):
        self.start()
        return self.wait(lambda state: state["stage"] == 7)

    def test_initial_agents_and_four_checkpoints_cannot_be_bypassed(self):
        state = self.engine.snapshot()
        self.assertEqual({node["sourceNodeId"] for node in state["nodes"]
                          if node["sourceNodeId"]}, {"10", "11", "20"})
        self.assertEqual(state["stage"], 0)
        self.assertEqual(state["status"], "waiting_user")
        state["nodes"].clear()
        self.assertEqual(len(self.engine.snapshot()["nodes"]), 4)
        for stage in (0, 3):
            resumed = self.engine.command("resume", {})
            self.assertEqual(resumed["stage"], stage)
            self.assertEqual(resumed["status"], "waiting_user")
            self.assertTrue(resumed["paused"])
            self.assertEqual(len(self.runner.calls), 0)
            self.confirm()
        state = self.wait(lambda state: state["stage"] == 7)
        self.assertFalse(state["report"]["approved"])
        for stage in (7, 8):
            resumed = self.engine.command("resume", {})
            self.assertEqual(resumed["stage"], stage)
            self.assertEqual(resumed["status"], "waiting_user")
            self.assertFalse(resumed["report"]["approved"])
            self.confirm()
        final = self.engine.snapshot()
        self.assertEqual(final["status"], "completed")
        self.assertTrue(final["report"]["approved"])
        self.assertTrue(all(c["status"] == "confirmed" for c in final["checkpoints"]))

    def test_requirement_validation_edit_versions_and_confirmed_edit_guard(self):
        requirements = self.engine.snapshot()["requirements"]
        requirements[0]["acceptance"] = ""
        with self.assertRaises(ValueError):
            self.engine.command("requirements", {"requirements": requirements})
        requirements[0]["acceptance"] = "可逐条复核"
        requirements[0]["description"] = "更新的研究需求"
        saved = self.engine.command("requirements", {"requirements": requirements})
        self.assertEqual(saved["requirements"][0]["version"], 2)
        self.assertEqual(next(node for node in saved["nodes"] if node["id"] == "central")["input"]["description"], "更新的研究需求")
        self.confirm()
        with self.assertRaises(ValueError):
            self.engine.command("requirements", {"requirements": requirements})

    def test_children_finish_before_aggregation_and_all_outputs_are_persisted(self):
        state = self.comparison()
        self.assertEqual(state["report"]["claims"][0]["status"], "candidate")
        active = [node for node in state["nodes"] if node["active"]]
        self.assertEqual(len(active), 5)
        self.assertTrue(all(node["status"] == "completed" for node in active))
        self.assertTrue(all(node["logs"] for node in active))
        self.assertLessEqual(self.runner.peak, 2)
        for node, context in self.runner.calls:
            if node["phase"] == "aggregate":
                self.assertTrue(context["children"])
                self.assertTrue(all(child["output"] for child in context["children"]))
        self.engine.close()
        reopened = self.make_engine()
        persisted = reopened.snapshot()
        self.assertEqual(persisted["report"], state["report"])
        self.assertEqual(persisted["activeCheckpointId"], state["activeCheckpointId"])
        self.assertTrue(persisted["paused"])
        connection = sqlite3.connect(self.path)
        try:
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM node_outputs").fetchone()[0], 0)
        finally:
            connection.close()

    def test_pause_discards_late_results_and_resume_reexecutes(self):
        gate = threading.Event()
        self.runner.block = gate
        self.start()
        self.assertTrue(self.runner.entered.wait(2))
        paused = self.engine.command("pause", {})
        paused_version = paused["revision"]
        self.runner.block = None
        gate.set()
        time.sleep(0.08)
        state = self.engine.snapshot()
        self.assertTrue(state["paused"])
        self.assertEqual(state["revision"], paused_version)
        self.assertFalse(any(n["status"] == "running" for n in state["nodes"]))
        self.assertFalse(state["report"]["claims"])
        self.engine.command("resume", {})
        self.wait(lambda state: state["stage"] == 7)

    def test_selected_intervention_reruns_branch_and_consumers_but_not_sibling(self):
        before = self.comparison()
        alpha = next(n for n in before["nodes"] if n["sourceNodeId"] == "10" and n["kind"] == "research")
        beta = next(n for n in before["nodes"] if n["sourceNodeId"] == "20" and n["kind"] == "research")
        beta_children = [n for n in before["nodes"] if n["parentId"] == beta["id"]]
        impact = self.engine.command("impact", {"nodeId": alpha["id"], "kind": "modify"})
        self.assertIn("central", impact["affectedIds"])
        self.assertEqual(impact["downstreamCount"], 2)
        self.assertNotIn(beta["id"], impact["affectedIds"])
        with self.assertRaises(ValueError):
            self.engine.command("intervene", {"nodeId": alpha["id"], "kind": "modify",
                                               "text": "新要求", "expectedRevision": impact["revision"] - 1})
        self.engine.command("intervene", {"nodeId": alpha["id"], "kind": "modify",
                                           "text": "追加甲的证据校验", "acceptance": "定位完备",
                                           "expectedRevision": impact["revision"]})
        state = self.wait(lambda state: state["stage"] == 7 and state["activeCheckpointId"] != before["activeCheckpointId"])
        for old in [beta] + beta_children:
            current = next(n for n in state["nodes"] if n["id"] == old["id"])
            self.assertEqual(current["version"], old["version"])
            self.assertEqual(current["output"], old["output"])
        latest_alpha = next(node for node, _ in reversed(self.runner.calls)
                            if node["sourceNodeId"] == "10" and node["phase"] == "execute")
        self.assertEqual(latest_alpha["input"]["parentDemand"]["description"], "追加甲的证据校验")
        self.assertFalse(state["report"]["approved"])

    def test_rollback_restores_checkpoint_and_supersedes_later_decisions(self):
        before = self.comparison()
        first = before["checkpoints"][0]["id"]
        revision = before["revision"]
        restored = self.engine.command("rollback", {"checkpointId": first})
        self.assertGreater(restored["revision"], revision)
        self.assertEqual(restored["stage"], 0)
        self.assertEqual(restored["activeCheckpointId"], first)
        self.assertTrue(restored["paused"])
        self.assertTrue(any(c["status"] == "superseded" for c in restored["checkpoints"]))
        self.assertGreater(len(restored["activities"]), len(before["activities"]))
        self.engine.command("resume", {})
        self.assertEqual(self.engine.snapshot()["stage"], 0)

    def test_restart_recovers_inflight_as_pending_and_does_not_auto_run(self):
        gate = threading.Event()
        self.runner.block = gate
        self.start()
        self.assertTrue(self.runner.entered.wait(2))
        self.engine.close()
        self.runner.block = None
        fresh = ResearchRunner()
        reopened = self.make_engine(runner=fresh)
        state = reopened.snapshot()
        self.assertTrue(state["paused"])
        self.assertFalse(any(n["status"] == "running" for n in state["nodes"]))
        self.assertEqual(fresh.calls, [])
        gate.set()
        reopened.command("resume", {})
        self.wait(lambda state: state["stage"] == 7, reopened)

    def test_failed_task_retry_finishes_without_replanning_completed_sibling(self):
        self.runner.fail_once = True
        self.start()
        state = self.wait(lambda state: any(n["status"] == "failed" for n in state["nodes"]))
        failed = next(n for n in state["nodes"] if n["status"] == "failed")
        self.assertTrue(any("可重试" in log["message"] for log in failed["logs"]))
        self.engine.command("retry", {"nodeId": failed["id"]})
        self.wait(lambda state: state["stage"] == 7)

    def test_feedback_and_next_round_are_durable_and_reopen_requirements(self):
        self.engine.command("feedback", {"paperId": "1", "value": "interested"})
        self.comparison()
        self.confirm()
        self.confirm()
        state = self.engine.command("next-round", {})
        self.assertEqual(state["project"]["round"], 2)
        self.assertEqual(state["stage"], 0)
        self.assertFalse(state["report"]["approved"])
        self.assertEqual(state["papers"][0]["feedback"], "interested")
        self.assertTrue(state["history"])
        self.assertEqual(state["status"], "waiting_user")

    def test_duplicate_confirmation_is_serialized(self):
        checkpoint = self.engine.snapshot()["activeCheckpointId"]
        barrier = threading.Barrier(3)
        results = []

        def confirm_once():
            barrier.wait()
            try:
                self.engine.command("checkpoint", {"id": checkpoint, "decision": "confirm"})
                results.append("confirmed")
            except ValueError:
                results.append("rejected")

        threads = [threading.Thread(target=confirm_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(2)
        self.assertCountEqual(results, ["confirmed", "rejected"])
        self.assertEqual(self.engine.snapshot()["stage"], 3)

    def test_invalid_evidence_is_failed_and_unsupported_claim_is_explicit(self):
        def unsafe(node, context, log):
            return {"summary": "无来源判断", "evidenceIds": ["missing"],
                    "claims": [], "structured": {}}

        bad = self.make_engine(runner=unsafe, path=Path(self.temp.name) / "bad.sqlite")
        self.confirm(bad)
        self.confirm(bad)
        state = self.wait(lambda state: state["status"] == "failed", bad)
        self.assertEqual(next(n for n in state["nodes"] if n["id"] == "central")["status"], "failed")

        def unsupported(node, context, log):
            return {"summary": "暂定观察", "evidenceIds": [],
                    "claims": [{"id": "one", "text": "待验证判断", "evidenceIds": [],
                                "status": "confirmed"}], "structured": {}}

        unsupported_engine = self.make_engine(runner=unsupported, path=Path(self.temp.name) / "empty.sqlite")
        self.confirm(unsupported_engine)
        self.confirm(unsupported_engine)
        state = self.wait(lambda state: state["stage"] == 7, unsupported_engine)
        claim = state["report"]["claims"][0]
        self.assertEqual(claim["status"], "candidate")
        self.assertIn("无证据", claim["limitations"])

    def test_decomposition_limit_fails_atomically(self):
        def runaway(node, context, log):
            children = [{"title": f"任务 {index}", "description": "检查", "acceptance": "定位",
                         "constraints": "", "kind": "evidence"} for index in range(81)]
            return {"summary": "分解", "evidenceIds": [], "claims": [],
                    "structured": {}, "children": children}

        engine = self.make_engine(runner=runaway, path=Path(self.temp.name) / "limit.sqlite")
        self.confirm(engine)
        self.confirm(engine)
        state = self.wait(lambda state: state["status"] == "failed", engine)
        self.assertEqual(len(state["nodes"]), 4)
        self.assertTrue(any("80" in activity["message"] for activity in state["activities"]))

    def test_deepening_keeps_existing_children_and_atomically_adds_library_material(self):
        before = self.comparison()
        alpha = next(node for node in before["nodes"] if node["id"] == "facet:10")
        old_child = next(node for node in before["nodes"] if node["parentId"] == alpha["id"])
        imported = sample_library()
        new_paper = copy.deepcopy(imported["papers"][0])
        new_paper.update(id="3", title="新检索论文", evidenceIds=[], facetNodeIds=["30"])
        imported["papers"].append(new_paper)
        imported["facetNodes"].append({"id": "30", "parentId": None, "title": "新增切面",
                                       "facetId": "2", "facetName": "新增", "topology": "tree", "paperIds": ["3"]})
        impact = self.engine.command("impact", {"nodeId": alpha["id"], "kind": "deepen"})
        self.assertEqual(set(impact["affectedIds"]), {"central", alpha["id"]})
        with self.assertRaises(ValueError):
            self.engine.command("intervene", {"nodeId": alpha["id"], "kind": "deepen", "text": "继续核对",
                                               "library": imported, "expectedRevision": impact["revision"] - 1})
        self.assertNotIn("3", {paper["id"] for paper in self.engine.snapshot()["papers"]})
        self.engine.command("intervene", {"nodeId": alpha["id"], "kind": "deepen", "text": "继续核对",
                                           "library": imported, "expectedRevision": impact["revision"]})
        after = self.wait(lambda state: state["stage"] == 7 and state["activeCheckpointId"] != before["activeCheckpointId"])
        preserved = next(node for node in after["nodes"] if node["id"] == old_child["id"])
        self.assertEqual(preserved["output"], old_child["output"])
        self.assertEqual(preserved["version"], old_child["version"])
        self.assertIn("3", {paper["id"] for paper in after["papers"]})
        self.assertTrue(any(node["id"] == "facet:30" for node in after["nodes"]))
        self.assertFalse(after["report"]["approved"])

    def test_changed_global_scope_reopens_requirements_then_replans_scope(self):
        before = self.comparison()
        requirements = copy.deepcopy(before["requirements"])
        requirements[0].update(description="只研究乙", sourceNodeIds=["20"])
        impact = self.engine.command("impact", {"nodeId": "central", "kind": "modify"})
        after = self.engine.command("intervene", {"nodeId": "central", "kind": "modify", "text": "只研究乙",
                                                   "requirements": requirements, "expectedRevision": impact["revision"]})
        self.assertEqual(after["stage"], 0)
        self.assertEqual(after["requirements"][0]["sourceNodeIds"], ["20"])
        self.assertEqual(next(node for node in after["nodes"] if node["id"] == "central")["phase"], "plan")
        self.assertFalse(any(node["active"] and node["id"] != "central" for node in after["nodes"]))
        self.engine.command("resume", {})
        self.assertEqual(self.engine.snapshot()["stage"], 0)
        self.confirm()
        self.confirm()
        self.wait(lambda state: state["stage"] == 7)
        root_plans = [(node, context) for node, context in self.runner.calls if node["id"] == "central" and node["phase"] == "plan"]
        self.assertEqual(len(root_plans), 2)
        self.assertEqual(root_plans[-1][1]["requirements"][0]["sourceNodeIds"], ["20"])

    def test_refresh_preserves_feedback_and_passes_current_metadata_to_runner_after_restart(self):
        self.engine.command("feedback", {"paperId": "1", "value": "read_later"})
        imported = sample_library()
        imported["relations"] = [{"source": "10", "target": "20", "type": "updated"}]
        imported["stats"] = {"paperCount": 2, "importRevision": 2}
        state = self.engine.command("refresh-library", {"library": imported})
        self.assertEqual(state["papers"][0]["feedback"], "read_later")
        self.engine.close()
        reopened = self.make_engine()
        self.confirm(reopened)
        self.confirm(reopened)
        self.wait(lambda state: state["stage"] == 7, reopened)
        root_context = next(context for node, context in self.runner.calls if node["id"] == "central")
        self.assertEqual(root_context["library"]["relations"], imported["relations"])
        self.assertEqual(root_context["library"]["stats"]["importRevision"], 2)

    def test_rejected_branch_stays_visible_to_aggregation_without_reexecution(self):
        before = self.comparison()
        impact = self.engine.command("impact", {"nodeId": "facet:10", "kind": "reject"})
        self.engine.command("intervene", {"nodeId": "facet:10", "kind": "reject", "text": "该方法不满足约束",
                                           "expectedRevision": impact["revision"]})
        after = self.wait(lambda state: state["stage"] == 7 and state["activeCheckpointId"] != before["activeCheckpointId"])
        root_contexts = [context for node, context in self.runner.calls if node["id"] == "central" and node["phase"] == "aggregate"]
        rejected = next(child for child in root_contexts[-1]["children"] if child["id"] == "facet:10")
        self.assertTrue(rejected["output"]["structured"]["rejected"])
        self.assertEqual(rejected["status"], "completed")
        self.assertFalse(after["report"]["approved"])

    def test_rollback_discards_inflight_outputs_and_generated_evidence(self):
        entered = threading.Event()
        gate = threading.Event()

        def generated(node, context, log):
            if node["phase"] == "plan":
                return {"summary": "实验任务", "evidenceIds": [], "claims": [], "structured": {},
                        "children": [{"title": "统计", "description": "执行统计", "acceptance": "原始数据可查",
                                      "constraints": "", "kind": "experiment", "experiment": {"operation": "mean"}}]}
            if node["phase"] == "execute":
                entered.set()
                gate.wait(3)
                return {"summary": "统计完成", "evidenceIds": ["experiment:unit:sha"], "claims": [], "structured": {},
                        "generatedEvidence": [{"id": "experiment:unit:sha", "type": "experiment", "paperId": "",
                                               "quote": "均值 2", "locator": "artifact.json", "confidence": 1,
                                               "extractor": "experiment_statistics"}]}
            return {"summary": "汇总", "evidenceIds": [], "claims": [], "structured": {}}

        engine = self.make_engine(runner=generated, path=Path(self.temp.name) / "stale-evidence.sqlite")
        checkpoint = engine.snapshot()["activeCheckpointId"]
        self.confirm(engine)
        self.confirm(engine)
        self.assertTrue(entered.wait(2))
        state = engine.command("rollback", {"checkpointId": checkpoint})
        gate.set()
        time.sleep(0.05)
        self.assertEqual(engine.snapshot()["revision"], state["revision"])
        self.assertNotIn("experiment:unit:sha", {item["id"] for item in engine.snapshot()["evidence"]})

    def test_mode_change_is_blocked_until_pause_and_closes_old_execution_epoch(self):
        gate = threading.Event()
        self.runner.block = gate
        self.start()
        self.assertTrue(self.runner.entered.wait(2))
        with self.assertRaises(ValueError):
            self.engine.command("mode", {"mode": "llm"})
        self.engine.command("pause", {})
        state = self.engine.command("mode", {"mode": "llm"})
        self.runner.block = None
        gate.set()
        time.sleep(0.05)
        self.assertEqual(self.engine.snapshot()["revision"], state["revision"])
        self.engine.command("resume", {})
        self.wait(lambda state: state["stage"] == 7)
        self.assertTrue(any(context["mode"] == "llm" for _, context in self.runner.calls))

    def test_deepening_after_final_approval_starts_new_work_without_manual_resume(self):
        self.comparison()
        self.confirm()
        self.confirm()
        impact = self.engine.command("impact", {"nodeId": "facet:10", "kind": "deepen"})
        state = self.engine.command("intervene", {"nodeId": "facet:10", "kind": "deepen",
                                                   "text": "深入核对实验限制", "expectedRevision": impact["revision"]})
        self.assertFalse(state["paused"])
        self.assertFalse(state["report"]["approved"])
        self.wait(lambda state: state["stage"] == 7)

    def test_paper_worker_status_tracks_real_active_leaf_work(self):
        gate = threading.Event()
        self.runner.block = gate
        self.start()
        self.assertTrue(self.runner.entered.wait(2))
        state = self.wait(lambda state: sum(node["status"] == "running" for node in state["nodes"]) == 2)
        self.assertEqual({paper["workerStatus"] for paper in state["papers"]}, {"running"})
        self.runner.block = None
        gate.set()
        state = self.wait(lambda state: state["stage"] == 7)
        self.assertEqual({paper["workerStatus"] for paper in state["papers"]}, {"completed"})

    def test_explicit_pause_at_checkpoint_survives_deepening(self):
        self.comparison()
        self.engine.command("pause", {})
        impact = self.engine.command("impact", {"nodeId": "facet:10", "kind": "deepen"})
        state = self.engine.command("intervene", {"nodeId": "facet:10", "kind": "deepen", "text": "深入核对",
                                                   "expectedRevision": impact["revision"]})
        self.assertTrue(state["paused"])
        self.assertEqual(state["status"], "idle")
        self.engine.command("resume", {})
        self.wait(lambda state: state["stage"] == 7)

    def test_inflight_intervention_does_not_discard_unrelated_running_sibling(self):
        gate = threading.Event()
        self.runner.block = gate
        self.start()
        self.assertTrue(self.runner.entered.wait(2))
        state = self.wait(lambda state: sum(node["status"] == "running" for node in state["nodes"]) == 2)
        beta = next(node for node in state["nodes"] if node["sourceNodeId"] == "20" and node["kind"] == "evidence")
        impact = self.engine.command("impact", {"nodeId": "facet:10", "kind": "modify"})
        self.engine.command("intervene", {"nodeId": "facet:10", "kind": "modify", "text": "核对甲的新约束",
                                           "expectedRevision": impact["revision"]})
        self.runner.block = None
        gate.set()
        after = self.wait(lambda state: state["stage"] == 7)
        self.assertEqual(next(node for node in after["nodes"] if node["id"] == beta["id"])["version"], beta["version"])
        counts = {source: sum(node["sourceNodeId"] == source and node["phase"] == "execute"
                              for node, _ in self.runner.calls) for source in ("10", "20")}
        self.assertEqual(counts, {"10": 2, "20": 1})

    def test_inserted_experiment_retains_protocol_and_evidence_reaches_ancestors(self):
        delegate = ResearchRunner()
        protocol = {"metric": "latency", "groups": {"甲": [1, 2, 3], "乙": [4, 5, 6]}}
        generated_id = "experiment:known:sha"

        def experimental(node, context, log):
            if node["kind"] == "experiment" and node["phase"] == "execute":
                self.assertEqual(node["input"]["experiment"], protocol)
                return {"summary": "均值有差异，意义待确认", "evidenceIds": [generated_id], "structured": {},
                        "claims": [{"id": "experiment-claim", "text": "记录均值差异", "evidenceIds": [generated_id]}],
                        "generatedEvidence": [{"id": generated_id, "type": "experiment", "paperId": "",
                                               "quote": "甲=2，乙=5", "locator": "artifact.json", "confidence": 1,
                                               "extractor": "experiment_statistics"}]}
            return delegate(node, context, log)

        engine = self.make_engine(runner=experimental, path=Path(self.temp.name) / "experiment.sqlite")
        self.confirm(engine)
        self.confirm(engine)
        before = self.wait(lambda state: state["stage"] == 7, engine)
        impact = engine.command("impact", {"nodeId": "facet:10", "kind": "insert"})
        engine.command("intervene", {"nodeId": "facet:10", "kind": "insert", "text": "分析输入数据",
                                     "taskKind": "experiment", "experiment": protocol, "expectedRevision": impact["revision"]})
        after = self.wait(lambda state: state["stage"] == 7 and state["activeCheckpointId"] != before["activeCheckpointId"], engine)
        self.assertIn(generated_id, {item["id"] for item in after["evidence"]})
        self.assertTrue(any(generated_id in claim["evidenceIds"] for claim in after["report"]["claims"]))
        final_context = next(context for node, context in reversed(delegate.calls) if node["id"] == "central")
        self.assertIn(generated_id, {item["id"] for item in final_context["library"]["evidence"]})

    def test_excessive_nested_decomposition_is_rejected_without_partial_nodes(self):
        child = {"title": "深层任务", "description": "分解", "acceptance": "可查", "constraints": "", "kind": "research"}
        nested = copy.deepcopy(child)
        for _ in range(4):
            nested = {**copy.deepcopy(child), "children": [nested]}

        def too_deep(node, context, log):
            return {"summary": "无限分解", "evidenceIds": [], "claims": [], "structured": {}, "children": [nested]}

        engine = self.make_engine(runner=too_deep, path=Path(self.temp.name) / "depth.sqlite")
        self.confirm(engine)
        self.confirm(engine)
        state = self.wait(lambda state: state["status"] == "failed", engine)
        self.assertEqual(len(state["nodes"]), 4)
        self.assertTrue(any("4 层" in activity["message"] for activity in state["activities"]))

    def test_max_depth_research_tasks_execute_without_another_planning_call(self):
        calls = []

        def refining(node, context, log):
            calls.append((copy.deepcopy(node), copy.deepcopy(context)))
            depth = node["input"].get("requestedDepth", 0)
            if node["phase"] == "plan":
                return {"summary": "明确下一层研究需求", "evidenceIds": [], "claims": [], "structured": {},
                        "children": [{"title": f"研究层 {depth + 1}", "description": f"第 {depth + 1} 层的具体问题",
                                      "acceptance": "逐条核对证据与边界", "constraints": "不能继续无限分解",
                                      "kind": "research", "requestedDepth": depth + 1}]}
            if node["phase"] == "execute":
                return {"summary": "界限叶任务已执行", "evidenceIds": ["101"], "structured": {"executedDepth": depth},
                        "claims": [{"id": "depth-claim", "text": "保留实际执行得到的候选观察", "evidenceIds": ["101"]}]}
            return copy.deepcopy(context["children"][0]["output"])

        engine = self.make_engine(runner=refining, path=Path(self.temp.name) / "bounded-refinement.sqlite", workflow="autonomous")
        engine.command("start-autonomous", {})
        state = self.wait(lambda state: state["status"] in ("completed", "failed"), engine)
        self.assertEqual(state["status"], "completed")
        self.assertEqual([context["remainingDepth"] for node, context in calls if node["phase"] == "plan"], [4, 3, 2, 1])
        executions = [(node, context) for node, context in calls if node["phase"] == "execute"]
        self.assertEqual(len(executions), 1)
        leaf, context = executions[0]
        self.assertEqual((leaf["input"]["requestedDepth"], context["remainingDepth"]), (4, 0))
        self.assertEqual(leaf["input"]["description"], "第 4 层的具体问题")
        self.assertEqual(leaf["input"]["acceptance"], "逐条核对证据与边界")
        self.assertEqual(state["report"]["claims"][0]["text"], "保留实际执行得到的候选观察")
        self.assertEqual(engine.node_history(leaf["id"])[0]["context"]["remainingDepth"], 0)

    def test_nested_research_leaf_at_max_depth_keeps_its_complete_execution_output(self):
        child = {"title": "嵌套研究", "description": "研究具体限制", "acceptance": "提供证据并标明未知",
                 "constraints": "只使用真实资料", "kind": "research"}
        nested = {**copy.deepcopy(child), "description": "不可丢弃的末层需求"}
        for _ in range(3):
            nested = {**copy.deepcopy(child), "children": [nested]}

        def nested_research(node, context, log):
            if node["id"] == "central" and node["phase"] == "plan":
                return {"summary": "明确四层研究任务", "evidenceIds": [], "claims": [], "structured": {}, "children": [nested]}
            if node["phase"] == "execute":
                self.assertEqual(context["remainingDepth"], 0)
                self.assertEqual(node["input"]["description"], "不可丢弃的末层需求")
                return {"summary": "按原始末层需求完成核验", "evidenceIds": ["101"], "claims": [],
                        "structured": {"concreteResult": "原始执行结果"}, "unresolved": ["边界仍待实验确认"]}
            if node["phase"] == "aggregate":
                return copy.deepcopy(context["children"][0]["output"])
            raise ValueError("最深层任务不应继续进入规划")

        engine = self.make_engine(runner=nested_research, path=Path(self.temp.name) / "nested-boundary.sqlite", workflow="autonomous")
        engine.command("start-autonomous", {})
        state = self.wait(lambda state: state["status"] in ("completed", "failed"), engine)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["report"]["structured"]["concreteResult"], "原始执行结果")
        self.assertIn("边界仍待实验确认", state["report"]["unresolved"])

    def test_runner_source_library_merges_only_with_a_valid_current_result(self):
        imported = sample_library()
        imported["papers"].append({**copy.deepcopy(imported["papers"][0]), "id": "3", "title": "代理检索新论文", "evidenceIds": ["103"]})
        imported["evidence"].append({**copy.deepcopy(imported["evidence"][0]), "id": "103", "paperId": "3"})

        def searching(node, context, log):
            return {"summary": "新增文献已核验", "evidenceIds": ["103"], "claims": [], "structured": {}, "sourceLibrary": imported}

        engine = self.make_engine(runner=searching, path=Path(self.temp.name) / "search.sqlite")
        self.confirm(engine)
        self.confirm(engine)
        state = self.wait(lambda state: state["stage"] == 7 or state["status"] == "failed", engine)
        self.assertEqual(state["stage"], 7)
        self.assertIn("3", {paper["id"] for paper in state["papers"]})
        self.assertNotIn("sourceLibrary", next(node for node in state["nodes"] if node["id"] == "central")["output"])

        def invalid_search(node, context, log):
            return {"summary": "格式不合格", "evidenceIds": ["unknown"], "claims": [], "structured": {}, "sourceLibrary": imported}

        bad = self.make_engine(runner=invalid_search, path=Path(self.temp.name) / "bad-search.sqlite")
        self.confirm(bad)
        self.confirm(bad)
        state = self.wait(lambda state: state["status"] == "failed", bad)
        self.assertNotIn("3", {paper["id"] for paper in state["papers"]})

    def test_authorized_search_budget_is_inherited_by_new_research_descendants(self):
        before = self.comparison()
        impact = self.engine.command("impact", {"nodeId": "facet:10", "kind": "deepen"})
        self.engine.command("intervene", {"nodeId": "facet:10", "kind": "deepen", "text": "检索新资料",
                                           "allowNewSearch": True, "searchBudgetId": "search-budget-one",
                                           "expectedRevision": impact["revision"]})
        after = self.wait(lambda state: state["stage"] == 7 and state["activeCheckpointId"] != before["activeCheckpointId"])
        demand = next(node for node in after["nodes"] if node["title"] == "检索新资料")
        child = next(node for node in after["nodes"] if node["parentId"] == demand["id"])
        self.assertIs(demand["input"]["allowNewSearch"], True)
        self.assertEqual(child["input"]["searchBudgetId"], "search-budget-one")

    def test_archived_execution_history_keeps_exact_inputs_outputs_and_invalidation(self):
        before = self.comparison()
        leaf = next(node for node in before["nodes"] if node["sourceNodeId"] == "10" and node["kind"] == "evidence")
        impact = self.engine.command("impact", {"nodeId": leaf["id"], "kind": "modify"})
        self.engine.command("intervene", {"nodeId": leaf["id"], "kind": "modify", "text": "只检验新增的边界条件",
                                           "expectedRevision": impact["revision"]})
        self.wait(lambda state: state["stage"] == 7 and state["activeCheckpointId"] != before["activeCheckpointId"])
        records = self.engine.node_history(leaf["id"])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["input"]["description"], "整理原始证据")
        self.assertEqual(records[1]["input"]["description"], "只检验新增的边界条件")
        self.assertFalse(records[0]["valid"])
        self.assertTrue(records[1]["valid"])
        self.assertEqual(records[0]["output"], leaf["output"])
        self.assertEqual(records[0]["context"]["requirements"], before["requirements"])
        self.engine.command("next-round", {})
        self.assertEqual(len(self.engine.node_history(leaf["id"])), 2)
        self.assertGreater(len(self.engine.node_history(None)), 2)

    def test_existing_database_schema_migrates_archived_input_column(self):
        path = Path(self.temp.name) / "old.sqlite"
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE node_outputs (id TEXT PRIMARY KEY, node_id TEXT NOT NULL, node_version INTEGER NOT NULL, round INTEGER NOT NULL, phase TEXT NOT NULL, facet TEXT NOT NULL DEFAULT 'research_output', source_node_id TEXT, created_at TEXT NOT NULL, valid INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL)")
            connection.execute("INSERT INTO node_outputs VALUES ('old-run','old-node',1,1,'execute','research_output',NULL,'2026-01-01',1,'{\"summary\":\"old\"}')")
            connection.commit()
        finally:
            connection.close()
        engine = self.make_engine(path=path)
        records = engine.node_history("old-node")
        self.assertEqual(records[0]["input"], {})
        self.assertEqual(records[0]["output"]["summary"], "old")

    def test_mode_change_cannot_bypass_pause_while_another_branch_is_failed(self):
        delegate = ResearchRunner()
        gate = threading.Event()
        entered = threading.Event()

        def partially_failed(node, context, log):
            if node["phase"] == "execute" and node["sourceNodeId"] == "10":
                raise RuntimeError("甲失败")
            if node["phase"] == "execute" and node["sourceNodeId"] == "20":
                entered.set()
                gate.wait(3)
            return delegate(node, context, log)

        engine = self.make_engine(runner=partially_failed, path=Path(self.temp.name) / "failed-mode.sqlite")
        self.confirm(engine)
        self.confirm(engine)
        self.assertTrue(entered.wait(2))
        self.wait(lambda state: state["status"] == "failed", engine)
        try:
            with self.assertRaises(ValueError):
                engine.command("mode", {"mode": "llm"})
        finally:
            gate.set()

    def test_inactive_source_node_impact_uses_nearest_active_source_ancestor(self):
        before = self.comparison()
        impact = self.engine.command("impact", {"nodeId": "facet:11", "kind": "modify"})
        self.assertEqual(set(impact["affectedIds"]), {"facet:11", "facet:10", "central"})
        self.engine.command("intervene", {"nodeId": "facet:11", "kind": "modify", "text": "激活该子切面研究",
                                           "expectedRevision": impact["revision"]})
        after = self.wait(lambda state: state["stage"] == 7 and state["activeCheckpointId"] != before["activeCheckpointId"])
        activated = next(node for node in after["nodes"] if node["id"] == "facet:11")
        self.assertEqual(activated["parentId"], "facet:10")
        self.assertTrue(activated["active"])

    def test_new_round_intervention_keeps_prior_round_confirmations_historical(self):
        self.comparison()
        self.confirm()
        self.confirm()
        old_ids = {item["id"] for item in self.engine.snapshot()["checkpoints"]}
        self.engine.command("next-round", {})
        self.comparison()
        impact = self.engine.command("impact", {"nodeId": "facet:10", "kind": "modify"})
        state = self.engine.command("intervene", {"nodeId": "facet:10", "kind": "modify", "text": "第二轮改进",
                                                   "expectedRevision": impact["revision"]})
        old = [item for item in state["checkpoints"] if item["id"] in old_ids]
        self.assertTrue(all(item["status"] == "confirmed" for item in old))

    def test_sibling_comparison_edges_are_visible_without_creating_execution_dependencies(self):
        before = self.comparison()
        comparisons = [edge for edge in before["edges"] if edge["type"] == "compare"]
        self.assertEqual(len(comparisons), 1)
        edge = comparisons[0]
        self.assertEqual({edge["source"], edge["target"]}, {"facet:10", "facet:20"})
        self.assertIs(edge["informational"], True)
        self.assertIn("同一上级需求", edge["reason"])
        unaffected_history = self.engine.node_history("facet:20")
        impact = self.engine.command("impact", {"nodeId": "facet:10", "kind": "modify"})
        self.assertNotIn("facet:20", impact["affectedIds"])
        self.engine.command("intervene", {"nodeId": "facet:10", "kind": "modify", "text": "重新核查甲的证据",
                                           "expectedRevision": impact["revision"]})
        after = self.wait(lambda state: state["stage"] == 7 and state["activeCheckpointId"] != before["activeCheckpointId"])
        self.assertEqual(self.engine.node_history("facet:20"), unaffected_history)
        self.assertEqual([edge for edge in after["edges"] if edge["type"] == "compare"], comparisons)

    def test_autonomous_start_requires_explicit_action_and_finishes_without_fixed_gates(self):
        engine = self.make_engine(path=Path(self.temp.name) / "autonomous.sqlite", workflow="autonomous")
        initial = engine.command("resume", {})
        self.assertIsNone(initial["activeCheckpointId"])
        self.assertTrue(initial["paused"])
        self.assertEqual(initial["status"], "idle")
        self.assertEqual(self.runner.calls, [])
        requirements = copy.deepcopy(initial["requirements"])
        requirements[0]["constraints"] = "# 用户原文\n\n完整需求 Markdown 与限制。"
        engine.command("requirements", {"requirements": requirements})
        engine.command("start-autonomous", {"requirements": requirements, "title": "自主科研课题",
                                             "mode": "evidence", "allowNewSearch": True,
                                             "searchBudgetId": "autonomous-budget"})
        state = self.wait(lambda value: value["status"] == "completed", engine)
        self.assertTrue(state["report"]["ready"])
        self.assertFalse(state["report"]["approved"])
        self.assertTrue(all(claim["status"] == "candidate" for claim in state["report"]["claims"]))
        self.assertEqual(state["report"]["structured"]["childCount"], 2)
        self.assertIsNone(state["activeCheckpointId"])
        self.assertFalse(any(item["status"] == "pending" for item in state["checkpoints"]))
        self.assertFalse(any(item["type"] in ("recommendations", "comparison", "final") for item in state["checkpoints"]))
        self.assertEqual(state["project"]["workflow"], "autonomous")
        self.assertEqual(state["project"]["title"], "自主科研课题")
        root, context = next((node, context) for node, context in self.runner.calls if node["id"] == "central")
        self.assertEqual(root["input"]["constraints"], requirements[0]["constraints"])
        self.assertIs(root["input"]["allowNewSearch"], True)
        self.assertEqual(root["input"]["searchBudgetId"], "autonomous-budget")
        self.assertEqual((context["workflow"], context["iteration"], context["maxIterations"]), ("autonomous", 1, 3))
        with self.assertRaises(ValueError):
            engine.command("start-autonomous", {})

    def test_autonomous_root_without_children_can_finish_as_candidate(self):
        def no_children(node, context, log):
            return {"summary": "现有材料不足，整理待验证问题", "evidenceIds": [],
                    "claims": [{"id": "unsupported", "text": "待检验猜想", "evidenceIds": [], "status": "confirmed"}],
                    "structured": {"nextResearch": ["补充论文"]}, "unresolved": ["缺少资料"]}

        engine = self.make_engine(runner=no_children, path=Path(self.temp.name) / "no-children.sqlite", workflow="autonomous")
        engine.command("start-autonomous", {})
        state = self.wait(lambda value: value["status"] == "completed", engine)
        self.assertTrue(state["report"]["ready"])
        self.assertFalse(state["report"]["approved"])
        self.assertEqual(state["report"]["claims"][0]["status"], "candidate")
        self.assertIn("无证据", state["report"]["claims"][0]["limitations"])
        self.assertEqual(state["report"]["structured"]["nextResearch"], ["补充论文"])

    def test_autonomous_followups_are_bounded_and_aggregate_only_after_new_children_complete(self):
        delegate = ResearchRunner()

        def continuing(node, context, log):
            result = delegate(node, context, log)
            if node["id"] == "central" and node["phase"] == "aggregate":
                result["followups"] = [{"title": "补充核验 " + str(context.get("iteration")),
                                        "description": "核对新的具体边界", "acceptance": "提供证据定位",
                                        "constraints": "不推断实验", "kind": "evidence", "paperIds": ["1"]}]
            return result

        engine = self.make_engine(runner=continuing, path=Path(self.temp.name) / "followups.sqlite", workflow="autonomous")
        engine.command("start-autonomous", {})
        state = self.wait(lambda value: value["status"] == "completed", engine)
        aggregates = [context for node, context in delegate.calls if node["id"] == "central" and node["phase"] == "aggregate"]
        self.assertEqual([context["iteration"] for context in aggregates], [1, 2, 3])
        self.assertEqual([len(context["children"]) for context in aggregates], [2, 3, 4])
        self.assertTrue(all(child["status"] == "completed" for context in aggregates for child in context["children"]))
        self.assertEqual(sum(node["id"] == "central" and node["phase"] == "plan" for node, _ in delegate.calls), 1)
        self.assertEqual(state["project"]["researchIteration"], 3)
        self.assertEqual(len(state["report"]["structured"]["deferredFollowups"]), 1)
        self.assertTrue(any("预算" in value for value in state["report"]["unresolved"]))
        self.assertIsNone(state["activeCheckpointId"])

    def test_autonomous_completion_supports_deepening_and_new_round_without_old_gates(self):
        engine = self.make_engine(path=Path(self.temp.name) / "auto-round.sqlite", workflow="autonomous")
        engine.command("start-autonomous", {"searchBudgetId": "first-round"})
        self.wait(lambda state: state["report"].get("ready"), engine)
        impact = engine.command("impact", {"nodeId": "facet:10", "kind": "deepen"})
        changed = engine.command("intervene", {"nodeId": "facet:10", "kind": "deepen", "text": "深入考察理论边界",
                                                "expectedRevision": impact["revision"]})
        self.assertFalse(changed["report"]["ready"])
        self.assertFalse(changed["paused"])
        self.assertIsNone(changed["activeCheckpointId"])
        self.wait(lambda state: state["report"].get("ready"), engine)
        prepared = engine.command("next-round", {})
        self.assertEqual(prepared["project"]["round"], 2)
        self.assertFalse(prepared["report"]["ready"])
        self.assertTrue(prepared["paused"])
        self.assertIsNone(prepared["activeCheckpointId"])
        resumed = engine.command("resume", {})
        self.assertTrue(resumed["paused"])
        engine.command("start-autonomous", {"allowNewSearch": False})
        completed = self.wait(lambda state: state["report"].get("ready"), engine)
        root = next(node for node in completed["nodes"] if node["id"] == "central")
        self.assertIs(root["input"]["allowNewSearch"], False)
        self.assertNotIn("searchBudgetId", root["input"])
        self.assertFalse(completed["report"]["approved"])

    def test_autonomous_failure_stays_failed_until_retry(self):
        self.runner.fail_once = True
        engine = self.make_engine(path=Path(self.temp.name) / "auto-failure.sqlite", workflow="autonomous")
        engine.command("start-autonomous", {})
        failed = self.wait(lambda state: state["status"] == "failed", engine)
        self.assertFalse(failed["report"]["ready"])
        node = next(node for node in failed["nodes"] if node["status"] == "failed")
        engine.command("retry", {"nodeId": node["id"]})
        self.wait(lambda state: state["status"] == "completed", engine)

    def test_autonomous_rollback_remains_paused_and_discards_late_results_without_a_user_gate(self):
        gate = threading.Event()
        self.runner.block = gate
        engine = self.make_engine(path=Path(self.temp.name) / "auto-rollback.sqlite", workflow="autonomous")
        started = engine.command("start-autonomous", {})
        checkpoint = started["checkpoints"][-1]["id"]
        self.assertTrue(self.runner.entered.wait(2))
        restored = engine.command("rollback", {"checkpointId": checkpoint})
        self.assertIsNone(restored["activeCheckpointId"])
        self.assertTrue(restored["paused"])
        self.assertFalse(restored["report"]["ready"])
        self.runner.block = None
        gate.set()
        time.sleep(.05)
        self.assertEqual(engine.snapshot()["revision"], restored["revision"])
        engine.command("resume", {})
        self.wait(lambda state: state["status"] == "completed", engine)

    def test_autonomous_workflow_and_ready_report_survive_reopen(self):
        path = Path(self.temp.name) / "auto-reopen.sqlite"
        engine = self.make_engine(path=path, workflow="autonomous")
        engine.command("start-autonomous", {})
        before = self.wait(lambda state: state["status"] == "completed", engine)
        engine.close()
        reopened = self.make_engine(path=path)
        self.assertEqual(reopened.snapshot()["project"]["workflow"], "autonomous")
        self.assertEqual(reopened.snapshot()["report"], before["report"])
        impact = reopened.command("impact", {"nodeId": "facet:10", "kind": "modify"})
        state = reopened.command("intervene", {"nodeId": "facet:10", "kind": "modify", "text": "复查来源",
                                                "expectedRevision": impact["revision"]})
        self.assertFalse(state["paused"])
        self.assertFalse(state["report"]["ready"])
        self.wait(lambda value: value["status"] == "completed", reopened)

    def test_autonomous_global_requirement_change_replans_without_confirmation_wizard(self):
        engine = self.make_engine(path=Path(self.temp.name) / "auto-edit.sqlite", workflow="autonomous")
        engine.command("start-autonomous", {})
        before = self.wait(lambda state: state["status"] == "completed", engine)
        requirements = copy.deepcopy(before["requirements"])
        requirements[0]["description"] = "收窄为新的可验收问题"
        impact = engine.command("impact", {"nodeId": "central", "kind": "modify"})
        changed = engine.command("intervene", {"nodeId": "central", "kind": "modify", "text": "收窄范围",
                                                "requirements": requirements, "expectedRevision": impact["revision"]})
        self.assertIsNone(changed["activeCheckpointId"])
        self.assertFalse(changed["report"]["ready"])
        self.assertFalse(changed["paused"])
        after = self.wait(lambda state: state["status"] == "completed", engine)
        self.assertEqual(after["requirements"][0]["description"], "收窄为新的可验收问题")


if __name__ == "__main__":
    unittest.main()
