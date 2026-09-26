"""Bounded model corrections and real-page prompt delivery; no live configuration."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from research_swarm.providers import ModelOutputError, Settings, parse_json_object
from research_swarm.runner import ResearchRunner


class ScriptedProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def chat(self, messages, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        if not self.replies:
            raise AssertionError("Model request exceeded the supplied correction budget")
        value = self.replies.pop(0)
        return value if isinstance(value, str) else json.dumps(value)


def context_with_papers(count=1, evidence_per_paper=1):
    papers, evidence = [], []
    for number in range(1, count + 1):
        paper_id = str(number)
        ids = [f"abstract:{paper_id}:{part}" for part in range(evidence_per_paper)]
        papers.append({"id": paper_id, "title": f"Paper {number}", "abstract": "Observed abstract.",
                       "score": 100 - number, "pdfAvailable": False, "evidenceIds": ids,
                       "facetNodeIds": []})
        evidence.extend({"id": evidence_id, "paperId": paper_id, "quote": "Observed abstract.",
                         "type": "abstract", "locator": "Abstract"} for evidence_id in ids)
    return {"library": {"papers": papers, "evidence": evidence, "facetNodes": []},
            "mode": "llm", "requirements": [], "children": []}


class ModelBoundaryTests(unittest.TestCase):
    def run_model(self, provider, *, phase="execute", context=None, read_pdf=None, logs=None):
        node = {"id": "leaf", "phase": phase, "input": {}}
        # The model runner writes no artifacts; this path is never accessed.
        runner = ResearchRunner(provider, Path("__unused_boundary_artifacts__"), read_pdf=read_pdf)
        return runner(node, context or context_with_papers(), (logs if logs is not None else []).append)

    def test_null_arrays_get_one_structural_correction(self):
        task = {"title": "Read method", "description": "Check the method.", "acceptance": "Cite evidence."}
        cases = [
            ("unresolved", {"summary": "Needs correction", "unresolved": None}, "execute"),
            ("requirementIds", {"summary": "Needs correction", "children": [dict(task, requirementIds=None)]}, "plan"),
            ("paperIds", {"summary": "Needs correction", "children": [dict(task, paperIds=None)]}, "plan"),
        ]
        for name, invalid, phase in cases:
            with self.subTest(field=name):
                provider = ScriptedProvider([invalid, {"summary": "Corrected", "unresolved": []}])
                result = self.run_model(provider, phase=phase)
                self.assertEqual(result["summary"], "Corrected")
                self.assertEqual(len(provider.requests), 2)

    def test_malformed_tool_call_gets_one_structural_correction(self):
        provider = ScriptedProvider([{"toolCalls": [None]}, {"summary": "Corrected without a tool"}])
        result = self.run_model(provider)
        self.assertEqual(result["summary"], "Corrected without a tool")
        self.assertEqual(len(provider.requests), 2)

    def test_second_invalid_structure_stops_without_a_third_request(self):
        provider = ScriptedProvider([{"summary": "Invalid", "unresolved": None}] * 2)
        with self.assertRaises(ValueError):
            self.run_model(provider)
        self.assertEqual(len(provider.requests), 2)

    def test_two_tool_rounds_and_one_correction_fit_the_four_request_budget(self):
        provider = ScriptedProvider([
            {"toolCalls": [{"name": "paper_search", "arguments": {"query": "Observed"}}]},
            {"summary": "Needs correction", "unresolved": None},
            {"toolCalls": [{"name": "evidence_lookup", "arguments": {"evidenceIds": ["abstract:1:0"]}}]},
            {"summary": "Grounded", "evidenceIds": ["abstract:1:0"]},
        ])
        result = self.run_model(provider)
        self.assertEqual(result["evidenceIds"], ["abstract:1:0"])
        self.assertEqual(len(provider.requests), 4)

    def test_nonfinite_constants_are_recoverable_json_format_errors(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant), self.assertRaises(ModelOutputError):
                parse_json_object('{"summary":"Invalid numeric result","structured":{"score":' + constant + '}}')

    def test_nonfinite_json_uses_exactly_one_provider_format_retry(self):
        # __new__ deliberately avoids Settings.__init__: no local config or environment key is read.
        settings = Settings.__new__(Settings)
        logs = []
        with patch.object(settings, "_chat_once", side_effect=[
            '{"summary":"Invalid","structured":{"score":NaN}}',
            '{"summary":"Corrected","structured":{"score":null}}',
        ]) as request:
            result = settings.chat([{"role": "user", "content": "Return a JSON result."}],
                                   json_mode=True, on_retry=logs.append)
        self.assertEqual(parse_json_object(result)["summary"], "Corrected")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(logs), 1)

    def test_prefetched_pages_and_updated_metadata_reach_a_crowded_prompt(self):
        context = context_with_papers(count=12, evidence_per_paper=3)
        original = copy.deepcopy(context)
        read_calls, new_evidence = [], []
        def read_pdf(paper_id, page_start, page_count):
            read_calls.append((paper_id, page_start, page_count))
            pages = [{"id": f"pdf:{paper_id}:abc123:p{page}", "paperId": paper_id,
                      "quote": f"Actual extracted text on page {page}.", "locator": f"PDF page {page}",
                      "type": "full_text", "extractor": "pypdf"} for page in range(1, 4)]
            new_evidence.extend(pages)
            return {"paperId": paper_id, "totalPages": 15, "pages": [], "evidence": pages, "limitations": []}
        provider = ScriptedProvider([{"summary": "Read the supplied material"}])
        result = self.run_model(provider, context=context, read_pdf=read_pdf)
        prompt = json.loads(provider.requests[0][1]["content"])["library"]
        self.assertEqual(read_calls, [("1", 1, 3), ("2", 1, 3)])
        self.assertEqual(context, original, "A prompt update must not mutate the engine's input snapshot")
        expected_ids = {item["id"] for item in new_evidence}
        with self.subTest(boundary="new page evidence survives the existing 36-evidence limit"):
            self.assertTrue(expected_ids.issubset({item["id"] for item in prompt["evidence"]}))
        for paper_id in ("1", "2"):
            with self.subTest(boundary="metadata reflects successful PDF reading", paper=paper_id):
                paper = next(item for item in prompt["papers"] if item["id"] == paper_id)
                self.assertTrue(paper["pdfAvailable"])
                self.assertTrue({item["id"] for item in new_evidence if item["paperId"] == paper_id}.issubset(paper["evidenceIds"]))
        self.assertTrue(expected_ids.issubset({item["id"] for item in result["sourceLibrary"]["evidence"]}))


if __name__ == "__main__":
    unittest.main()
