from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from research_swarm.library import Library
from research_swarm.sources import prepare_source


DDL = """
CREATE TABLE IF NOT EXISTS ResearchTopic (TopicID INTEGER PRIMARY KEY, TopicName TEXT,
 Description TEXT, Keywords TEXT, SuccessCriteria TEXT, Priority INTEGER, Status TEXT);
CREATE TABLE IF NOT EXISTS Facet (FacetID INTEGER PRIMARY KEY, FacetName TEXT,
 FacetTopology TEXT, DecisionRole TEXT);
CREATE TABLE IF NOT EXISTS FacetNode (NodeID INTEGER PRIMARY KEY, FacetID INTEGER,
 NodeName TEXT, Description TEXT, ParentNodeID INTEGER, NodeLevel INTEGER, Path TEXT,
 NodeKind TEXT, IsSearchRoot INTEGER, CanPromote INTEGER, Status TEXT, Priority INTEGER,
 TopicID INTEGER, CreatedAt TEXT);
CREATE TABLE IF NOT EXISTS Paper (PaperID INTEGER PRIMARY KEY, Title TEXT,
 Abstract TEXT, PDFPath TEXT, CodeURL TEXT);
CREATE TABLE IF NOT EXISTS PaperFacet (PaperFacetID INTEGER PRIMARY KEY,
 PaperID INTEGER, NodeID INTEGER, Confidence REAL, IsHumanConfirmed INTEGER, CreatedAt TEXT,
 UNIQUE(PaperID,NodeID));
CREATE TABLE IF NOT EXISTS Association (AssocID INTEGER PRIMARY KEY, Dim TEXT,
 SourceRefType TEXT, SourceRefID INTEGER, TargetRefType TEXT, TargetRefID INTEGER,
 Degree REAL, Method TEXT, CreatedAt TEXT);
CREATE TABLE IF NOT EXISTS Evidence (EvidenceID INTEGER PRIMARY KEY, PaperID INTEGER,
 EvType TEXT, QuoteText TEXT, Locator TEXT, Extractor TEXT, Confidence REAL);
CREATE TABLE IF NOT EXISTS Fact (FactID INTEGER PRIMARY KEY, PaperID INTEGER,
 FactType TEXT, Content TEXT, EvidenceID INTEGER);
CREATE TABLE IF NOT EXISTS DecisionCard (CardID INTEGER PRIMARY KEY, PaperID INTEGER,
 OverallScore REAL, OneLinePosition TEXT);
CREATE TABLE IF NOT EXISTS ScoreBasis (BasisID INTEGER PRIMARY KEY, CardID INTEGER,
 ScoreField TEXT, EvidenceID INTEGER, Note TEXT);
"""

MAIN = '''
import json
from pathlib import Path
from paper_research.config import load_config, storage_paths
from paper_research.schema import connect
root = Path(__file__).resolve().parent.parent
cfg=load_config(); db=storage_paths()['db']
conn=connect(db)
for topic in cfg['topics']:
    if not conn.execute('SELECT 1 FROM ResearchTopic WHERE TopicName=?',(topic['name'],)).fetchone():
        conn.execute('INSERT INTO ResearchTopic (TopicName, Description, Keywords, SuccessCriteria, Priority, Status) VALUES (?,?,?,?,?,?)',(topic['name'],topic['id'],topic.get('keywords',''),topic.get('success_criteria',''),topic.get('priority',3),'active'))
conn.commit();conn.close()
marker=root/'init-count.txt'
marker.write_text(str(int(marker.read_text())+1 if marker.exists() else 1))
'''

CONFIG = '''
import json
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parent.parent
def load_config():
    return json.loads((REPO_ROOT/'config.json').read_text(encoding='utf-8'))
def storage_paths():
    cfg=load_config(); root=REPO_ROOT/'tasks'/cfg['tasks']['active_id']
    return {'db':root/'data'/'paper_research.sqlite','pdf_dir':root/'papers'}
def get_topic(cfg=None):
    return (cfg or load_config())['topics'][0]
'''

PIPELINE = '''
import json, urllib.request, urllib.parse
from datetime import datetime,timezone
from paper_research.config import load_config,storage_paths,get_topic
_cfg=load_config();DEFAULT_TOPIC=get_topic(_cfg);DB_PATH=storage_paths()['db']
WEIGHTS={'novelty':.2,'relevance':.35,'impact':.25,'repro':.15,'urgency':.05}
def now_iso(): return datetime.now(timezone.utc).isoformat()
def get_or_create_facet(conn,name,topology,role):
    row=conn.execute('SELECT FacetID FROM Facet WHERE FacetName=?',(name,)).fetchone()
    if row:return row[0]
    return conn.execute('INSERT INTO Facet (FacetName,FacetTopology,DecisionRole) VALUES (?,?,?)',(name,topology,role)).lastrowid
def ensure_node(conn,facet_id,name,parent_id,level,kind='problem'):
    row=conn.execute('SELECT NodeID FROM FacetNode WHERE FacetID=? AND NodeName=? AND ParentNodeID IS ?',(facet_id,name,parent_id)).fetchone()
    if row:return row[0]
    return conn.execute('INSERT INTO FacetNode (FacetID,NodeName,ParentNodeID,NodeLevel,NodeKind) VALUES (?,?,?,?,?)',(facet_id,name,parent_id,level,kind)).lastrowid
def http_json(url):
    with urllib.request.urlopen(url,timeout=3) as response:return json.load(response)
def fetch_openalex(query,year_from=None,max_results=20,ieee_only=True):
    return http_json(_cfg['sources']['openalex']['base_url'])['results']
def fetch_crossref_ieee(query,max_results=10):return []
def mount_facets(*args):raise RuntimeError('legacy multimodal mount must be replaced')
def heuristic_score(*args):raise RuntimeError('legacy multimodal score must be replaced')
'''


class SourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.original = self.base / "original"
        self.original.mkdir()
        package = self.original / "paper_research"
        package.mkdir()
        files = {"__init__.py": "", "__main__.py": MAIN, "config.py": CONFIG,
                 "schema.py": "import sqlite3\nfrom pathlib import Path\nDDL=" + repr(DDL) + '''
def connect(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(path);conn.row_factory=sqlite3.Row
    conn.executescript(DDL);conn.commit();return conn
''', "tasks.py": "# task runtime\n", "llm.py": "# no inline credentials\n"}
        for name, content in files.items():
            (package / name).write_text(content, encoding="utf-8")
        build = self.original / "build"
        build.mkdir()
        for name in ("retrieve_rank.py", "download_papers.py", "upstream_ops.py"):
            (build / name).write_text("# runtime component\n", encoding="utf-8")
        (build / "pipeline_core.py").write_text(PIPELINE, encoding="utf-8")
        (build / "seed_data.py").write_text("secret='must-not-copy-seed'", encoding="utf-8")
        (self.original / ".env").write_text("KEY=must-not-copy-env")
        (self.original / "debug.log").write_text("must-not-copy-log")
        (self.original / "node_modules").mkdir()
        (self.original / "node_modules" / "secret.js").write_text("must-not-copy-node")
        self.config = {
            "tasks": {"active_id": "multimodal-efficiency", "root": "tasks"},
            "storage": {"db_path": "data/paper_research.sqlite", "pdf_dir": "papers"},
            "topics": [{"id": "multimodal-efficiency", "name": "Old multimodal", "priority": 1,
                        "keywords": "multimodal inference", "lexicon": {"method": [{"pattern": "attention", "label": "旧注意力"}]}}],
            "sources": {"openalex": {"base_url": "https://api.openalex.org/works?api_key=source-secret", "api_key": "source-secret", "mailto": "research@example.test", "ieee_only": True},
                        "crossref": {"base_url": "https://api.crossref.org/works"},
                        "unpaywall": {"email": "research@example.test"}},
            "llm": {"active_provider": "legacy", "providers": {"legacy": {"api_key": "source-secret", "api_key_env": "LEGACY_SECRET"}}},
            "retrieve": {"default_top_k": 10, "default_year_from": 2020},
        }
        self.write_config()
        self.task = self.original / "tasks" / "multimodal-efficiency"
        self.db = self.task / "data" / "paper_research.sqlite"
        self.db.parent.mkdir(parents=True)
        (self.task / "task.json").write_text(json.dumps({"id":"multimodal-efficiency", "name":"Old multimodal", "topic_id":"multimodal-efficiency"}))
        pdf = self.task / "papers" / "0007.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF-1.7\nfixture\n%%EOF")
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript(DDL)
            conn.execute("INSERT INTO ResearchTopic VALUES (4, 'Old multimodal', 'Old description', 'old keywords', 'Old criteria', 1, 'active')")
            conn.execute("INSERT INTO Facet VALUES (3, 'Historical methods', 'Tree', 'original')")
            conn.execute("INSERT INTO FacetNode (NodeID,FacetID,NodeName,NodeLevel,TopicID) VALUES (21,3,'Historical node',0,4)")
            conn.execute("INSERT INTO Paper VALUES (7,'Known paper','Known abstract',?,NULL)", (str(pdf),))
            conn.execute("INSERT INTO Evidence VALUES (91,7,'abstract','Known quote','Abstract','OpenAlex',0.8)")
            conn.execute("INSERT INTO Fact VALUES (41,7,'finding','Known fact',91)")
            conn.execute("INSERT INTO DecisionCard VALUES (51,7,3.5,'Known reason')")
            conn.execute("INSERT INTO ScoreBasis VALUES (61,51,'OverallScore',91,'Known basis')")
            conn.execute("INSERT INTO PaperFacet (PaperFacetID,PaperID,NodeID) VALUES (81,7,21)")
            conn.commit()

    def write_config(self):
        (self.original / "config.json").write_text(json.dumps(self.config), encoding="utf-8")

    def hashes(self):
        return {str(p.relative_to(self.original)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.original.rglob("*") if p.is_file()}

    def prepare(self, name="new-topic", import_existing=False, title="Graph oversmoothing", description="graph neural network oversmoothing"):
        return prepare_source(self.original, self.base / name, name, title, description, import_existing=import_existing)

    def test_new_task_is_empty_has_current_scope_and_copies_no_secrets_or_old_data(self):
        before = self.hashes()
        source = self.prepare()
        self.assertEqual(source, (self.base / "new-topic").resolve())
        data = Library(source).load()
        self.assertEqual(data["papers"], [])
        self.assertEqual(data["evidence"], [])
        self.assertEqual(data["topic"]["title"], "Graph oversmoothing")
        self.assertIn("oversmoothing", data["topic"]["description"])
        self.assertTrue(data["facetNodes"])
        self.assertNotIn("Historical node", [node["title"] for node in data["facetNodes"]])
        cfg = json.loads((source / "config.json").read_text(encoding="utf-8"))
        self.assertEqual([topic["id"] for topic in cfg["topics"]], ["new-topic"])
        self.assertFalse(cfg["sources"]["openalex"]["ieee_only"])
        self.assertEqual(cfg["llm"]["providers"], {})
        self.assertNotIn("source-secret", json.dumps(cfg))
        self.assertNotIn("LEGACY_SECRET", json.dumps(cfg))
        for forbidden in (".env", "debug.log", "node_modules", "build/seed_data.py"):
            self.assertFalse((source / forbidden).exists())
        self.assertEqual(before, self.hashes())

    def test_two_tasks_have_independent_writable_databases(self):
        first, second = self.prepare("one"), self.prepare("two")
        with closing(sqlite3.connect(Library(first).load()["sourceDb"])) as conn:
            conn.execute("INSERT INTO Paper (PaperID,Title) VALUES (99,'Only task one')")
            conn.commit()
        self.assertEqual([p["id"] for p in Library(first).load()["papers"]], ["99"])
        self.assertEqual(Library(second).load()["papers"], [])
        self.assertEqual([p["id"] for p in Library(self.original).load()["papers"]], ["7"])

    def test_non_boolean_import_flag_cannot_accidentally_migrate_private_library(self):
        with self.assertRaises(ValueError):
            prepare_source(self.original, self.base / "invalid-import", "invalid-import",
                           "Title", "Description", import_existing="false")
        self.assertFalse((self.base / "invalid-import").exists())

    def test_prepared_runtime_can_seed_another_clean_topic(self):
        template = self.prepare("template", True)
        source = prepare_source(template, self.base / "from-template", "from-template",
                                "Battery research", "solid state battery energy density")
        self.assertEqual(Library(source).load()["papers"], [])
        result = self.run_code(source, '''
import sys,json
sys.path.insert(0,'build')
import pipeline_core as core
print(json.dumps(core.heuristic_score('Solid state battery energy density','',0),ensure_ascii=False))
''')
        self.assertEqual(result["SolveWhat"], "Battery research")
        self.assertEqual(result["RelevanceScore"], 5)

    def test_import_preserves_original_ids_and_rehomes_only_verified_pdf(self):
        before = self.hashes()
        source = self.prepare("migrated", True)
        data = Library(source).load()
        self.assertEqual([p["id"] for p in data["papers"]], ["7"])
        self.assertEqual(data["evidence"][0]["id"], "91")
        self.assertEqual(data["papers"][0]["facts"][0]["id"], "41")
        self.assertEqual(data["papers"][0]["scoreBasis"][0]["id"], "61")
        self.assertIn("21", [node["id"] for node in data["facetNodes"]])
        pdf = Library(source).pdf_path("7")
        self.assertTrue(pdf and pdf.is_relative_to(source))
        self.assertEqual(pdf.read_bytes(), (self.task / "papers/0007.pdf").read_bytes())
        self.assertEqual(before, self.hashes())

    def test_reuse_updates_scope_without_overwriting_data_or_destination_model_setting(self):
        source = self.prepare("reused", True)
        cfg_path = source / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["llm"] = {"providers": {"mine": {"api_key": "locally-configured-key"}}}
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        runtime_before = (source / "build/pipeline_core.py").read_bytes()
        result = prepare_source(self.original, source, "reused", "Battery research", "solid state battery energy density")
        self.assertEqual(result, source)
        data = Library(result).load()
        self.assertEqual([p["id"] for p in data["papers"]], ["7"])
        self.assertEqual(data["topic"]["title"], "Battery research")
        self.assertEqual((source / "init-count.txt").read_text(), "1")
        self.assertEqual(runtime_before, (source / "build/pipeline_core.py").read_bytes())
        self.assertEqual(json.loads(cfg_path.read_text())["llm"]["providers"]["mine"]["api_key"], "locally-configured-key")

    def test_rejects_path_traversal_original_overlap_and_foreign_existing_destination(self):
        for task_id, destination in [("../escape", self.base / "bad"), ("bad", self.original), ("bad", self.original / "new")]:
            with self.subTest(task_id=task_id, destination=destination):
                with self.assertRaises(ValueError):
                    prepare_source(self.original, destination, task_id, "Title", "Description")
        foreign = self.base / "foreign"
        foreign.mkdir();(foreign / "keep.txt").write_text("user data")
        with self.assertRaises(ValueError):
            prepare_source(self.original, foreign, "task", "Title", "Description")
        self.assertEqual((foreign / "keep.txt").read_text(), "user data")
        prepared = self.prepare("same")
        with self.assertRaises(ValueError):
            prepare_source(self.original, prepared, "different", "Title", "Description")

    def test_reuse_rejects_storage_path_escape(self):
        source = self.prepare("owned")
        cfg_path = source / "config.json"
        cfg = json.loads(cfg_path.read_text());cfg["tasks"]["root"] = str(self.original / "tasks")
        cfg_path.write_text(json.dumps(cfg))
        before = self.hashes()
        with self.assertRaises(ValueError):
            prepare_source(self.original, source, "owned", "Changed", "Changed")
        self.assertEqual(before, self.hashes())

    def run_code(self, source, code):
        result = subprocess.run([sys.executable,"-B","-X","utf8","-c",code], cwd=source,
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_managed_rules_build_current_topic_nodes_without_multimodal_bias(self):
        source = self.prepare("battery", title="Battery research", description="solid state battery energy density")
        result = self.run_code(source, '''
import sys,json
sys.path.insert(0,'build')
import pipeline_core as core
from paper_research.schema import connect
conn=connect(core.DB_PATH)
conn.execute("INSERT INTO Paper (PaperID,Title) VALUES (9,'Solid state battery energy density')")
core.mount_facets(conn,9,'Solid state battery energy density','')
conn.commit()
nodes=[r[0] for r in conn.execute('SELECT NodeName FROM FacetNode')]
scores=core.heuristic_score('Solid state battery energy density','',0)
unrelated=core.heuristic_score('Multimodal sparse attention token pruning','',0)
conn.close()
print(json.dumps({'nodes':nodes,'scores':scores,'unrelated':unrelated},ensure_ascii=False))
''')
        self.assertIn("Battery research", result["nodes"])
        self.assertFalse(any("推理效率" in node or "深度学习" in node for node in result["nodes"]))
        self.assertEqual(result["scores"]["SolveWhat"], "Battery research")
        self.assertGreater(result["scores"]["RelevanceScore"], result["unrelated"]["RelevanceScore"])
        self.assertIn("规则", result["scores"]["KeyLimitation"])

    def test_public_crossref_fallback_needs_no_key_and_accepts_non_ieee_papers(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                if self.path.startswith("/openalex"):
                    self.send_response(401);self.end_headers();return
                payload = json.dumps({"message":{"items":[{"title":["Quantum battery materials"], "DOI":"10.example/quantum", "publisher":"Nature", "container-title":["Nature Energy"], "issued":{"date-parts":[[2025]]}, "author":[], "is-referenced-by-count":4}]}}).encode()
                self.send_response(200);self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload)
            def log_message(self,*args): pass
        server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.config["sources"]["openalex"]["base_url"] = f"http://127.0.0.1:{server.server_port}/openalex"
        self.config["sources"]["crossref"]["base_url"] = f"http://127.0.0.1:{server.server_port}/crossref"
        self.write_config()
        source = self.prepare("public")
        result = self.run_code(source, '''
import sys,json
sys.path.insert(0,'build')
import pipeline_core as core
first=core.fetch_openalex('quantum battery',max_results=1,ieee_only=True)
fallback=core.fetch_crossref_ieee('quantum battery',max_results=1) if not first else []
print(json.dumps({'first':first,'fallback':fallback},ensure_ascii=False))
''')
        self.assertEqual(result["first"], [])
        self.assertEqual(result["fallback"][0]["DOI"], "10.example/quantum")
        self.assertEqual(result["fallback"][0]["Publisher"], "Nature")
        self.assertTrue(any(path.startswith("/crossref") for path,_ in requests))
        self.assertTrue(all(auth is None and "source-secret" not in path for path,auth in requests))


if __name__ == "__main__":
    unittest.main()
