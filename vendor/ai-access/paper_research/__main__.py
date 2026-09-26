# -*- coding: utf-8 -*-
"""python -m paper_research  — repo CLI

Examples:
  python -m paper_research init
  python -m paper_research retrieve --query "..." --topic multimodal-efficiency
  python -m paper_research search --name "KV缓存" --query "kv cache multimodal"
  python -m paper_research subdivide --parent 4 --names "A,B"
  python -m paper_research move --node 17 --parent 12
  python -m paper_research relate --src 1 --tgt 2 --dim method_similarity
  python -m paper_research tree
  python -m paper_research report
  python -m paper_research export
  python -m paper_research topics
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from paper_research.config import (  # noqa: E402
    ensure_dirs,
    get_topic,
    load_config,
    storage_paths,
)
from paper_research.schema import connect  # noqa: E402


def _bootstrap_build_path() -> None:
    sys.path.insert(0, str(_ROOT / "build"))


def cmd_init() -> None:
    from paper_research import tasks as tasks_mod

    cfg = load_config()
    # ensure at least one task workspace
    if not tasks_mod.active_task_id(cfg) or not (
        tasks_mod.task_dir(tasks_mod.active_task_id(cfg) or "") / "task.json"
    ).exists():
        tid = (cfg.get("tasks") or {}).get("active_id") or "default"
        try:
            tasks_mod.create_task(tid, name=tid, make_active=True)
            print(f"created task workspace: {tid}")
        except FileExistsError:
            tasks_mod.set_active_task(tid)
    paths = ensure_dirs(load_config())
    conn = connect(paths["db"])
    # seed topics from config
    for t in cfg.get("topics") or []:
        row = conn.execute(
            "SELECT TopicID FROM ResearchTopic WHERE TopicName=?", (t["name"],)
        ).fetchone()
        if not row:
            conn.execute(
                """INSERT INTO ResearchTopic (TopicName, Description, Keywords, SuccessCriteria, Priority, Status)
                   VALUES (?,?,?,?,?,'active')""",
                (
                    t["name"],
                    t.get("id", ""),
                    t.get("keywords", ""),
                    t.get("success_criteria", ""),
                    t.get("priority", 3),
                ),
            )
    conn.execute(
        """INSERT OR IGNORE INTO ProtocolContract
           (ContractID, Version, Name, InputSchemaJson, OutputSchemaJson, CallSignature, IsActive, CreatedAt)
           VALUES ('retrieve_rank/v1','1.0','检索排序协议','{}','{}','retrieve_rank()',1, datetime('now'))"""
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) AS c FROM ResearchTopic").fetchone()["c"]
    conn.close()
    print(f"init ok: db={paths['db']} topics={n}")
    print(f"pdfs -> {paths['pdf_dir']}")
    print(f"report -> {paths['report']}")


def cmd_topics() -> None:
    cfg = load_config()
    print(f"{'id':22} {'priority':>8}  name / success")
    for t in sorted(cfg.get("topics") or [], key=lambda x: x.get("priority", 99)):
        print(
            f"{t.get('id',''):22} {t.get('priority',''):>8}  "
            f"{t.get('name','')} — {(t.get('success_criteria') or '')[:50]}"
        )


def main() -> None:
    _bootstrap_build_path()
    ap = argparse.ArgumentParser(prog="paper_research", description="IEEE paper decision research")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="建库 + 从 config.json 写入课题")
    sub.add_parser("topics", help="列出 config.json 中的课题")

    p = sub.add_parser("retrieve", help="retrieve_rank 检索+排序+下载")
    p.add_argument("--query", required=True)
    p.add_argument("--topic", default=None, help="config topics[].id")
    p.add_argument("--node", type=int, default=None)
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--year-from", type=int, default=None)
    p.add_argument("--increment", action="store_true")
    p.add_argument("--promote", action="store_true")

    p = sub.add_parser("search", help="新课题/小问题为节点并立刻检索")
    p.add_argument("--name", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--parent", type=int, default=None)
    p.add_argument("--top-k", type=int, default=15)
    p.add_argument("--year-from", type=int, default=2020)

    p = sub.add_parser("subdivide", help="上游细分")
    p.add_argument("--parent", type=int, required=True)
    p.add_argument("--names", required=True)

    p = sub.add_parser("move", help="更改节点位置")
    p.add_argument("--node", type=int, required=True)
    p.add_argument("--parent", type=int, default=None)

    p = sub.add_parser("relate", help="建立关联")
    p.add_argument("--dim", default="method_similarity")
    p.add_argument("--src-type", default="Paper")
    p.add_argument("--src", type=int, required=True)
    p.add_argument("--tgt-type", default="Paper")
    p.add_argument("--tgt", type=int, required=True)
    p.add_argument("--degree", type=float, default=0.8)
    p.add_argument("--node", type=int, default=None)

    p = sub.add_parser("models", help="编辑/查看模型供应厂商")
    msub = p.add_subparsers(dest="models_cmd", required=True)
    msub.add_parser("list", help="列出厂商与模型")
    pa = msub.add_parser("add", help="新增厂商")
    pa.add_argument("--name", required=True)
    pa.add_argument("--type", default="openai", choices=["openai", "anthropic"])
    pa.add_argument("--base-url", default="")
    pa.add_argument("--label", default=None)
    pa.add_argument("--api-key-env", default=None)
    pa.add_argument("--api-key", default=None, help="不推荐写入配置；优先用环境变量")
    pa.add_argument("--models", default="", help="逗号分隔模型 ID")
    pu = msub.add_parser("use", help="切换当前厂商/模型")
    pu.add_argument("--provider", required=True)
    pu.add_argument("--model", default=None)
    pm = msub.add_parser("set", help="修改厂商字段")
    pm.add_argument("--name", required=True)
    pm.add_argument("--base-url", default=None)
    pm.add_argument("--label", default=None)
    pm.add_argument("--api-key-env", default=None)
    pm.add_argument("--models", default=None, help="逗号分隔，覆盖模型列表")
    pm.add_argument("--enable", dest="enable", action="store_true", default=None)
    pm.add_argument("--disable", dest="enable", action="store_false", default=None)
    pr = msub.add_parser("remove", help="删除厂商")
    pr.add_argument("--name", required=True)
    pk = msub.add_parser("set-key", help="设置 API Key")
    pk.add_argument("--name", required=True)
    pk.add_argument("--key", required=True)
    pk.add_argument("--env", action="store_true", help="写入进程环境变量而非配置文件")
    pt = msub.add_parser("test", help="测试厂商连通")
    pt.add_argument("--name", required=True)
    pt.add_argument("--model", default=None)

    p = sub.add_parser("task", help="任务工作区（每任务独立文件夹）")
    tsub = p.add_subparsers(dest="task_cmd", required=True)
    tsub.add_parser("list", help="列出任务")
    pt = tsub.add_parser("new", help="新建任务文件夹")
    pt.add_argument("--id", required=True, help="任务 ID（文件夹名）")
    pt.add_argument("--name", default=None)
    pt.add_argument("--topic", default=None, help="绑定 config topics[].id")
    pt.add_argument("--desc", default="")
    pt.add_argument("--no-activate", action="store_true")
    pu = tsub.add_parser("use", help="切换当前任务")
    pu.add_argument("--id", required=True)
    tsub.add_parser("show", help="显示当前任务")
    pd = tsub.add_parser("remove", help="归档或删除任务")
    pd.add_argument("--id", required=True)
    pd.add_argument("--purge", action="store_true", help="真正删除文件夹")

    sub.add_parser("tree", help="打印结构树")
    sub.add_parser("report", help="生成决策报告")
    sub.add_parser("export", help="导出结构 JSON")

    args = ap.parse_args()

    if args.cmd == "init":
        cmd_init()
        return
    if args.cmd == "topics":
        cmd_topics()
        return
    if args.cmd == "task":
        from paper_research import tasks as tasks_mod

        if args.task_cmd == "list":
            rows = tasks_mod.list_tasks()
            if not rows:
                print("(no tasks)")
            print(f"{'id':24} {'active':6} {'pdf':>4}  name")
            for t in rows:
                mark = "*" if t["active"] else ""
                print(f"{t['id']:24} {mark:6} {t['pdf_count']:>4}  {t['name']}")
        elif args.task_cmd == "new":
            meta = tasks_mod.create_task(
                args.id,
                name=args.name,
                topic_id=args.topic,
                description=args.desc,
                make_active=not args.no_activate,
            )
            print(f"created task: {args.id}")
            print(f"  db:    {meta['layout']['db']}")
            print(f"  pdfs:  {meta['layout']['pdf_dir']}")
            print(f"  report:{meta['layout']['report']}")
        elif args.task_cmd == "use":
            tasks_mod.set_active_task(args.id)
            print(f"active task -> {args.id}")
        elif args.task_cmd == "show":
            paths = tasks_mod.active_paths()
            print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2, ensure_ascii=False))
        elif args.task_cmd == "remove":
            tasks_mod.delete_task(args.id, purge_files=args.purge)
            print(f"{'purged' if args.purge else 'archived'}: {args.id}")
        return
    if args.cmd == "models":
        from paper_research import llm as llm_mod

        if args.models_cmd == "list":
            cfg = load_config()
            llm = llm_mod.get_llm_cfg(cfg)
            print(f"active: {llm.get('active_provider')} / {llm.get('active_model')}")
            for p in llm_mod.list_providers(cfg):
                mark = "*" if p["active"] else " "
                key = "key:ok" if llm_mod.resolve_api_key(p) else "key:--"
                print(
                    f"{mark} {p['name']:12} type={p['type']:9} {key}  "
                    f"{p['base_url']}"
                )
                print(f"   models: {', '.join(p['models']) or '(none)'}")
        elif args.models_cmd == "add":
            llm_mod.add_provider(
                args.name, type=args.type, base_url=args.base_url or "",
                label=args.label, api_key_env=args.api_key_env,
                api_key=args.api_key, models=[m.strip() for m in args.models.split(",") if m.strip()],
            )
            print(f"added provider: {args.name}")
        elif args.models_cmd == "use":
            r = llm_mod.use_provider(args.provider, args.model)
            print(f"active -> {r['provider']} / {r['model']}")
        elif args.models_cmd == "set":
            fields = {
                "base_url": args.base_url,
                "label": args.label,
                "api_key_env": args.api_key_env,
                "models": args.models,
                "enabled": args.enable,
            }
            p = llm_mod.update_provider(args.name, **fields)
            print(f"updated: {args.name} -> {p}")
        elif args.models_cmd == "remove":
            llm_mod.remove_provider(args.name)
            print(f"removed: {args.name}")
        elif args.models_cmd == "set-key":
            llm_mod.set_api_key(args.name, args.key, use_env=args.env)
            where = "env" if args.env else "config.json"
            print(f"api key saved for {args.name} -> {where}")
        elif args.models_cmd == "test":
            r = llm_mod.test_provider(args.name, args.model)
            print(json.dumps(r, ensure_ascii=False))
        return

    import retrieve_rank as rr
    import upstream_ops as uo

    if args.cmd == "retrieve":
        cfg = load_config()
        topic = get_topic(cfg, args.topic) if args.topic else get_topic(cfg)
        rr.run_retrieve_rank(
            query=args.query,
            node_id=args.node,
            top_k=args.top_k or cfg["retrieve"].get("default_top_k", 20),
            year_from=args.year_from or cfg["retrieve"].get("default_year_from", 2020),
            increment_only=args.increment,
            promote=args.promote,
            topic_id=None,
        )
        rr.make_report()
    elif args.cmd == "search":
        uo.cmd_search_node(args.name, args.query, parent_id=args.parent, top_k=args.top_k,
                           year_from=args.year_from)
    elif args.cmd == "subdivide":
        uo.cmd_subdivide(args.parent, args.names.split(","))
    elif args.cmd == "move":
        uo.cmd_move(args.node, args.parent)
    elif args.cmd == "relate":
        uo.cmd_relate(args.dim, args.src_type, args.src, args.tgt_type, args.tgt,
                      degree=args.degree, node_id=args.node)
    elif args.cmd == "tree":
        uo.cmd_tree()
    elif args.cmd == "report":
        rr.make_report()
    elif args.cmd == "export":
        uo.cmd_export(storage_paths()["structure"])


if __name__ == "__main__":
    main()
