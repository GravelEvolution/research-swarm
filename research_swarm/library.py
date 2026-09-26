"""Read a paper_research library without importing its mutating bootstrap code.

Only ``retrieve`` runs the original project, in a separate process.  Child logs
are deliberately not returned: upstream exceptions may contain provider keys.
The adapter never infers that an experiment has been reproduced from a score.
"""

from __future__ import annotations

from contextlib import closing
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import subprocess
import sys
import threading


_TABLES = (
    "Paper", "ResearchTopic", "Facet", "FacetNode", "PaperFacet", "Evidence",
    "Fact", "DecisionCard", "ScoreBasis", "EvidenceFlag", "RelationType",
    "RelEdge", "Association",
)
_SCORES = {
    "novelty": "NoveltyScore", "relevance": "RelevanceScore",
    "impact": "ImpactScore", "reproducibility": "ReproValue", "urgency": "Urgency",
}


def _text(value) -> str:
    return "" if value is None else str(value)


def _id(value) -> str | None:
    return None if value is None else str(value)


def _number(value, default=0.0) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _bounded(value, maximum=1.0) -> float:
    return max(0.0, min(maximum, _number(value)))


def _score(value) -> float:
    # Original DecisionCard scores are out of five, including OverallScore.
    return round(_bounded(value, 5.0) * 20, 2)


def _ordered(rows, key):
    return sorted(rows, key=lambda row: (_number(row.get(key)), _text(row.get(key))))


def _json_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ValueError(f"无法读取有效 JSON 配置：{path.name}") from None
    if not isinstance(value, dict):
        raise ValueError(f"配置须为 JSON 对象：{path.name}")
    return value


def _absolute(root: Path, value) -> Path:
    path = Path(str(value))
    return (path if path.is_absolute() else root / path).resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


class Library:
    """Adapter for a source project directory, task directory, or SQLite file."""

    def __init__(self, source: str | Path):
        self.source = Path(source).expanduser().resolve()
        self._retrieve_lock = threading.Lock()

    def _layout(self) -> dict:
        source = self.source
        is_db = source.is_file() or source.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
        start = source.parent if is_db else source
        if not start.is_dir():
            raise FileNotFoundError("论文项目目录不存在")
        root = next((p for p in (start, *start.parents)
                     if (p / "config.json").is_file()), start)
        cfg = _json_object(root / "config.json")
        task_cfg = cfg.get("tasks") if isinstance(cfg.get("tasks"), dict) else {}
        storage = cfg.get("storage") if isinstance(cfg.get("storage"), dict) else {}
        active_id = task_cfg.get("active_id")
        if active_id and (str(active_id) in {".", ".."} or
                          any(c in str(active_id) for c in ("/", "\\", ":"))):
            raise ValueError("原项目当前任务 ID 含无效路径字符")
        active_task = (_absolute(root, task_cfg.get("root") or "tasks") / str(active_id)) if active_id else None
        active_db = ((active_task / "data" / "paper_research.sqlite") if active_task else
                     _absolute(root, storage.get("db_path") or "data/paper_research.sqlite")).resolve()
        if is_db:
            db = source
            task = db.parent.parent if (db.parent.parent / "task.json").is_file() else None
        elif (source / "task.json").is_file():
            task = source
            db = (task / "data" / "paper_research.sqlite").resolve()
        else:
            task, db = active_task, active_db
            # Unconfigured legacy libraries sometimes keep their database at root.
            if not cfg and not db.is_file() and (root / "paper_research.sqlite").is_file():
                db = (root / "paper_research.sqlite").resolve()
                active_db = db
        meta = _json_object(task / "task.json") if task else {}
        selected_id = meta.get("topic_id") or (active_id if db == active_db else None)
        topics = [t for t in cfg.get("topics", []) if isinstance(t, dict)]
        configured_topic = next((t for t in topics if selected_id and
                                 str(t.get("id")) == str(selected_id)), {})
        if not configured_topic and not selected_id and topics and db == active_db:
            configured_topic = min(topics, key=lambda t: _number(t.get("priority"), 99))
        roots = []
        if task:
            roots.append((task / "papers").resolve())
        if is_db and not task:
            local_root = db.parent.parent if db.parent.name == "data" else db.parent
            roots.append((local_root / "papers").resolve())
        roots.append(_absolute(root, storage.get("pdf_dir") or "papers"))
        return {"root": root, "db": db, "activeDb": active_db, "task": task,
                "meta": meta, "configuredTopic": configured_topic,
                "pdfRoots": tuple(dict.fromkeys(roots))}

    @staticmethod
    def _read_tables(db: Path) -> tuple[dict, list[str], list[str]]:
        rows = {name: [] for name in _TABLES}
        if not db.is_file():
            return rows, list(_TABLES), ["当前课题尚无论文数据库；未创建或切换到其他库。"]
        try:
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
                existing = {r[0].lower() for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                missing = []
                for name in _TABLES:
                    if name.lower() not in existing:
                        missing.append(name)
                    else:
                        rows[name] = [dict(row) for row in connection.execute(f'SELECT * FROM "{name}"')]
        except sqlite3.DatabaseError:
            raise ValueError("无法只读打开论文数据库；请检查文件格式、权限或数据库占用。") from None
        warnings = ["原库缺少数据表：" + "、".join(missing)] if missing else []
        return rows, missing, warnings

    @staticmethod
    def _topic(layout: dict, topics: list[dict]) -> dict:
        configured = layout["configuredTopic"]
        meta = layout["meta"]
        title = _text(configured.get("name") or meta.get("name"))
        selected = next((row for row in topics if title and row.get("TopicName") == title), None)
        if selected is None and topics:
            selected = min(topics, key=lambda row: (
                row.get("Status") not in (None, "active"), _number(row.get("Priority"), 99),
                _number(row.get("TopicID"))))
        selected = selected or {}
        return {
            "id": _id(selected.get("TopicID")) or _text(configured.get("id") or meta.get("id") or "unconfigured"),
            "title": _text(selected.get("TopicName") or title or "未命名研究课题"),
            "description": _text(selected.get("Description") or meta.get("description") or configured.get("description")),
            "successCriteria": _text(selected.get("SuccessCriteria") or configured.get("success_criteria")),
        }

    @staticmethod
    def _paper_pdf(paper: dict, layout: dict) -> Path | None:
        stored = _text(paper.get("PDFPath")).strip()
        if not stored:
            return None
        # Windows basenames also work when the adapter is run on another OS.
        name = PureWindowsPath(stored).name
        if not name or PureWindowsPath(name).suffix.lower() != ".pdf":
            return None
        stem = PureWindowsPath(name).stem
        paper_id = _text(paper.get("PaperID"))
        # The source downloader uses the numeric PaperID as the filename.  Do
        # not silently serve another paper when a legacy path is misassigned.
        if stem.isdecimal() and paper_id.isdecimal() and int(stem) != int(paper_id):
            return None
        roots = layout["pdfRoots"]
        candidates = [root / name for root in roots]
        recorded = Path(stored)
        if recorded.is_absolute():
            candidates.append(recorded)
        else:
            candidates.extend((layout["root"] / recorded, layout["db"].parent / recorded))
        for candidate in candidates:
            try:
                path = candidate.resolve(strict=True)
                if path.suffix.lower() != ".pdf" or not path.is_file():
                    continue
                if not any(_inside(path, root) for root in roots):
                    continue
                with path.open("rb") as handle:
                    header = handle.read(1024)
                if b"%PDF-" in header:
                    return path
            except (OSError, ValueError, RuntimeError):
                continue
        return None

    @staticmethod
    def _materials(paper: dict, pdf: Path | None, flag: dict) -> str:
        if paper.get("CodeURL"):
            code = "代码链接已记录（未验证）"
        elif flag.get("HasCode"):
            code = "原库标记有代码（未验证）"
        else:
            code = "未记录代码链接"
        parts = ["材料状态：" + code, "本地 PDF 可用" if pdf else "未找到本地 PDF",
                 "原库标记有开放数据（未验证）" if flag.get("HasOpenData") else "开放数据未核验",
                 "未执行复现实验"]
        if flag.get("EvidenceGap"):
            parts.append("原库材料缺口：" + str(flag["EvidenceGap"]))
        return "；".join(parts)

    def load(self) -> dict:
        """Return all source records as normalized data; never initialize a DB."""
        layout = self._layout()
        tables, missing, warnings = self._read_tables(layout["db"])
        evidence = []
        for row in _ordered(tables["Evidence"], "EvidenceID"):
            locator = _text(row.get("Locator")).strip()
            if locator.lower() in {"", "unknown", "none", "null", "n/a", "?", "未定位", "未知"}:
                locator = "未定位"
            evidence.append({"id": _id(row.get("EvidenceID")), "paperId": _id(row.get("PaperID")),
                "quote": _text(row.get("QuoteText")), "locator": locator,
                "type": _text(row.get("EvType") or "unknown"),
                "confidence": _bounded(row.get("Confidence")), "extractor": _text(row.get("Extractor"))})
        facts = [{"id": _id(row.get("FactID")), "paperId": _id(row.get("PaperID")),
                  "type": _text(row.get("FactType")), "content": _text(row.get("Content")),
                  "value": None if row.get("ValueNum") is None else _number(row["ValueNum"]),
                  "unit": _text(row.get("Unit")), "evidenceId": _id(row.get("EvidenceID")),
                  "parentId": _id(row.get("ParentFactID"))}
                 for row in _ordered(tables["Fact"], "FactID")]
        score_basis = [{"id": _id(row.get("BasisID")), "cardId": _id(row.get("CardID")),
                        "field": _text(row.get("ScoreField")), "factId": _id(row.get("FactID")),
                        "associationId": _id(row.get("AssociationID")),
                        "evidenceId": _id(row.get("EvidenceID")), "weight": _number(row.get("Weight")),
                        "note": _text(row.get("Note"))}
                       for row in _ordered(tables["ScoreBasis"], "BasisID")]
        facets = {_id(row.get("FacetID")): row for row in tables["Facet"]}
        paper_links, node_links = {}, {}
        for row in _ordered(tables["PaperFacet"], "PaperFacetID"):
            paper_id, node_id = _id(row.get("PaperID")), _id(row.get("NodeID"))
            if paper_id is not None and node_id is not None:
                paper_links.setdefault(paper_id, []).append(node_id)
                node_links.setdefault(node_id, []).append(paper_id)
        nodes = []
        for row in _ordered(tables["FacetNode"], "NodeID"):
            facet_id = _id(row.get("FacetID"))
            facet = facets.get(facet_id, {})
            node_id = _id(row.get("NodeID"))
            nodes.append({"id": node_id, "parentId": _id(row.get("ParentNodeID")),
                          "title": _text(row.get("NodeName")), "facetId": facet_id,
                          "facetName": _text(facet.get("FacetName") or "未命名切面"),
                          "topology": _text(facet.get("FacetTopology") or "Unknown"),
                          "paperIds": list(dict.fromkeys(node_links.get(node_id, [])))})
        cards = {_id(row.get("PaperID")): row for row in _ordered(tables["DecisionCard"], "CardID")}
        flags = {_id(row.get("CardID")): row for row in tables["EvidenceFlag"]}
        papers = []
        for row in _ordered(tables["Paper"], "PaperID"):
            paper_id = _id(row.get("PaperID"))
            card = cards.get(paper_id, {})
            card_id = _id(card.get("CardID"))
            pdf = self._paper_pdf(row, layout)
            paper = {"id": paper_id, "title": _text(row.get("Title")),
                "abstract": _text(row.get("Abstract")), "year": row.get("Year"),
                "venue": _text(row.get("Venue")), "doi": _text(row.get("DOI")),
                "authors": _text(row.get("Authors")), "codeUrl": _text(row.get("CodeURL")),
                "pdfAvailable": pdf is not None, "pdfPath": str(pdf) if pdf else None,
                "score": _score(card.get("OverallScore")),
                "scores": {name: _score(card.get(field)) for name, field in _SCORES.items()},
                "reason": _text(card.get("OneLinePosition") or card.get("ImpactNote") or "暂无评分依据"),
                "reproducibility": self._materials(row, pdf, flags.get(card_id, {})),
                "workerStatus": "pending", "facetNodeIds": list(dict.fromkeys(paper_links.get(paper_id, []))),
                "evidenceIds": [ev["id"] for ev in evidence if ev["paperId"] == paper_id],
                "facts": [fact for fact in facts if fact["paperId"] == paper_id],
                "scoreBasis": [basis for basis in score_basis if basis["cardId"] == card_id],
                "feedback": None}
            papers.append(paper)
        relations = []
        relation_types = {_id(row.get("RelTypeID")): row for row in tables["RelationType"]}
        for row in _ordered(tables["RelEdge"], "RelationID"):
            kind = relation_types.get(_id(row.get("RelTypeID")), {})
            source_paper, target_paper = row.get("FromPaperID"), row.get("ToPaperID")
            relations.append({"id": _id(row.get("RelationID")), "kind": "network",
                "source": _id(source_paper if source_paper is not None else row.get("FromNodeID")),
                "target": _id(target_paper if target_paper is not None else row.get("ToNodeID")),
                "sourceType": "Paper" if source_paper is not None else "FacetNode",
                "targetType": "Paper" if target_paper is not None else "FacetNode",
                "relationType": _text(kind.get("TypeName") or "unknown"),
                "weight": _bounded(row.get("Weight")), "confidence": _bounded(row.get("Confidence")),
                "evidenceId": _id(row.get("EvidenceID")), "directed": bool(kind.get("IsDirected", 1)),
                "facetId": _id(kind.get("FacetID")), "method": ""})
        for row in _ordered(tables["Association"], "AssocID"):
            relations.append({"id": _id(row.get("AssocID")), "kind": "relevance",
                "source": _id(row.get("SourceRefID")), "target": _id(row.get("TargetRefID")),
                "sourceType": _text(row.get("SourceRefType")), "targetType": _text(row.get("TargetRefType")),
                "relationType": _text(row.get("Dim")), "weight": _bounded(row.get("Degree")),
                "confidence": None, "evidenceId": _id(row.get("EvidenceID")), "directed": True,
                "facetId": None, "method": _text(row.get("Method"))})
        abstract_count = sum(1 for ev in evidence if ev["type"].lower() == "abstract")
        if evidence and abstract_count == len(evidence):
            warnings.append("当前证据全部来自摘要，尚无全文定位或复现实验记录。")
        paper_ids = {paper["id"] for paper in papers}
        evidence_ids = {ev["id"] for ev in evidence}
        node_ids = {node["id"] for node in nodes}
        unknown_references = sum(ev["paperId"] not in paper_ids for ev in evidence)
        unknown_references += sum(fact["paperId"] not in paper_ids for fact in facts)
        unknown_references += sum(
            row["evidenceId"] is not None and row["evidenceId"] not in evidence_ids
            for row in facts + score_basis + relations)
        unknown_references += sum(
            node["parentId"] is not None and node["parentId"] not in node_ids for node in nodes)
        if unknown_references:
            warnings.append(f"原库有 {unknown_references} 处引用的记录不存在；已保留原始 ID，需核对关联。")
        stats = {"paperCount": len(papers), "facetNodeCount": len(nodes),
                 "evidenceCount": len(evidence), "factCount": len(facts),
                 "scoreBasisCount": len(score_basis), "relationCount": len(relations),
                 "pdfCount": sum(p["pdfAvailable"] for p in papers),
                 "abstractEvidenceCount": abstract_count, "missingTables": missing,
                 "unresolvedReferenceCount": unknown_references, "warnings": warnings}
        return {"sourcePath": str(layout["root"]), "sourceDb": str(layout["db"]),
                "topic": self._topic(layout, tables["ResearchTopic"]), "papers": papers,
                "facetNodes": nodes, "evidence": evidence, "relations": relations,
                "facts": facts, "scoreBasis": score_basis, "stats": stats}

    def pdf_path(self, paper_id: str) -> Path | None:
        """Resolve only the PDF recorded for this known paper in allowed roots."""
        for paper in self.load()["papers"]:
            if paper["id"] == str(paper_id):
                return Path(paper["pdfPath"]) if paper["pdfAvailable"] else None
        return None

    @staticmethod
    def _retrieval_index(db: Path) -> tuple[dict, dict]:
        """Read optional source retrieval records without creating schema."""
        if not db.is_file():
            return {}, {}
        try:
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
                existing = {row[0].lower() for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"retrievaltask", "rankedresult"}.issubset(existing):
                    return {}, {}
                tasks = {_id(row["TaskID"]): dict(row) for row in connection.execute(
                    "SELECT TaskID, RootNodeID, LocalOverrideJson, Status FROM RetrievalTask")}
                ranked = {}
                for row in connection.execute(
                        "SELECT TaskID, PaperID, Rank, ResultID FROM RankedResult ORDER BY Rank, ResultID"):
                    ranked.setdefault(_id(row["TaskID"]), []).append(dict(row))
                return tasks, ranked
        except sqlite3.DatabaseError:
            # A legacy/partial library may not have this optional index.  Never
            # turn an unknown match set into all library papers.
            return {}, {}

    @staticmethod
    def _matched_results(index, previous_task_ids, output, query, top_k, node_id, paper_ids) -> dict:
        result = {"sourceTaskId": None, "resultPaperIds": [], "resultLookupComplete": False}
        tasks, ranked = index
        output = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else _text(output)
        candidates = set()
        for task_id, reported_node in re.findall(
                r"(?m)^\[retrieve_rank\]\s+contract=\S+\s+task=(\d+)\s+node=(\d+)\s*$", output):
            if task_id in previous_task_ids or task_id not in tasks:
                continue
            task = tasks[task_id]
            if task.get("Status") != "done" or _id(task.get("RootNodeID")) != reported_node:
                continue
            if node_id is not None and reported_node != node_id:
                continue
            try:
                local = json.loads(task.get("LocalOverrideJson") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(local, dict) or local.get("keywords") != query or local.get("top_k") != top_k:
                continue
            candidates.add(task_id)
        if len(candidates) != 1:
            return result
        task_id = candidates.pop()
        rows = ranked.get(task_id, [])
        ids = [_id(row.get("PaperID")) for row in rows if 0 < _number(row.get("Rank")) <= top_k]
        result.update(sourceTaskId=task_id,
                      resultPaperIds=list(dict.fromkeys(paper_id for paper_id in ids if paper_id in paper_ids)),
                      resultLookupComplete=all(paper_id in paper_ids for paper_id in ids))
        return result

    def retrieve(self, query: str, top_k: int = 10, node_id: str | None = None) -> dict:
        """Explicitly invoke source CLI; return refreshed library and metadata.

        This is the only mutating operation: upstream may write papers, scores,
        PDFs, and reports to its currently active task.  It does not switch tasks.
        Raw child stdout/stderr are not returned.  Only a numeric CLI task marker
        is used to resolve this call's RankedResult rows, including existing hits.
        """
        if not isinstance(query, str) or not query.strip() or len(query) > 2000 or "\x00" in query:
            raise ValueError("检索词须为 1–2000 字符的非空文本")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("检索数量须为 1–100 的整数")
        with self._retrieve_lock:
            layout = self._layout()
            if layout["db"] != layout["activeDb"]:
                raise ValueError("此数据库不是原项目当前课题，无法安全调用原检索器。")
            if not (layout["root"] / "paper_research" / "__main__.py").is_file():
                raise ValueError("原项目缺少 paper_research 检索入口")
            before = self.load()
            previous_task_ids = set(self._retrieval_index(layout["db"])[0])
            args = [sys.executable, "-B", "-X", "utf8", "-m", "paper_research", "retrieve",
                    "--query=" + query.strip(), "--top-k", str(top_k)]
            if node_id is not None:
                node_id = str(node_id)
                if node_id not in {node["id"] for node in before["facetNodes"]} or not node_id.isdecimal():
                    raise ValueError("检索节点不是当前原库中的有效节点 ID")
                args.extend(("--node", node_id))
            topic_id = layout["configuredTopic"].get("id")
            if topic_id:
                args.extend(("--topic", str(topic_id)))
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
            try:
                completed = subprocess.run(args, cwd=layout["root"], env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
            except subprocess.TimeoutExpired:
                raise RuntimeError("原项目检索超时，进程已停止；可能已有部分记录写入，可刷新后检查。") from None
            except OSError:
                raise RuntimeError("无法启动原项目检索进程，请检查 Python 和项目路径。") from None
            if completed.returncode:
                raise RuntimeError(f"原项目检索失败（退出码 {completed.returncode}）；请检查原项目网络或配置，刷新确认已写入的记录。")
            current = self._layout()
            if current["activeDb"] != layout["activeDb"]:
                raise RuntimeError("检索期间原项目切换了当前课题；本次结果未合并，请重新确认来源。")
            after = self.load()
            previous_ids = {paper["id"] for paper in before["papers"]}
            metadata = {"query": query.strip(), "topK": top_k, "nodeId": node_id,
                        "paperCountBefore": len(before["papers"]), "paperCountAfter": len(after["papers"]),
                        "newPaperIds": [paper["id"] for paper in after["papers"] if paper["id"] not in previous_ids]}
            metadata.update(self._matched_results(
                self._retrieval_index(layout["db"]), previous_task_ids, completed.stdout,
                query.strip(), top_k, node_id, {paper["id"] for paper in after["papers"]}))
            return {"ok": True, "library": after, "retrieval": metadata,
                    "message": "已调用原项目检索器并刷新论文库。"}
