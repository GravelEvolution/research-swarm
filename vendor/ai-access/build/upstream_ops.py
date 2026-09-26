# -*- coding: utf-8 -*-
"""Upstream structure commands — parent system can edit the tree and spawn searches.

Commands (CLI / importable):
  subdivide   在节点下细分子节点
  move        更改节点位置（换父节点 / 改层级）
  search      以新课题或小问题为节点，立刻检索（可升格为检索根）
  relate      在某一节点建立新的关联（论文×论文 / 论文×节点 / 节点×节点）
  tree        打印当前树
  export      导出结构 JSON 供上游查看/编辑

All ops log to NodeOpLog (有据可依).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_research.config import storage_paths  # noqa: E402
from pipeline_core import now_iso  # noqa: E402
from retrieve_rank import make_report, run_retrieve_rank  # noqa: E402
from paper_research.schema import connect  # noqa: E402

DB_PATH = storage_paths()["db"]


def _log(conn, node_id: int, op: str, payload: dict, reason: str, related: int | None = None) -> None:
    conn.execute(
        """INSERT INTO NodeOpLog (NodeID, OpType, PayloadJson, Reason, Actor, RelatedNodeID, OpAt)
           VALUES (?,?,?,?,?,?,?)""",
        (node_id, op, json.dumps(payload, ensure_ascii=False), reason, "upstream", related, now_iso()),
    )


def _get_node(conn, node_id: int):
    return conn.execute("SELECT * FROM FacetNode WHERE NodeID=?", (node_id,)).fetchone()


def cmd_subdivide(parent_id: int, names: list[str], kind: str = "problem",
                  topic_id: int | None = None) -> list[int]:
    """上游自行细分：在 parent 下按 names 建子节点。"""
    conn = connect(DB_PATH)
    conn.row_factory = __import__("sqlite3").Row
    parent = _get_node(conn, parent_id)
    if not parent:
        raise SystemExit(f"node {parent_id} not found")
    ids = []
    for i, name in enumerate(names, 1):
        name = name.strip()
        if not name:
            continue
        exist = conn.execute(
            "SELECT NodeID FROM FacetNode WHERE FacetID=? AND NodeName=? AND ParentNodeID=?",
            (parent["FacetID"], name, parent_id),
        ).fetchone()
        if exist:
            ids.append(exist["NodeID"])
            continue
        cur = conn.execute(
            """INSERT INTO FacetNode
               (FacetID, NodeName, Description, ParentNodeID, NodeLevel, Path, NodeKind, Status, CanPromote, TopicID, CreatedAt)
               VALUES (?,?,?,?,?,?,?, 'active', 1, ?, ?)""",
            (
                parent["FacetID"], name, f"upstream subdivide under {parent['NodeName']}",
                parent_id, (parent["NodeLevel"] or 0) + 1, parent["Path"] or f"/{parent_id}/",
                kind, topic_id or parent["TopicID"], now_iso(),
            ),
        )
        nid = cur.lastrowid
        conn.execute("UPDATE FacetNode SET Path=? WHERE NodeID=?", (f"{parent['Path'] or f'/{parent_id}/'}{nid}/", nid))
        _log(conn, parent_id, "expand", {"child": name, "child_id": nid}, f"upstream subdivide x{len(names)}", nid)
        ids.append(nid)
    # single-child compression hint
    kids = conn.execute(
        "SELECT COUNT(*) AS c FROM FacetNode WHERE ParentNodeID=?", (parent_id,)
    ).fetchone()["c"]
    if kids == 1:
        print(f"hint: parent {parent_id} has only 1 child (单一子节点) — 可再细分或合并")
    conn.commit()
    conn.close()
    print(f"subdivide under {parent_id}: {list(zip(names, ids))}")
    return ids


def cmd_move(node_id: int, new_parent_id: int | None, reason: str = "upstream reposition") -> None:
    """更改项目位置：换父节点并重算 level/path。"""
    conn = connect(DB_PATH)
    conn.row_factory = __import__("sqlite3").Row
    node = _get_node(conn, node_id)
    if not node:
        raise SystemExit(f"node {node_id} not found")
    old_parent = node["ParentNodeID"]
    if new_parent_id is not None:
        parent = _get_node(conn, new_parent_id)
        if not parent:
            raise SystemExit(f"new parent {new_parent_id} not found")
        # prevent cycles
        walk = parent
        while walk:
            if walk["NodeID"] == node_id:
                raise SystemExit("move would create a cycle")
            walk = _get_node(conn, walk["ParentNodeID"]) if walk["ParentNodeID"] else None
        level = (parent["NodeLevel"] or 0) + 1
        path_prefix = parent["Path"] or f"/{new_parent_id}/"
    else:
        level = 0
        path_prefix = "/"
        new_parent_id = None
    new_path = f"{path_prefix}{node_id}/"
    conn.execute(
        "UPDATE FacetNode SET ParentNodeID=?, NodeLevel=?, Path=? WHERE NodeID=?",
        (new_parent_id, level, new_path, node_id),
    )
    # descendants path rewrite
    for child in conn.execute(
        "SELECT NodeID, Path FROM FacetNode WHERE Path LIKE ?", (f"{node['Path'] or ''}%",)
    ):
        if child["NodeID"] == node_id:
            continue
        old_p = child["Path"] or ""
        rel = old_p[len(node["Path"] or "") :] if (node["Path"] and old_p.startswith(node["Path"])) else old_p.lstrip("/")
        conn.execute(
            "UPDATE FacetNode SET Path=? WHERE NodeID=?",
            (new_path + rel if rel else f"{new_path}{child['NodeID']}/", child["NodeID"]),
        )
    _log(conn, node_id, "move",
         {"from_parent": old_parent, "to_parent": new_parent_id, "path": new_path}, reason)
    conn.commit()
    conn.close()
    print(f"moved node {node_id}: parent {old_parent} -> {new_parent_id}, path={new_path}")


def cmd_search_node(name: str, query: str, parent_id: int | None = None,
                    topic_id: int | None = None, top_k: int = 15,
                    year_from: int = 2020, promote: bool = True,
                    success: str | None = None) -> dict:
    """以新课题或小问题为节点建立检索根，并立刻搜索。"""
    conn = connect(DB_PATH)
    conn.row_factory = __import__("sqlite3").Row
    # default facet: 方法树
    facet = conn.execute("SELECT FacetID FROM Facet ORDER BY FacetID LIMIT 1").fetchone()
    facet_id = facet["FacetID"] if facet else 1
    parent = _get_node(conn, parent_id) if parent_id else None
    exist = conn.execute(
        "SELECT NodeID FROM FacetNode WHERE NodeName=? AND (ParentNodeID IS ? OR ParentNodeID=?)",
        (name, parent_id, parent_id if parent_id is not None else -1),
    ).fetchone()
    if exist:
        nid = exist["NodeID"]
    else:
        cur = conn.execute(
            """INSERT INTO FacetNode
               (FacetID, NodeName, Description, ParentNodeID, NodeLevel, Path, NodeKind, Status, CanPromote, TopicID, CreatedAt)
               VALUES (?,?,?, ?, ?, ?, 'problem', 'active', 1, ?, ?)""",
            (
                facet_id, name, f"upstream search node; query={query}",
                parent_id,
                (parent["NodeLevel"] or 0) + 1 if parent else 0,
                f"{parent['Path'] or f'/{parent_id}/'}" if parent else "/",
                topic_id, now_iso(),
            ),
        )
        nid = cur.lastrowid
        conn.execute("UPDATE FacetNode SET Path=? WHERE NodeID=?", (f"/{nid}/" if not parent else f"{parent['Path'] or f'/{parent_id}/'}{nid}/", nid))
        _log(conn, nid, "expand", {"name": name, "query": query}, "upstream new search node")

    # ensure a ResearchTopic row for this small question
    if topic_id is None:
        cur = conn.execute(
            """INSERT INTO ResearchTopic (TopicName, Description, ParentTopicID, Keywords, SuccessCriteria, Priority, Status)
               VALUES (?,?,?,?,?,?, 'active')""",
            (
                name,
                f"upstream small question / new topic",
                None,
                query,
                success or f"围绕「{name}」找到可跟进的 IEEE 工作",
                2,
            ),
        )
        topic_id = cur.lastrowid
        conn.execute("UPDATE FacetNode SET TopicID=? WHERE NodeID=?", (topic_id, nid))
    conn.commit()
    conn.close()

    if promote:
        # run_retrieve_rank will promote if --promote path used; call with promote=True
        result = run_retrieve_rank(
            query=query,
            node_id=nid,
            top_k=top_k,
            year_from=year_from,
            increment_only=False,
            promote=True,
            promote_reason=f"upstream: 以节点「{name}」为新检索根",
            topic_id=topic_id,
        )
    else:
        result = run_retrieve_rank(
            query=query,
            node_id=nid,
            top_k=top_k,
            year_from=year_from,
            promote=False,
            topic_id=topic_id,
        )
    make_report()
    return {"node_id": nid, "topic_id": topic_id, **result}


def cmd_relate(
    dim: str,
    src_type: str,
    src_id: int,
    tgt_type: str,
    tgt_id: int,
    degree: float = 0.8,
    method: str = "upstream",
    node_id: int | None = None,
    reason: str = "upstream relate",
) -> None:
    """在某节点建立新关联（关联度）+ 可选网状边。"""
    conn = connect(DB_PATH)
    conn.execute(
        """INSERT INTO Association
           (Dim, SourceRefType, SourceRefID, TargetRefType, TargetRefID, Degree, Method, CreatedAt)
           VALUES (?,?,?,?,?,?,?,?)""",
        (dim, src_type, src_id, tgt_type, tgt_id, degree, method, now_iso()),
    )
    # if both papers, also create a RelEdge
    if src_type == "Paper" and tgt_type == "Paper":
        rt = conn.execute("SELECT RelTypeID FROM RelationType WHERE TypeName='similar_to'").fetchone()
        if rt:
            rt_id = rt[0]
        else:
            rt_id = conn.execute(
                "INSERT INTO RelationType (TypeName, IsDirected) VALUES ('similar_to', 0)"
            ).lastrowid
        conn.execute(
            """INSERT INTO RelEdge (RelTypeID, FromPaperID, ToPaperID, Weight, Confidence, CreatedAt)
               VALUES (?,?,?,?,?,?)""",
            (rt_id, src_id, tgt_id, degree, 0.9, now_iso()),
        )
    if node_id:
        _log(conn, node_id, "absorb",
             {"dim": dim, "src": f"{src_type}:{src_id}", "tgt": f"{tgt_type}:{tgt_id}", "degree": degree},
             reason)
    conn.commit()
    conn.close()
    print(f"related {src_type}:{src_id} -[{dim} {degree}]-> {tgt_type}:{tgt_id}")


def cmd_tree() -> None:
    conn = connect(DB_PATH)
    conn.row_factory = __import__("sqlite3").Row
    rows = conn.execute(
        """SELECT N.NodeID, N.NodeName, N.ParentNodeID, N.NodeLevel, N.Status, N.IsSearchRoot, F.FacetName
           FROM FacetNode N JOIN Facet F ON N.FacetID=F.FacetID
           ORDER BY N.Path, N.NodeID"""
    ).fetchall()
    conn.close()
    print(f"{'ID':>4} {'L':>2} {'parent':>6}  {'status':12} {'root':4} facet/ node")
    for r in rows:
        print(
            f"{r['NodeID']:4d} {r['NodeLevel']:2d} {r['ParentNodeID'] or '-':>6}  "
            f"{(r['Status'] or ''):12} {'Y' if r['IsSearchRoot'] else '':4} "
            f"{r['FacetName']}/ {r['NodeName']}"
        )


def cmd_export(path: Path) -> None:
    conn = connect(DB_PATH)
    conn.row_factory = __import__("sqlite3").Row
    data = {
        "nodes": [dict(r) for r in conn.execute("SELECT * FROM FacetNode ORDER BY Path, NodeID")],
        "facets": [dict(r) for r in conn.execute("SELECT * FROM Facet")],
        "associations": [dict(r) for r in conn.execute("SELECT * FROM Association")],
        "relations": [dict(r) for r in conn.execute("SELECT * FROM RelEdge")],
        "topics": [dict(r) for r in conn.execute("SELECT * FROM ResearchTopic")],
    }
    conn.close()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported structure -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Upstream structure commands")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("subdivide", help="在节点下细分")
    p.add_argument("--parent", type=int, required=True)
    p.add_argument("--names", required=True, help="逗号分隔子节点名")
    p.add_argument("--kind", default="problem")

    p = sub.add_parser("move", help="更改节点位置")
    p.add_argument("--node", type=int, required=True)
    p.add_argument("--parent", type=int, default=None, help="新父节点；不传则提为根")
    p.add_argument("--reason", default="upstream reposition")

    p = sub.add_parser("search", help="以新课题/小问题为节点立刻搜索")
    p.add_argument("--name", required=True, help="节点名 / 小问题")
    p.add_argument("--query", required=True)
    p.add_argument("--parent", type=int, default=None)
    p.add_argument("--topic", type=int, default=None)
    p.add_argument("--top-k", type=int, default=15)
    p.add_argument("--year-from", type=int, default=2020)
    p.add_argument("--no-promote", action="store_true")
    p.add_argument("--success", default=None, help="覆盖上游 SuccessCriteria")

    p = sub.add_parser("relate", help="建立新关联")
    p.add_argument("--dim", default="method_similarity",
                   help="topic_relevance|method_similarity|impact|temporal")
    p.add_argument("--src-type", default="Paper", choices=["Paper", "FacetNode", "ResearchTopic"])
    p.add_argument("--src", type=int, required=True)
    p.add_argument("--tgt-type", default="Paper", choices=["Paper", "FacetNode", "ResearchTopic"])
    p.add_argument("--tgt", type=int, required=True)
    p.add_argument("--degree", type=float, default=0.8)
    p.add_argument("--node", type=int, default=None, help="在哪棵树节点记这条关联")
    p.add_argument("--reason", default="upstream relate")

    sub.add_parser("tree", help="打印结构树")
    p = sub.add_parser("export", help="导出结构 JSON")
    p.add_argument("--out", default=str(DB_PATH.parent / "structure_export.json"))

    args = ap.parse_args()
    if args.cmd == "subdivide":
        cmd_subdivide(args.parent, args.names.split(","), kind=args.kind)
    elif args.cmd == "move":
        cmd_move(args.node, args.parent, reason=args.reason)
    elif args.cmd == "search":
        cmd_search_node(
            args.name, args.query, parent_id=args.parent, topic_id=args.topic,
            top_k=args.top_k, year_from=args.year_from, promote=not args.no_promote,
            success=args.success,
        )
    elif args.cmd == "relate":
        cmd_relate(
            args.dim, args.src_type, args.src, args.tgt_type, args.tgt,
            degree=args.degree, node_id=args.node, reason=args.reason,
        )
    elif args.cmd == "tree":
        cmd_tree()
    elif args.cmd == "export":
        cmd_export(Path(args.out))


if __name__ == "__main__":
    main()
