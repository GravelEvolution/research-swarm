import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from research_swarm.library import Library


@contextlib.contextmanager
def database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


SCHEMA = """
CREATE TABLE Paper (PaperID INTEGER PRIMARY KEY, Title TEXT, Abstract TEXT,
 Year INTEGER, Venue TEXT, DOI TEXT, Authors TEXT, CodeURL TEXT, PDFPath TEXT);
CREATE TABLE ResearchTopic (TopicID INTEGER PRIMARY KEY, TopicName TEXT,
 Description TEXT, SuccessCriteria TEXT, Priority INTEGER, Status TEXT);
CREATE TABLE Facet (FacetID INTEGER PRIMARY KEY, FacetName TEXT, FacetTopology TEXT);
CREATE TABLE FacetNode (NodeID INTEGER PRIMARY KEY, FacetID INTEGER, NodeName TEXT,
 ParentNodeID INTEGER, Description TEXT, Status TEXT);
CREATE TABLE PaperFacet (PaperFacetID INTEGER PRIMARY KEY, PaperID INTEGER, NodeID INTEGER,
 FactID INTEGER, EvidenceID INTEGER, Confidence REAL, IsHumanConfirmed INTEGER);
CREATE TABLE Evidence (EvidenceID INTEGER PRIMARY KEY, PaperID INTEGER, EvType TEXT,
 QuoteText TEXT, Locator TEXT, Extractor TEXT, Confidence REAL);
CREATE TABLE Fact (FactID INTEGER PRIMARY KEY, PaperID INTEGER, FactType TEXT,
 Content TEXT, ValueNum REAL, Unit TEXT, EvidenceID INTEGER, ParentFactID INTEGER);
CREATE TABLE DecisionCard (CardID INTEGER PRIMARY KEY, PaperID INTEGER,
 NoveltyScore REAL, RelevanceScore REAL, ImpactScore REAL, ReproValue REAL,
 Urgency REAL, OverallScore REAL, OneLinePosition TEXT, ImpactNote TEXT,
 KeyLimitation TEXT, IsHumanReviewed INTEGER);
CREATE TABLE ScoreBasis (BasisID INTEGER PRIMARY KEY, CardID INTEGER, ScoreField TEXT,
 FactID INTEGER, AssociationID INTEGER, EvidenceID INTEGER, Weight REAL, Note TEXT);
CREATE TABLE EvidenceFlag (FlagID INTEGER PRIMARY KEY, CardID INTEGER, HasCode INTEGER,
 HasOfficialRepo INTEGER, HasAblation INTEGER, HasOpenData INTEGER, EvidenceGap TEXT);
CREATE TABLE RelationType (RelTypeID INTEGER PRIMARY KEY, TypeName TEXT,
 IsDirected INTEGER, FacetID INTEGER);
CREATE TABLE RelEdge (RelationID INTEGER PRIMARY KEY, RelTypeID INTEGER,
 FromPaperID INTEGER, FromNodeID INTEGER, ToPaperID INTEGER, ToNodeID INTEGER,
 Weight REAL, EvidenceID INTEGER, Confidence REAL);
CREATE TABLE Association (AssocID INTEGER PRIMARY KEY, Dim TEXT, SourceRefType TEXT,
 SourceRefID INTEGER, TargetRefType TEXT, TargetRefID INTEGER,
 Degree REAL, Method TEXT, EvidenceID INTEGER);
CREATE TABLE RetrievalTask (TaskID INTEGER PRIMARY KEY, RootNodeID INTEGER,
 LocalOverrideJson TEXT, Status TEXT);
CREATE TABLE RankedResult (ResultID INTEGER PRIMARY KEY, TaskID INTEGER,
 PaperID INTEGER, Rank INTEGER);
"""


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self.root / "tasks" / "topic-b"
        self.db = self.task / "data" / "paper_research.sqlite"
        self.db.parent.mkdir(parents=True)
        self.pdf_dir = self.task / "papers"
        self.pdf_dir.mkdir()
        self.config = {
            "tasks": {"active_id": "topic-b", "root": "tasks"},
            "storage": {"db_path": "data/paper_research.sqlite", "pdf_dir": "papers"},
            "topics": [
                {"id": "topic-a", "name": "Other", "priority": 1},
                {"id": "topic-b", "name": "Selected", "success_criteria": "Bounded cost", "priority": 2},
            ],
            "models": {"provider": {"api_key": "private-fixture-secret"}},
        }
        self.write_config()
        (self.task / "task.json").write_text(json.dumps({
            "id": "topic-b", "name": "Selected", "topic_id": "topic-b",
            "description": "Task description",
        }), encoding="utf-8")
        with database(self.db) as conn:
            conn.executescript(SCHEMA)
            conn.executescript("""
                INSERT INTO Paper VALUES (7, 'Paper seven', 'Abstract seven', 2024,
                  'Venue', '10.example/seven', 'A; B', NULL, NULL);
                INSERT INTO Paper VALUES (12, 'Paper twelve', '', 2025,
                  '', NULL, 'C', 'https://example.test/code', NULL);
                INSERT INTO ResearchTopic VALUES (5, 'Other', 'wrong topic', '', 1, 'active');
                INSERT INTO ResearchTopic VALUES (8, 'Selected', 'Research topic description',
                  'Bounded cost', 2, 'active');
                INSERT INTO Facet VALUES (2, 'Methods', 'Tree');
                INSERT INTO Facet VALUES (3, 'Similarities', 'Network');
                INSERT INTO FacetNode VALUES (31, 2, 'Root method', NULL, '', 'active');
                INSERT INTO FacetNode VALUES (32, 2, 'Child method', 31, '', 'active');
                INSERT INTO FacetNode VALUES (37, 3, 'Related method', NULL, '', 'active');
                INSERT INTO PaperFacet VALUES (20, 7, 32, 41, 91, 0.8, 0);
                INSERT INTO PaperFacet VALUES (21, 12, 37, NULL, NULL, 0.7, 0);
                INSERT INTO Evidence VALUES (91, 7, 'abstract', 'Actual abstract excerpt',
                  'Abstract', 'OpenAlex', 0.8);
                INSERT INTO Evidence VALUES (92, 12, 'unknown', 'Unlocated quote',
                  NULL, 'AI', 0.4);
                INSERT INTO Fact VALUES (41, 7, 'finding', 'Abstract-based finding', NULL,
                  NULL, 91, NULL);
                INSERT INTO DecisionCard VALUES (51, 7, 2, 4, 5, 3, 3, 3.65,
                  'Candidate route', 'heuristic', 'Only abstract', 1);
                INSERT INTO DecisionCard VALUES (52, 12, 5, 5, 4, 5, 2, 4.3,
                  'Candidate twelve', 'heuristic', '', 0);
                INSERT INTO ScoreBasis VALUES (61, 51, 'NoveltyScore', 41, NULL, 91, 1,
                  'Abstract-based heuristic');
                INSERT INTO EvidenceFlag VALUES (71, 52, 1, 1, 0, 1, 'Environment untested');
                INSERT INTO RelationType VALUES (3, 'similar_to', 0, 3);
                INSERT INTO RelEdge VALUES (15, 3, 7, NULL, 12, NULL, 0.7, 91, 0.6);
                INSERT INTO Association VALUES (19, 'topic_relevance', 'Paper', 7,
                  'ResearchTopic', 8, 0.8, 'lexicon', 91);
            """)

    def write_config(self):
        (self.root / "config.json").write_text(json.dumps(self.config), encoding="utf-8")

    def execute(self, sql, params=()):
        with database(self.db) as conn:
            conn.execute(sql, params)

    def write_pdf(self, name="0007.pdf", payload=b"%PDF-1.7\nfixture\n%%EOF"):
        path = self.pdf_dir / name
        path.write_bytes(payload)
        return path.resolve()

    def test_active_task_data_preserves_ids_links_and_original_scores(self):
        data = Library(self.root).load()
        self.assertEqual(data.get("sourceDb"), str(self.db.resolve()))
        self.assertEqual(data["topic"], {"id": "8", "title": "Selected",
            "description": "Research topic description", "successCriteria": "Bounded cost"})
        self.assertEqual([p["id"] for p in data["papers"]], ["7", "12"])
        paper = data["papers"][0]
        self.assertEqual(paper["score"], 73.0)
        self.assertEqual(paper["scores"], {"novelty": 40.0, "relevance": 80.0,
            "impact": 100.0, "reproducibility": 60.0, "urgency": 60.0})
        self.assertEqual(paper["facetNodeIds"], ["32"])
        self.assertEqual(paper["evidenceIds"], ["91"])
        self.assertEqual(paper["facts"][0]["id"], "41")
        self.assertEqual(paper["facts"][0]["evidenceId"], "91")
        self.assertEqual(paper["scoreBasis"][0]["id"], "61")
        self.assertEqual(paper["scoreBasis"][0]["evidenceId"], "91")
        self.assertEqual(paper["workerStatus"], "pending")
        self.assertIsNone(paper["feedback"])
        self.assertEqual(data["facetNodes"][1]["parentId"], "31")
        self.assertEqual(data["facetNodes"][1]["paperIds"], ["7"])
        self.assertEqual(data["facetNodes"][2]["topology"], "Network")
        self.assertEqual(data["stats"]["scoreBasisCount"], 1)

    def test_network_and_relevance_relations_retain_typed_endpoints(self):
        relations = Library(self.root).load()["relations"]
        self.assertEqual(len(relations), 2)
        network = next(r for r in relations if r["kind"] == "network")
        self.assertEqual((network["id"], network["source"], network["target"]), ("15", "7", "12"))
        self.assertEqual((network["sourceType"], network["targetType"]), ("Paper", "Paper"))
        self.assertEqual(network["relationType"], "similar_to")
        self.assertFalse(network["directed"])
        self.assertEqual(network["evidenceId"], "91")
        relevance = next(r for r in relations if r["kind"] == "relevance")
        self.assertEqual((relevance["id"], relevance["targetType"], relevance["target"]),
                         ("19", "ResearchTopic", "8"))

    def test_materials_and_abstract_evidence_never_claim_reproduced_success(self):
        data = Library(self.root).load()
        self.assertEqual(data["evidence"][0]["type"], "abstract")
        self.assertEqual(data["evidence"][0]["locator"], "Abstract")
        self.assertEqual(data["evidence"][1]["locator"], "未定位")
        self.assertEqual(data["stats"]["abstractEvidenceCount"], 1)
        for paper in data["papers"]:
            self.assertIn("未执行复现实验", paper["reproducibility"])
            self.assertNotIn("复现成功", paper["reproducibility"])
        self.assertIn("未验证", data["papers"][1]["reproducibility"])
        self.assertIn("Environment untested", data["papers"][1]["reproducibility"])

    def test_load_leaves_source_bytes_and_file_set_unchanged_and_omits_keys(self):
        before = {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob("*") if p.is_file()}
        data = Library(self.root).load()
        after = {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertNotIn("private-fixture-secret", json.dumps(data))

    def test_missing_active_database_is_empty_without_silent_legacy_fallback(self):
        self.db.unlink()
        legacy = self.root / "data" / "paper_research.sqlite"
        legacy.parent.mkdir()
        with database(legacy) as conn:
            conn.execute("CREATE TABLE Paper (PaperID INTEGER, Title TEXT)")
            conn.execute("INSERT INTO Paper VALUES (99, 'Wrong task')")
        data = Library(self.root).load()
        self.assertEqual(data["papers"], [])
        self.assertEqual(data["topic"]["title"], "Selected")
        self.assertTrue(data["stats"]["warnings"])
        self.assertFalse(self.db.exists())

    def test_missing_optional_tables_preserves_partial_library(self):
        self.execute("DROP TABLE ScoreBasis")
        self.execute("DROP TABLE Evidence")
        self.execute("DROP TABLE DecisionCard")
        data = Library(self.root).load()
        self.assertEqual(len(data["papers"]), 2)
        self.assertEqual(data["papers"][0]["score"], 0)
        self.assertEqual(data["papers"][0]["scoreBasis"], [])
        self.assertEqual(data["evidence"], [])
        self.assertIn("Evidence", data["stats"]["missingTables"])
        self.assertTrue(data["stats"]["warnings"])

    def test_missing_facet_metadata_is_not_invented_as_tree_topology(self):
        self.execute("DROP TABLE Facet")
        data = Library(self.root).load()
        self.assertEqual(len(data["facetNodes"]), 3)
        self.assertEqual(data["facetNodes"][2]["topology"], "Unknown")

    def test_orphaned_source_records_are_preserved_with_visible_warning(self):
        self.execute("INSERT INTO Evidence VALUES (93, 999, 'abstract', 'Orphan', 'Abstract', 'AI', 0.5)")
        data = Library(self.root).load()
        self.assertEqual(len(data["evidence"]), 3)
        self.assertEqual(data["evidence"][-1]["paperId"], "999")
        self.assertTrue(any("不存在" in warning for warning in data["stats"]["warnings"]))

    def test_empty_database_and_explicit_file_source_do_not_require_config(self):
        standalone = self.root / "empty.sqlite"
        with database(standalone):
            pass
        data = Library(standalone).load()
        self.assertEqual(data["sourceDb"], str(standalone.resolve()))
        self.assertEqual(data["papers"], [])
        self.assertEqual(data["facetNodes"], [])
        self.assertEqual(data["stats"]["paperCount"], 0)
        self.assertIn("Paper", data["stats"]["missingTables"])

    def test_invalid_and_out_of_range_scores_are_finite_and_bounded(self):
        self.execute("""UPDATE DecisionCard SET NoveltyScore=-3, RelevanceScore=99,
            ImpactScore='bad', ReproValue=NULL, Urgency='inf', OverallScore='NaN' WHERE CardID=51""")
        paper = Library(self.root).load()["papers"][0]
        self.assertEqual(paper["score"], 0)
        self.assertEqual(paper["scores"], {"novelty": 0.0, "relevance": 100.0,
            "impact": 0.0, "reproducibility": 0.0, "urgency": 0.0})

    def test_stale_pdf_path_resolves_only_known_pdf_in_current_task(self):
        pdf = self.write_pdf()
        self.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=7", (r"C:\old-machine\papers\0007.pdf",))
        library = Library(self.root)
        data = library.load()
        self.assertTrue(data["papers"][0]["pdfAvailable"])
        self.assertEqual(data["papers"][0]["pdfPath"], str(pdf))
        self.assertEqual(library.pdf_path("7"), pdf)
        self.assertIsNone(library.pdf_path("../../config.json"))
        self.assertIsNone(library.pdf_path("999"))

    def test_pdf_lookup_rejects_outside_root_disguised_file_and_unlinked_file(self):
        outside = self.root / "private.pdf"
        outside.write_bytes(b"%PDF-1.7\nprivate")
        self.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=7", (str(outside),))
        self.assertIsNone(Library(self.root).pdf_path("7"))
        disguised = self.write_pdf("not-a-pdf.pdf", b"<html>credentials</html>")
        self.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=7", (str(disguised),))
        self.assertIsNone(Library(self.root).pdf_path("7"))
        self.write_pdf("0012.pdf")
        self.assertIsNone(Library(self.root).pdf_path("12"))

    def test_pdf_lookup_rejects_numeric_filename_belonging_to_other_paper(self):
        pdf = self.write_pdf("0012.pdf")
        self.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=7", (str(pdf),))
        self.assertIsNone(Library(self.root).pdf_path("7"))

    def test_pdf_symlink_cannot_escape_expected_pdf_root(self):
        outside = self.root / "private.pdf"
        outside.write_bytes(b"%PDF-1.7\nprivate")
        link = self.pdf_dir / "0007.pdf"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("Creating symbolic links is unavailable on this host")
        self.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=7", (str(link),))
        self.assertIsNone(Library(self.root).pdf_path("7"))

    def install_fake_cli(self, fail=False):
        package = self.root / "paper_research"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text('''
import argparse, json, sqlite3, sys
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('command', choices=['retrieve'])
parser.add_argument('--query', required=True)
parser.add_argument('--top-k', type=int, required=True)
parser.add_argument('--node', type=int)
parser.add_argument('--topic')
args = parser.parse_args()
root = Path.cwd()
(root / 'invocation.json').write_text(json.dumps(vars(args)), encoding='utf-8')
secret = json.loads((root / 'config.json').read_text())['models']['provider']['api_key']
print(secret)
print('stderr: ' + secret, file=sys.stderr)
''' + ("sys.exit(7)\n" if fail else '''
cfg = json.loads((root / 'config.json').read_text())
db = root / 'tasks' / cfg['tasks']['active_id'] / 'data' / 'paper_research.sqlite'
with sqlite3.connect(db) as conn:
    conn.execute("INSERT INTO Paper (PaperID, Title) VALUES (88, 'Retrieved paper')")
'''), encoding="utf-8")

    def test_retrieve_runs_explicit_cli_with_query_top_k_and_node_without_log_leaks(self):
        self.install_fake_cli()
        library = Library(self.root)
        library.load()
        self.assertFalse((self.root / "invocation.json").exists())
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = library.retrieve("--主题 检索 ; literal", top_k=3, node_id="32")
        call = json.loads((self.root / "invocation.json").read_text(encoding="utf-8"))
        self.assertEqual(call["query"], "--主题 检索 ; literal")
        self.assertEqual(call["top_k"], 3)
        self.assertEqual(call["node"], 32)
        self.assertTrue(result["ok"])
        self.assertEqual(result["retrieval"]["newPaperIds"], ["88"])
        self.assertEqual(len(result["library"]["papers"]), 3)
        self.assertNotIn("private-fixture-secret", output.getvalue() + json.dumps(result))
        self.assertFalse((self.root / "paper_research" / "__pycache__").exists())

    def test_retrieval_returns_ranked_existing_papers_for_the_exact_cli_task(self):
        self.install_fake_cli()
        self.execute("INSERT INTO RetrievalTask VALUES (9, 32, ?, 'done')",
                     (json.dumps({"keywords": "existing query", "top_k": 2}),))
        self.execute("INSERT INTO RankedResult VALUES (3, 9, 7, 1)")
        entrypoint = self.root / "paper_research" / "__main__.py"
        source = entrypoint.read_text(encoding="utf-8")
        source = source.replace(
            "    conn.execute(\"INSERT INTO Paper (PaperID, Title) VALUES (88, 'Retrieved paper')\")",
            '''    conn.execute("INSERT INTO RetrievalTask VALUES (10, ?, ?, 'done')", (args.node, json.dumps({'keywords': args.query, 'top_k': args.top_k})))
    conn.execute("INSERT INTO RankedResult VALUES (4, 10, 12, 1)")
    conn.execute("INSERT INTO RankedResult VALUES (5, 10, 7, 2)")
    conn.execute("INSERT INTO RetrievalTask VALUES (11, 32, ?, 'done')", (json.dumps({'keywords': 'different query', 'top_k': 2}),))
    conn.execute("INSERT INTO RankedResult VALUES (6, 11, 7, 1)")
print('[retrieve_rank] contract=retrieve_rank/v1 task=10 node=32')''')
        entrypoint.write_text(source, encoding="utf-8")
        result = Library(self.root).retrieve("existing query", top_k=2, node_id="32")
        self.assertEqual(result["retrieval"]["newPaperIds"], [])
        self.assertEqual(result["retrieval"].get("resultPaperIds"), ["12", "7"])
        self.assertEqual(result["retrieval"].get("sourceTaskId"), "10")
        self.assertTrue(result["retrieval"].get("resultLookupComplete"))

    def test_retrieval_with_unverifiable_task_keeps_result_ids_empty(self):
        self.install_fake_cli()
        self.execute("INSERT INTO RetrievalTask VALUES (9, 32, ?, 'done')",
                     (json.dumps({"keywords": "existing query", "top_k": 2}),))
        self.execute("INSERT INTO RankedResult VALUES (3, 9, 7, 1)")
        entrypoint = self.root / "paper_research" / "__main__.py"
        with entrypoint.open("a", encoding="utf-8") as handle:
            handle.write("\nprint('[retrieve_rank] contract=retrieve_rank/v1 task=9 node=32')\n")
        result = Library(self.root).retrieve("existing query", top_k=2, node_id="32")
        self.assertEqual(result["retrieval"].get("resultPaperIds"), [])
        self.assertFalse(result["retrieval"].get("resultLookupComplete", True))

    def test_retrieve_failure_redacts_child_output(self):
        self.install_fake_cli(fail=True)
        with self.assertRaises(RuntimeError) as failure:
            Library(self.root).retrieve("research")
        self.assertIn("7", str(failure.exception))
        self.assertNotIn("private-fixture-secret", str(failure.exception))

    def test_retrieve_rejects_invalid_input_before_starting_source_process(self):
        self.install_fake_cli()
        library = Library(self.root)
        for query, top_k, node_id in [(" ", 5, None), ("x", 0, None),
                ("x", 101, None), ("x", True, None), ("x", 3, "999")]:
            with self.subTest(query=query, top_k=top_k, node_id=node_id):
                with self.assertRaises(ValueError):
                    library.retrieve(query, top_k, node_id)
        self.assertFalse((self.root / "invocation.json").exists())

    def test_direct_inactive_database_cannot_retrieve_into_wrong_task(self):
        self.install_fake_cli()
        inactive = self.root / "tasks" / "other" / "data" / "paper_research.sqlite"
        inactive.parent.mkdir(parents=True)
        with database(inactive):
            pass
        with self.assertRaises(ValueError):
            Library(inactive).retrieve("research")
        self.assertFalse((self.root / "invocation.json").exists())

    def test_retrieval_does_not_merge_an_active_task_switched_during_child_run(self):
        self.install_fake_cli()
        entrypoint = self.root / "paper_research" / "__main__.py"
        with entrypoint.open("a", encoding="utf-8") as handle:
            handle.write("\ncfg['tasks']['active_id'] = 'different-task'\n")
            handle.write("(root / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')\n")
        with self.assertRaises(RuntimeError) as failure:
            Library(self.root).retrieve("research")
        self.assertIn("课题", str(failure.exception))

    def test_invalid_config_error_does_not_echo_contents(self):
        (self.root / "config.json").write_text('{"api_key": "private-fixture-secret"', encoding="utf-8")
        with self.assertRaises(ValueError) as failure:
            Library(self.root).load()
        self.assertNotIn("private-fixture-secret", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
