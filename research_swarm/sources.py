"""Prepare a separate, key-free literature runtime for one research task.

Only allowlisted Python runtime files and public-source settings are copied.
Every destination has its own config, topic, SQLite DB, PDFs, and retrieval root.
Existing destinations are owned by a manifest and never reinitialized. Models
configured later in a destination are preserved; original provider keys are not.

The managed copy installs a narrow rules adapter: current-topic keywords replace
the old multimodal scoring/tree bias, public OpenAlex/Crossref/arXiv retrieval
requires no model key, and automatic PDF download is disabled. These rules only
rank and organize records; they do not assess scientific novelty or reproduction.
Public APIs may be unavailable or return incomplete abstracts. Such records keep
missing evidence rather than receiving synthetic quotes. Historical imported
scores/facets retain their original provenance; later retrieval uses task rules.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .library import Library


_LOCK = threading.RLock()
_MANIFEST = ".research-swarm-source.json"
_VERSION = 1
_RUNTIME_SUFFIX = "\n# Managed task scope; original source is untouched.\nfrom swarm_task_scope import install as _swarm_install\n_swarm_install(globals())\n"
_RUNTIME_FILES = (
    "paper_research/__init__.py", "paper_research/__main__.py",
    "paper_research/config.py", "paper_research/schema.py", "paper_research/tasks.py",
    "paper_research/llm.py", "build/pipeline_core.py", "build/retrieve_rank.py",
    "build/download_papers.py", "build/upstream_ops.py",
)
_WEIGHTS = {"novelty": .2, "relevance": .35, "impact": .25, "repro": .15, "urgency": .05}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ValueError(f"无法读取课题配置：{path.name}") from None
    if not isinstance(value, dict):
        raise ValueError(f"课题配置不是对象：{path.name}")
    return value


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _terms(title: str, description: str) -> list[str]:
    stop = set("the and for with from this that into about study research task investigate improve current requirements acceptance constraints goal please using new 方法 研究 课题 需求 目标 约束 验收 标准".split())
    result = []
    for value in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,20}", title + " " + description):
        value = value.lower()
        if value not in stop and value not in result:
            result.append(value)
    return result[:32] or [title.strip()]


def _topic(task_id: str, title: str, description: str) -> dict:
    terms = _terms(title, description)
    return {"id": task_id, "name": title, "description": description,
            "keywords": ", ".join(terms), "success_criteria": description or f"围绕 {title} 检索资料并核对证据。",
            "priority": 0, "parent_id": None, "queries": [" ".join(terms[:12])],
            "lexicon": {"method": [{"pattern": re.escape(term), "label": term} for term in terms],
                        "task": [], "solution": []}}


def _secret_values(value) -> set[str]:
    secrets = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower().replace("-", "_") in {"api_key", "apikey", "token", "access_token", "secret", "password", "authorization"} and isinstance(item, str) and item:
                secrets.add(item)
            secrets.update(_secret_values(item))
    elif isinstance(value, list):
        for item in value:
            secrets.update(_secret_values(item))
    return secrets


def _public_url(value, default: str, secrets: set[str]) -> str:
    if not isinstance(value, str):
        return default
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return default
        if any(secret in parsed.path for secret in secrets):
            return default
        hostname = parsed.hostname
        if ":" in hostname:
            hostname = "[" + hostname + "]"
        host = hostname + (":" + str(parsed.port) if parsed.port else "")
        query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
                 if not any(word in key.lower() for word in ("key", "token", "secret", "password", "auth"))
                 and not any(secret in val for secret in secrets)]
        return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), ""))
    except ValueError:
        return default


def _new_config(original: dict, task_id: str, title: str, description: str) -> dict:
    sources = original.get("sources") if isinstance(original.get("sources"), dict) else {}
    secrets = _secret_values(original)
    public = {}
    defaults = {"openalex": "https://api.openalex.org/works", "crossref": "https://api.crossref.org/works",
                "arxiv": "https://export.arxiv.org/api/query"}
    for name, default in defaults.items():
        item = sources.get(name) if isinstance(sources.get(name), dict) else {}
        public[name] = {"enabled": True, "base_url": _public_url(item.get("base_url"), default, secrets)}
    contact = (sources.get("openalex") or {}).get("mailto", "")
    contact = contact if isinstance(contact, str) and "@" in contact and not any(s in contact for s in secrets) else ""
    public["openalex"].update(mailto=contact, ieee_only=False)
    public["crossref"]["fallback_when_sparse"] = True
    public["unpaywall"] = {"enabled": False, "email": contact}
    raw_weights = (original.get("scoring") or {}).get("weights") or {}
    weights = {}
    for name, default in _WEIGHTS.items():
        try:
            value = float(raw_weights.get(name, default))
        except (TypeError, ValueError):
            value = default
        weights[name] = value if math.isfinite(value) and value >= 0 else default
    total = sum(weights.values())
    weights = {name: value / total for name, value in weights.items()} if total else dict(_WEIGHTS)
    return {"project": {"name": title, "description": description, "version": "research-swarm-source/1"},
            "tasks": {"active_id": task_id, "root": "tasks"},
            "storage": {"db_path": "data/paper_research.sqlite", "pdf_dir": "papers",
                        "report_path": "data/decision_report.md", "structure_export": "exports/structure_export.json"},
            "sources": public, "scoring": {"weights": weights},
            "llm": {"active_provider": None, "active_model": None, "providers": {}},
            "retrieve": {"default_top_k": 10, "default_year_from": 2020, "download_pdfs_on_ingest": False},
            "topics": [_topic(task_id, title, description)]}


def _paths(root: Path, task_id: str) -> dict:
    task = root / "tasks" / task_id
    return {"task": task, "db": task / "data" / "paper_research.sqlite",
            "pdf": task / "papers", "meta": task / "task.json"}


def _check_owned_path(path: Path, root: Path) -> None:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("课题目录中的路径越界，已停止读取和更新")


def _task_meta(task_id, title, description) -> dict:
    prefix = f"tasks/{task_id}"
    return {"id": task_id, "name": title, "topic_id": task_id, "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(), "config_overrides": {},
            "paths": {"db": prefix + "/data/paper_research.sqlite", "pdf_dir": prefix + "/papers",
                      "report": prefix + "/data/decision_report.md", "structure": prefix + "/exports/structure_export.json"}}


def _scope_database(db: Path, topic: dict, primary_id=None, root_id=None) -> tuple[str, str]:
    with closing(sqlite3.connect(db)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            row = connection.execute("SELECT TopicID FROM ResearchTopic WHERE TopicID=?", (primary_id,)).fetchone() if primary_id else None
            if row is None:
                row = connection.execute("SELECT TopicID FROM ResearchTopic ORDER BY Priority, TopicID LIMIT 1").fetchone()
            if row is None:
                primary_id = connection.execute("INSERT INTO ResearchTopic (TopicName) VALUES (?)", (topic["name"],)).lastrowid
            else:
                primary_id = row[0]
            connection.execute("UPDATE ResearchTopic SET Priority=MAX(COALESCE(Priority,3),1) WHERE TopicID<>?", (primary_id,))
            connection.execute("UPDATE ResearchTopic SET TopicName=?,Description=?,Keywords=?,SuccessCriteria=?,Priority=0,Status='active' WHERE TopicID=?",
                               (topic["name"], topic["description"], topic["keywords"], topic["success_criteria"], primary_id))
            facet = connection.execute("SELECT FacetID FROM Facet WHERE FacetName='研究主题' AND FacetTopology='Tree'").fetchone()
            facet_id = facet[0] if facet else connection.execute("INSERT INTO Facet (FacetName,FacetTopology,DecisionRole) VALUES ('研究主题','Tree','当前课题范围')").lastrowid
            root = connection.execute("SELECT NodeID FROM FacetNode WHERE NodeID=?", (root_id,)).fetchone() if root_id else None
            if root is None:
                root_id = connection.execute("""INSERT INTO FacetNode
                    (FacetID,NodeName,Description,ParentNodeID,NodeLevel,NodeKind,IsSearchRoot,CanPromote,Status,Priority,TopicID,CreatedAt)
                    VALUES (?,?,?,NULL,0,'research',1,1,'search_root',0,?,?)""",
                    (facet_id, topic["name"], topic["description"], primary_id, datetime.now(timezone.utc).isoformat())).lastrowid
            else:
                root_id = root[0]
            connection.execute("UPDATE FacetNode SET NodeName=?,Description=?,Path=?,TopicID=? WHERE NodeID=?",
                               (topic["name"], topic["description"], f"/{root_id}/", primary_id, root_id))
    return str(primary_id), str(root_id)


def _install_runtime(original: Path, destination: Path) -> None:
    for relative in _RUNTIME_FILES:
        source = original / relative
        _check_owned_path(source, original)
        if not source.is_file():
            raise ValueError(f"原检索运行时缺少文件：{relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (destination / "build" / "swarm_task_scope.py").write_text(_SCOPE_SHIM, encoding="utf-8")
    pipeline = destination / "build" / "pipeline_core.py"
    text = pipeline.read_text(encoding="utf-8-sig")
    text = text.replace('(pid, "openalex", key,', '(pid, paper.get("SourceType", "openalex"), key,')
    text = text.replace(_RUNTIME_SUFFIX, "") + _RUNTIME_SUFFIX
    pipeline.write_text(text, encoding="utf-8")
    retrieve = destination / "build" / "retrieve_rank.py"
    text = retrieve.read_text(encoding="utf-8-sig")
    text = re.sub(r"(?m)^(\s*)pdf_path = (download_for_work\(.+\))$",
                  r'\1pdf_path = \2 if _CFG.get("retrieve", {}).get("download_pdfs_on_ingest", False) else None', text)
    text = text.replace("IEEE works via", "works via").replace("（IEEE / retrieve_rank/v1）", "（当前课题 / retrieve_rank/v1）")
    fetch_prefix = '    papers = papers[:top_k]\n    source = "+".join(sorted({p.get("SourceType", "openalex") for p in papers})) or "public-sources"\n'
    text = text.replace(fetch_prefix, "").replace('    print(f"[fetch] got', fetch_prefix + '    print(f"[fetch] got')
    text = text.replace("SELECT NodeID FROM FacetNode WHERE NodeName LIKE '%效率%' OR NodeName LIKE '%稀疏%' ORDER BY NodeID LIMIT 1",
                        "SELECT NodeID FROM FacetNode WHERE IsSearchRoot=1 ORDER BY Priority, NodeID LIMIT 1")
    retrieve.write_text(text, encoding="utf-8")


def _initialize(root: Path) -> None:
    try:
        completed = subprocess.run([sys.executable, "-B", "-X", "utf8", "-m", "paper_research", "init"],
            cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("无法在独立目录初始化课题检索库") from None
    if completed.returncode:
        raise RuntimeError(f"独立课题初始化失败（退出码 {completed.returncode}），原资料未修改。")


def _reuse(destination: Path, task_id: str, title: str, description: str) -> Path:
    manifest_path = destination / _MANIFEST
    paths = _paths(destination, task_id)
    for path in (manifest_path, destination / "config.json", paths["meta"], paths["db"], paths["pdf"]):
        _check_owned_path(path, destination)
    manifest = _read_json(manifest_path)
    if manifest.get("version") != _VERSION or manifest.get("taskId") != task_id:
        raise ValueError("目标目录属于另一课题或使用不兼容的检索运行时，未覆盖资料")
    cfg = _read_json(destination / "config.json")
    if cfg.get("tasks") != {"active_id": task_id, "root": "tasks"}:
        raise ValueError("目标课题的当前任务或存储路径已改变，未覆盖资料")
    for value in (cfg.get("storage") or {}).values():
        if isinstance(value, str):
            _check_owned_path(destination / value, destination)
    if not paths["db"].is_file() or not paths["meta"].is_file():
        raise ValueError("目标课题的资料库或元数据缺失，未自动重建")
    for relative in _RUNTIME_FILES + ("build/swarm_task_scope.py",):
        _check_owned_path(destination / relative, destination)
        if not (destination / relative).is_file():
            raise ValueError("目标课题的检索代码不完整，未覆盖资料")
    topic = _topic(task_id, title, description)
    primary, root = _scope_database(paths["db"], topic, manifest.get("primaryTopicId"), manifest.get("rootNodeId"))
    cfg["topics"] = [topic]
    cfg.setdefault("project", {}).update(name=title, description=description)
    meta = _read_json(paths["meta"])
    meta.update(id=task_id, name=title, topic_id=task_id, description=description)
    _write_json(destination / "config.json", cfg)
    _write_json(paths["meta"], meta)
    manifest.update(primaryTopicId=primary, rootNodeId=root)
    _write_json(manifest_path, manifest)
    return destination


def prepare_source(original: Path, destination: Path, task_id: str, title: str,
                   description: str, *, import_existing: bool = False) -> Path:
    """Create or reuse one independent literature workspace, without secrets.

    ``import_existing=True`` is used only on initial creation. Reusing a managed
    destination updates its current title/description and preserves its data.
    The original project is never imported or executed by this process.
    """
    original, destination = Path(original).resolve(), Path(destination).resolve()
    if not isinstance(import_existing, bool):
        raise ValueError("是否迁入现有资料必须为布尔值")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", task_id):
        raise ValueError("课题 ID 只能包含字母、数字、下划线和短横线，最长 80 字符")
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError("课题标题须为 1–300 字符的非空文本")
    if not isinstance(description, str) or len(description) > 200000:
        raise ValueError("课题描述须为文本，最多 200000 字符")
    if original == destination or destination.is_relative_to(original) or original.is_relative_to(destination):
        raise ValueError("独立检索目录不能覆盖原项目或位于原项目内部")
    title, description = title.strip(), description.strip()
    with _LOCK:
        if destination.exists():
            if not destination.is_dir():
                raise ValueError("检索目标不是目录")
            if (destination / _MANIFEST).is_file():
                return _reuse(destination, task_id, title, description)
            if any(destination.iterdir()):
                raise ValueError("目标目录已有非本课题管理的文件，未覆盖资料")
        if not original.is_dir():
            raise ValueError("原论文检索程序目录不存在")
        original_cfg = _read_json(original / "config.json")
        cfg = _new_config(original_cfg, task_id, title, description)
        imported = Library(original).load() if import_existing else None
        if imported:
            _check_owned_path(Path(imported["sourceDb"]), original)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".research-source-", dir=destination.parent) as temporary:
            stage = Path(temporary).resolve()
            if stage.parent != destination.parent:
                raise ValueError("临时检索目录越界")
            _install_runtime(original, stage)
            paths = _paths(stage, task_id)
            paths["db"].parent.mkdir(parents=True)
            paths["pdf"].mkdir()
            (paths["task"] / "exports").mkdir()
            _write_json(stage / "config.json", cfg)
            _write_json(paths["meta"], _task_meta(task_id, title, description))
            _initialize(stage)
            primary = None
            copied_pdfs = 0
            if imported and Path(imported["sourceDb"]).is_file() and "Paper" not in imported["stats"]["missingTables"]:
                with closing(sqlite3.connect(Path(imported["sourceDb"]).as_uri() + "?mode=ro", uri=True)) as source_connection:
                    with closing(sqlite3.connect(paths["db"])) as target_connection:
                        source_connection.backup(target_connection)
                primary = imported["topic"]["id"]
                with closing(sqlite3.connect(paths["db"])) as connection:
                    with connection:
                        connection.execute("UPDATE Paper SET PDFPath=NULL")
                        for paper in imported["papers"]:
                            if not paper.get("pdfAvailable") or not paper.get("pdfPath"):
                                continue
                            paper_id = paper["id"]
                            filename = f"{int(paper_id):04d}.pdf" if paper_id.isdecimal() else hashlib.sha256(paper_id.encode()).hexdigest()[:16] + ".pdf"
                            shutil.copyfile(paper["pdfPath"], paths["pdf"] / filename)
                            final_path = _paths(destination, task_id)["pdf"] / filename
                            connection.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=?", (str(final_path), paper_id))
                            copied_pdfs += 1
            primary, root = _scope_database(paths["db"], cfg["topics"][0], primary)
            _write_json(stage / _MANIFEST, {"version": _VERSION, "taskId": task_id,
                "primaryTopicId": primary, "rootNodeId": root, "importedExisting": bool(imported),
                "importedPaperCount": len(imported["papers"]) if imported else 0,
                "copiedPdfCount": copied_pdfs, "rules": "current-topic-keyword-ranking-not-scientific-validation"})
            if destination.exists():
                if any(destination.iterdir()):
                    raise ValueError("准备期间目标目录被写入，未覆盖资料")
                destination.rmdir()
            stage.rename(destination)
        return destination


_SCOPE_SHIM = r'''"""Managed task rules; public retrieval never requires a model API key."""
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def install(core):
    cfg = core['_cfg']
    topic = cfg['topics'][0]
    terms = [v.strip().lower() for v in topic.get('keywords','').split(',') if v.strip()]
    original_openalex = core['fetch_openalex']

    def public_json(url):
        request = urllib.request.Request(url, headers={'User-Agent':'research-swarm/1.0 (public literature retrieval)'})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))

    def openalex(query, year_from=None, max_results=20, ieee_only=False):
        try:
            papers = original_openalex(query, year_from=year_from, max_results=max_results, ieee_only=False)
            for paper in papers:
                paper['SourceType'] = 'openalex'
            return papers
        except Exception:
            print('[public-search] OpenAlex unavailable; trying key-free public fallback.')
            return []

    def arxiv(query, max_results):
        base = cfg['sources'].get('arxiv',{}).get('base_url','https://export.arxiv.org/api/query')
        words = re.findall(r'[A-Za-z0-9_-]+',query)[:12]
        params = {'search_query':' AND '.join('all:'+word for word in words),'start':0,'max_results':max_results,'sortBy':'relevance'}
        request = urllib.request.Request(base+'?'+urllib.parse.urlencode(params),headers={'User-Agent':'research-swarm/1.0'})
        with urllib.request.urlopen(request,timeout=20) as response:
            root = ET.fromstring(response.read())
        ns = {'a':'http://www.w3.org/2005/Atom','x':'http://arxiv.org/schemas/atom'}
        papers = []
        for entry in root.findall('a:entry',ns):
            url = entry.findtext('a:id','',ns)
            published = entry.findtext('a:published','',ns)
            papers.append({'Title':' '.join(entry.findtext('a:title','',ns).split()),
                'Abstract':' '.join(entry.findtext('a:summary','',ns).split()),
                'Year':int(published[:4]) if published[:4].isdigit() else None,
                'Venue':'arXiv','DOI':entry.findtext('x:doi',None,ns),'OpenAlexId':url,
                'Authors':'; '.join(a.findtext('a:name','',ns) for a in entry.findall('a:author',ns)),
                'CitationCount':0,'Publisher':'arXiv','URL':url,'OAUrls':[], 'SourceType':'arxiv'})
        return papers

    def crossref(query, max_results=10):
        base = cfg['sources']['crossref']['base_url']
        params = {'query':query,'rows':min(max_results,100)}
        contact = cfg['sources'].get('openalex',{}).get('mailto')
        if contact: params['mailto'] = contact
        try:
            data = public_json(base+('&' if '?' in base else '?')+urllib.parse.urlencode(params))
            papers = []
            for item in data.get('message',{}).get('items',[]):
                doi = item.get('DOI')
                year = None
                for key in ('published-print','published-online','issued'):
                    parts = ((item.get(key) or {}).get('date-parts') or [[None]])[0]
                    if parts and parts[0]: year=parts[0];break
                papers.append({'Title':' '.join(item.get('title') or ['(untitled)']),
                    'Abstract':re.sub(r'<[^>]+>',' ',item.get('abstract') or '').strip(),
                    'Year':year,'Venue':' '.join(item.get('container-title') or []),'DOI':doi,
                    'OpenAlexId':'https://doi.org/'+doi if doi else item.get('URL'),
                    'Authors':'; '.join((a.get('given','')+' '+a.get('family','')).strip() for a in item.get('author',[])[:20]),
                    'CitationCount':item.get('is-referenced-by-count') or 0,'Publisher':item.get('publisher') or '',
                    'URL':item.get('URL') or ('https://doi.org/'+doi if doi else ''),'OAUrls':[], 'SourceType':'crossref'})
            if papers: return papers[:max_results]
        except Exception:
            print('[public-search] Crossref unavailable; trying key-free arXiv.')
        try:
            return arxiv(query,max_results)
        except Exception:
            raise RuntimeError('公开论文源当前不可用（OpenAlex/Crossref/arXiv）；不需要额外 API key，可稍后重试。') from None

    def matched_terms(title, abstract):
        text = (title+' '+abstract).lower()
        return [term for term in terms if term in text]

    def mount(conn,paper_id,title,abstract):
        facet = core['get_or_create_facet'](conn,'研究主题','Tree','当前课题范围')
        root = core['ensure_node'](conn,facet,topic['name'],None,0,'research')
        hits = matched_terms(title,abstract)
        nodes = [root]
        for term in hits[:8]:
            nodes.append(core['ensure_node'](conn,facet,term,root,1,'keyword'))
        for node in nodes:
            conn.execute('INSERT OR IGNORE INTO PaperFacet (PaperID,NodeID,Confidence,IsHumanConfirmed,CreatedAt) VALUES (?,?,?,0,?)',(paper_id,node,0.5,core['now_iso']()))
        selected = conn.execute('SELECT TopicID FROM ResearchTopic WHERE TopicName=? ORDER BY Priority,TopicID LIMIT 1',(topic['name'],)).fetchone()
        if selected:
            degree = min(1.0,len(hits)/max(1,len(terms)))
            conn.execute("INSERT INTO Association (Dim,SourceRefType,SourceRefID,TargetRefType,TargetRefID,Degree,Method,CreatedAt) VALUES ('topic_relevance','Paper',?,'ResearchTopic',?,?,'task_keyword_overlap',?)",(paper_id,selected[0],degree,core['now_iso']()))
        return {'methods':hits[:4],'tasks':[topic['name']],'solutions':[]}

    def score(title,abstract,citations,success=''):
        hits = matched_terms(title,abstract)
        coverage = len(hits)/max(1,len(terms))
        relevance = 5 if coverage>=.5 else 4 if coverage>=.25 else 3 if hits else 1
        impact = 5 if citations>=800 else 4 if citations>=400 else 3 if citations>=100 else 2 if citations>0 else 1
        values = {'NoveltyScore':3,'RelevanceScore':relevance,'ImpactScore':impact,'ReproValue':3,'Urgency':3}
        weights = core['WEIGHTS']
        overall = round(weights['novelty']*3+weights['relevance']*relevance+weights['impact']*impact+weights['repro']*3+weights['urgency']*3,2)
        return dict(values, OverallScore=overall, ImpactRelation='borrowable' if hits else 'unrelated',
            OneLinePosition=(abstract or title)[:160].replace('\n',' '),SolveWhat=topic['name'],
            ImpactNote='当前课题关键词与引文数量启发式排序；新颖性、复现价值和紧迫性未核验。',
            KeyAdvantage='当前课题关键词命中 '+str(len(hits))+'/'+str(len(terms))+'；citations='+str(citations),
            KeyLimitation='规则排序，不代表模型研究或实验证实；未核验项使用中性分。',
            DecisionConfidence=.35 if abstract else .2)

    core.update(fetch_openalex=openalex,fetch_crossref_ieee=crossref,mount_facets=mount,heuristic_score=score)
'''
