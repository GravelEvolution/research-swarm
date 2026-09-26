# -*- coding: utf-8 -*-
"""retrieve_rank/v1 executor.

Tasks (per upstream contract):
  1. retrieve — query IEEE works via OpenAlex (+ Crossref fallback)
  2. rank     — score + sort
Side effects for the decision library:
  - auto-mount tree/network/relevance facets
  - solution clusters
  - increment scan (only new papers)
  - optional child-node promotion (new search root)

CLI examples:
  python build/retrieve_rank.py --query "sparse attention multimodal" --top-k 20
  python build/retrieve_rank.py --node 4 --promote --query "video sparse attention"
  python build/retrieve_rank.py --increment --query "sparse attention multimodal"
  python build/retrieve_rank.py --report
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_core import (  # noqa: E402
    DB_PATH,
    ensure_evidence_fact,
    fetch_crossref_ieee,
    fetch_openalex,
    heuristic_score,
    link_network,
    mount_facets,
    now_iso,
    upsert_paper,
    write_decision,
)
from download_papers import download_for_work  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_research.config import load_config, storage_paths  # noqa: E402
from paper_research.schema import connect  # noqa: E402

_CFG = load_config()
_PATHS = storage_paths(_CFG)
DB_PATH = _PATHS["db"]
REPORT_PATH = _PATHS["report"]


def load_upstream(conn: sqlite3.Connection, topic_id: int | None) -> dict:
    if topic_id:
        row = conn.execute(
            "SELECT TopicID, TopicName, Keywords, SuccessCriteria FROM ResearchTopic WHERE TopicID=?",
            (topic_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT TopicID, TopicName, Keywords, SuccessCriteria FROM ResearchTopic ORDER BY Priority, TopicID LIMIT 1"
        ).fetchone()
    if not row:
        return {
            "TopicID": None,
            "TopicName": "default",
            "Keywords": "multimodal efficient inference",
            "SuccessCriteria": "可复现、成本可降、精度可控",
        }
    return {
        "TopicID": row["TopicID"],
        "TopicName": row["TopicName"],
        "Keywords": row["Keywords"] or "",
        "SuccessCriteria": row["SuccessCriteria"] or "",
    }


def ensure_contract(conn: sqlite3.Connection) -> str:
    conn.execute(
        """INSERT OR IGNORE INTO ProtocolContract
           (ContractID, Version, Name, InputSchemaJson, OutputSchemaJson, CallSignature, IsActive, CreatedAt)
           VALUES (?,?,?,?,?,?,1,?)""",
        (
            "retrieve_rank/v1",
            "1.0",
            "检索排序协议",
            json.dumps({"required": ["node_id", "upstream", "local"], "type": "object"}),
            json.dumps({"properties": {"ranked": {"type": "array"}}}),
            "retrieve_rank(node_id, upstream_req, local_override) -> ranked",
            now_iso(),
        ),
    )
    return "retrieve_rank/v1"


def promote_node(conn: sqlite3.Connection, node_id: int, reason: str, upstream: dict,
                 query: str) -> int:
    """Mark node as search root; log inheritance + promotion."""
    node = conn.execute("SELECT * FROM FacetNode WHERE NodeID=?", (node_id,)).fetchone()
    if not node:
        raise SystemExit(f"node {node_id} not found")
    conn.execute(
        "UPDATE FacetNode SET IsSearchRoot=1, Status='search_root', SearchRootSince=?, PromotedFromParentID=?, CanPromote=1 WHERE NodeID=?",
        (now_iso(), node["ParentNodeID"], node_id),
    )
    fields = [
        ("SuccessCriteria", upstream.get("SuccessCriteria", ""), False, None, None),
        ("Keywords", upstream.get("Keywords", ""), True, query, "升格后本地检索词"),
    ]
    parent_id = node["ParentNodeID"] if node["ParentNodeID"] is not None else node_id
    for field, val, over, oval, oreason in fields:
        conn.execute(
            """INSERT INTO NodeInheritance
               (ChildNodeID, ParentNodeID, InheritedField, InheritedValue, IsOverridden, OverrideValue, OverrideReason)
               VALUES (?,?,?,?,?,?,?)""",
            (node_id, parent_id, field, val, int(over), oval, oreason),
        )
    promo = conn.execute(
        """INSERT INTO NodePromotion (NodeID, FromParentNodeID, TriggerReason, InheritedBrief, ScanRunID, PromotedAt)
           VALUES (?,?,?,?,?,?)""",
        (
            node_id,
            parent_id,
            reason,
            f"inherit SuccessCriteria; local query={query}",
            None,
            now_iso(),
        ),
    ).lastrowid
    conn.execute(
        """INSERT INTO NodeOpLog (NodeID, OpType, PayloadJson, Reason, Actor, RelatedNodeID, OpAt)
           VALUES (?,?,?,?,?,?,?)""",
        (
            node_id,
            "promote",
            json.dumps({"to": "search_root", "query": query}, ensure_ascii=False),
            reason,
            "retrieve_rank",
            node["ParentNodeID"],
            now_iso(),
        ),
    )
    return promo


def run_retrieve_rank(
    query: str,
    node_id: int | None = None,
    top_k: int = 20,
    year_from: int = 2020,
    increment_only: bool = False,
    promote: bool = False,
    promote_reason: str = "子节点升格开新一轮检索",
    topic_id: int | None = None,
    use_crossref_fallback: bool = True,
) -> dict:
    conn = connect(DB_PATH)
    contract = ensure_contract(conn)
    upstream = load_upstream(conn, topic_id)
    if node_id is None:
        row = conn.execute(
            "SELECT NodeID FROM FacetNode WHERE NodeName LIKE '%效率%' OR NodeName LIKE '%稀疏%' ORDER BY NodeID LIMIT 1"
        ).fetchone()
        node_id = row["NodeID"] if row else 1

    if promote:
        promote_node(conn, node_id, promote_reason, upstream, query)

    local = {"keywords": query, "rank_by": "OverallScore", "allow_new_nodes": True, "top_k": top_k}
    task_id = conn.execute(
        """INSERT INTO RetrievalTask
           (RootNodeID, ContractID, UpstreamReqJson, LocalOverrideJson, Status,
            InheritanceCompliant, InheritanceNote, CreatedAt)
           VALUES (?,?,?,?, 'running', 1, ?, ?)""",
        (
            node_id,
            contract,
            json.dumps(upstream, ensure_ascii=False),
            json.dumps(local, ensure_ascii=False),
            f"遵从上游 SuccessCriteria；query={query}",
            now_iso(),
        ),
    ).lastrowid

    print(f"[retrieve_rank] contract={contract} task={task_id} node={node_id}")
    print(f"[upstream] {upstream['TopicName']}: {upstream['SuccessCriteria'][:80]}")
    print(f"[local] query={query!r} top_k={top_k} increment_only={increment_only}")

    papers = fetch_openalex(query, year_from=year_from, max_results=top_k, ieee_only=True)
    source = "openalex"
    if len(papers) < max(5, top_k // 3) and use_crossref_fallback:
        extra = fetch_crossref_ieee(query, max_results=max(5, top_k // 2))
        known = {p.get("DOI") for p in papers}
        for e in extra:
            if e.get("DOI") not in known:
                papers.append(e)
        source = "openalex+crossref"
    print(f"[fetch] got {len(papers)} IEEE works via {source}")

    scan_id = conn.execute(
        """INSERT INTO ScanRun (RootNodeID, OriginTopicID, QueryText, InheritedQueryParts, ScanSource, ScannedAt, NewCount)
           VALUES (?,?,?,?,?,?,0)""",
        (
            node_id,
            upstream["TopicID"],
            query,
            upstream["SuccessCriteria"][:200],
            source,
            now_iso(),
        ),
    ).lastrowid

    all_papers = [
        {"PaperID": r["PaperID"], "Title": r["Title"] or ""}
        for r in conn.execute("SELECT PaperID, Title FROM Paper")
    ]

    imported = []
    new_count = 0
    pdf_count = 0
    for p in papers:
        pid, is_new = upsert_paper(conn, p)
        # 立刻下载全文（上游命令后同步执行）
        pdf_path = download_for_work({"OAUrls": p.get("OAUrls") or [], "doi": p.get("DOI")}, pid)
        if pdf_path:
            conn.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=?", (str(pdf_path), pid))
            pdf_count += 1
        if increment_only and not is_new:
            conn.execute(
                "INSERT OR IGNORE INTO ScanHit (ScanID, PaperID, IsNew, DeltaNote) VALUES (?,?,0,?)",
                (scan_id, pid, "exists"),
            )
            continue
        ensure_evidence_fact(conn, pid, p.get("Abstract") or "")
        facets = mount_facets(conn, pid, p["Title"], p.get("Abstract") or "")
        edges = link_network(conn, pid, p["Title"], all_papers)
        scores = heuristic_score(p["Title"], p.get("Abstract") or "", p.get("CitationCount") or 0,
                                upstream.get("SuccessCriteria", ""))
        card_id = write_decision(conn, pid, scores)
        if upstream["TopicID"]:
            conn.execute(
                """INSERT OR IGNORE INTO PaperTopic (PaperID, TopicID, Relevance, MatchReason, SubDirection, CreatedAt)
                   VALUES (?,?,?,?,?,?)""",
                (
                    pid,
                    upstream["TopicID"],
                    round(scores["RelevanceScore"] / 5.0, 2),
                    f"retrieve_rank:{query}",
                    ",".join(facets.get("methods", [])[:2]) or "IEEE",
                    now_iso(),
                ),
            )
        conn.execute(
            "INSERT OR IGNORE INTO ScanHit (ScanID, PaperID, IsNew, DeltaNote) VALUES (?,?,?,?)",
            (scan_id, pid, int(is_new), "new" if is_new else "updated"),
        )
        imported.append({
            "paper_id": pid,
            "title": p["Title"],
            "score": scores["OverallScore"],
            "relation": scores["ImpactRelation"],
            "is_new": is_new,
            "edges": edges,
            "methods": facets.get("methods", []),
        })
        if is_new:
            new_count += 1
        all_papers.append({"PaperID": pid, "Title": p["Title"]})

    imported.sort(key=lambda x: -x["score"])
    conn.execute("DELETE FROM RankedResult WHERE TaskID=?", (task_id,))
    for i, row in enumerate(imported[:top_k], 1):
        conn.execute(
            """INSERT INTO RankedResult (TaskID, PaperID, Rank, Score, MatchReason, EvidenceID, SolutionApproach)
               VALUES (?,?,?,?,?,?,?)""",
            (
                task_id,
                row["paper_id"],
                i,
                row["score"],
                f"{row['relation']} | {','.join(row['methods'][:2])}",
                None,
                ",".join(row["methods"][:1]) or "auto",
            ),
        )
    conn.execute("UPDATE ScanRun SET NewCount=? WHERE ScanID=?", (new_count, scan_id))
    conn.execute(
        "UPDATE RetrievalTask SET Status='done', FinishedAt=? WHERE TaskID=?",
        (now_iso(), task_id),
    )
    # refresh cluster paper counts
    conn.execute(
        """UPDATE SolutionCluster SET PaperCount=(
             SELECT COUNT(*) FROM PaperSolution PS WHERE PS.ClusterID=SolutionCluster.ClusterID)"""
    )
    conn.commit()

    print("\n=== Ranked ===")
    for row in imported[: min(12, top_k)]:
        flag = "NEW" if row["is_new"] else "   "
        print(
            f"{flag} #{row['paper_id']:3d} {row['score']:4.2f} {row['relation']:13s} "
            f"{row['title'][:64]}"
        )
    print(f"\nnew={new_count} imported={len(imported)} pdfs={pdf_count} scan_id={scan_id} task_id={task_id}")
    conn.close()
    return {
        "task_id": task_id,
        "scan_id": scan_id,
        "new_count": new_count,
        "pdf_count": pdf_count,
        "imported": imported,
    }


def make_report(out_path: Path | None = None) -> Path:
    conn = connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    out_path = out_path or REPORT_PATH
    lines = ["# 论文决策报告（IEEE / retrieve_rank/v1）", "", f"生成时间：{now_iso()}", ""]

    topic = conn.execute("SELECT * FROM ResearchTopic ORDER BY TopicID LIMIT 1").fetchone()
    if topic:
        lines += [
            "## 上游课题",
            "",
            f"- **课题**：{topic['TopicName']}",
            f"- **关键词**：{topic['Keywords']}",
            f"- **成功标准**：{topic['SuccessCriteria']}",
            "",
        ]

    lines += ["## 决策漏斗（OverallScore 降序）", ""]
    lines.append("| 分 | N | R | I | Rep | U | 关系 | 论文 |")
    lines.append("|---:|---:|---:|---:|---:|---:|---|---|")
    for r in conn.execute(
        """SELECT C.*, P.Title, P.Year, P.Venue FROM DecisionCard C
           JOIN Paper P ON C.PaperID=P.PaperID
           ORDER BY C.OverallScore DESC, C.PaperID"""
    ):
        lines.append(
            f"| {r['OverallScore']:.2f} | {r['NoveltyScore']} | {r['RelevanceScore']} | "
            f"{r['ImpactScore']} | {r['ReproValue']} | {r['Urgency']} | "
            f"{r['ImpactRelation']} | [{r['PaperID']}] {r['Title'][:70]} |"
        )

    lines += ["", "## ImpactRelation 分布", ""]
    for r in conn.execute(
        """SELECT ImpactRelation, COUNT(*) n, ROUND(AVG(OverallScore),2) avg
           FROM DecisionCard GROUP BY ImpactRelation ORDER BY avg DESC"""
    ):
        lines.append(f"- **{r['ImpactRelation']}**：{r['n']} 篇，均分 {r['avg']}")

    lines += ["", "## 同题多解", ""]
    for r in conn.execute(
        """SELECT S.ApproachName, S.PaperCount, N.NodeName
           FROM SolutionCluster S LEFT JOIN FacetNode N ON S.NodeID=N.NodeID
           ORDER BY S.PaperCount DESC"""
    ):
        lines.append(f"- **{r['ApproachName']}**（{r['NodeName'] or '—'}）：{r['PaperCount']} 篇")

    lines += ["", "## 行动队列", ""]
    for r in conn.execute(
        """SELECT A.Priority, A.ActionType, A.ActionText, C.OverallScore, P.Title
           FROM ActionItem A
           JOIN DecisionCard C ON A.CardID=C.CardID
           JOIN Paper P ON C.PaperID=P.PaperID
           ORDER BY A.Priority, C.OverallScore DESC"""
    ):
        lines.append(f"- P{r['Priority']} **{r['ActionType']}**：{r['ActionText']}")
        lines.append(f"  - ← {r['Title'][:70]} ({r['OverallScore']:.2f})")

    lines += ["", "## 升格 / 检索根", ""]
    for r in conn.execute(
        """SELECT NodeID, NodeName, Status, IsSearchRoot FROM FacetNode
           WHERE IsSearchRoot=1 OR Status='search_root'"""
    ):
        lines.append(f"- `{r['NodeID']}` {r['NodeName']} — {r['Status']}")

    lines += ["", "## 全文 PDF", ""]
    pdf_n = conn.execute(
        "SELECT COUNT(*) AS c FROM Paper WHERE PDFPath IS NOT NULL AND PDFPath != ''"
    ).fetchone()["c"]
    tot_n = conn.execute("SELECT COUNT(*) AS c FROM Paper").fetchone()["c"]
    lines.append(f"- 已下载：**{pdf_n} / {tot_n}** 篇（`papers/*.pdf`，仅开放获取）")
    for r in conn.execute(
        "SELECT PaperID, Title, PDFPath FROM Paper WHERE PDFPath IS NOT NULL AND PDFPath != '' ORDER BY PaperID LIMIT 15"
    ):
        lines.append(f"- [{r['PaperID']}] {r['Title'][:60]} → `{Path(r['PDFPath']).name}`")

    lines += ["", "## 最近扫描", ""]
    for r in conn.execute(
        "SELECT ScanID, QueryText, ScanSource, NewCount, ScannedAt FROM ScanRun ORDER BY ScanID DESC LIMIT 5"
    ):
        lines.append(
            f"- Scan#{r['ScanID']} new={r['NewCount']} src={r['ScanSource']} "
            f"q=`{r['QueryText']}` @ {r['ScannedAt'][:10]}"
        )

    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    conn.close()
    print(f"report -> {out_path}")
    return out_path


def rescore_all() -> None:
    """Re-apply heuristic scores to every paper in the library."""
    conn = connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT PaperID, Title, Abstract, CitationCount FROM Paper"
    ).fetchall()
    for r in rows:
        scores = heuristic_score(
            r["Title"] or "",
            r["Abstract"] or "",
            r["CitationCount"] or 0,
        )
        write_decision(conn, r["PaperID"], scores)
        mount_facets(conn, r["PaperID"], r["Title"] or "", r["Abstract"] or "")
    # refresh ranked results for latest task
    task = conn.execute("SELECT MAX(TaskID) AS t FROM RetrievalTask").fetchone()["t"]
    if task:
        conn.execute("DELETE FROM RankedResult WHERE TaskID=?", (task,))
        ranked = conn.execute(
            """SELECT C.PaperID, C.OverallScore, C.ImpactRelation
               FROM DecisionCard C ORDER BY C.OverallScore DESC"""
        ).fetchall()
        for i, row in enumerate(ranked, 1):
            conn.execute(
                """INSERT INTO RankedResult (TaskID, PaperID, Rank, Score, MatchReason, SolutionApproach)
                   VALUES (?,?,?,?,?, 'rescore')""",
                (task, row["PaperID"], i, row["OverallScore"], row["ImpactRelation"]),
            )
    conn.commit()
    conn.close()
    print(f"rescored {len(rows)} papers")


def main() -> None:
    ap = argparse.ArgumentParser(description="retrieve_rank/v1 executor")
    ap.add_argument("--query", default="multimodal efficient inference attention token")
    ap.add_argument("--node", type=int, default=None, help="RootNodeID (search root)")
    ap.add_argument("--topic", type=int, default=None)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--year-from", type=int, default=2020)
    ap.add_argument("--increment", action="store_true", help="only score NEW papers")
    ap.add_argument("--promote", action="store_true", help="promote node to search root first")
    ap.add_argument("--promote-reason", default="子节点升格开新一轮检索")
    ap.add_argument("--report", action="store_true", help="write decision_report.md and exit")
    ap.add_argument("--rescore", action="store_true", help="re-score existing papers only")
    ap.add_argument("--no-crossref", action="store_true")
    args = ap.parse_args()

    if args.report:
        make_report()
        return
    if args.rescore:
        rescore_all()
        make_report()
        return

    run_retrieve_rank(
        query=args.query,
        node_id=args.node,
        top_k=args.top_k,
        year_from=args.year_from,
        increment_only=args.increment,
        promote=args.promote,
        promote_reason=args.promote_reason,
        topic_id=args.topic,
        use_crossref_fallback=not args.no_crossref,
    )
    make_report()


if __name__ == "__main__":
    main()
