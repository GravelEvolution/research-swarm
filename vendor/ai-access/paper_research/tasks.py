# -*- coding: utf-8 -*-
"""Task workspace manager.

Each task (课题/项目) gets its own folder:
  tasks/<task_id>/
    task.json          # 任务元数据 + 可覆盖配置
    data/              # sqlite + report
    papers/            # PDF
    exports/

Active task is tracked in config.json → tasks.active_id
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import CONFIG_PATH, REPO_ROOT, load_config, reload_config, save_raw_cfg

TASKS_DIR = REPO_ROOT / "tasks"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_dir(task_id: str) -> Path:
    return TASKS_DIR / task_id


def load_task_meta(task_id: str) -> dict:
    p = task_dir(task_id) / "task.json"
    if not p.exists():
        raise FileNotFoundError(f"task not found: {task_id} ({p})")
    return json.loads(p.read_text(encoding="utf-8"))


def save_task_meta(task_id: str, meta: dict) -> Path:
    d = task_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "task.json"
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def list_tasks() -> list[dict]:
    if not TASKS_DIR.exists():
        return []
    out = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta_p = d / "task.json"
        if not meta_p.exists():
            continue
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        db = d / "data" / "paper_research.sqlite"
        pdfs = list((d / "papers").glob("*.pdf")) if (d / "papers").exists() else []
        out.append({
            "id": meta.get("id") or d.name,
            "name": meta.get("name") or d.name,
            "topic_id": meta.get("topic_id"),
            "description": meta.get("description", ""),
            "created_at": meta.get("created_at"),
            "path": str(d),
            "has_db": db.exists(),
            "pdf_count": len(pdfs),
            "active": meta.get("id") == active_task_id(),
        })
    return out


def active_task_id(cfg: dict | None = None) -> str | None:
    cfg = cfg or load_config()
    return (cfg.get("tasks") or {}).get("active_id")


def set_active_task(task_id: str, cfg: dict | None = None) -> None:
    cfg = cfg or load_config()
    if not (task_dir(task_id) / "task.json").exists():
        raise FileNotFoundError(f"task not found: {task_id}")
    cfg.setdefault("tasks", {})["active_id"] = task_id
    save_raw_cfg(cfg)


def ensure_task_layout(task_id: str) -> dict:
    d = task_dir(task_id)
    for sub in ("data", "papers", "exports"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return {
        "root": d,
        "db": d / "data" / "paper_research.sqlite",
        "pdf_dir": d / "papers",
        "report": d / "data" / "decision_report.md",
        "structure": d / "exports" / "structure_export.json",
    }


def create_task(
    task_id: str,
    name: str | None = None,
    topic_id: str | None = None,
    description: str = "",
    make_active: bool = True,
    config_overrides: dict | None = None,
) -> dict:
    if not task_id or "/" in task_id or "\\" in task_id:
        raise ValueError("task_id must be a simple name (no path separators)")
    d = task_dir(task_id)
    if (d / "task.json").exists():
        raise FileExistsError(f"task already exists: {task_id}")
    layout = ensure_task_layout(task_id)
    meta = {
        "id": task_id,
        "name": name or task_id,
        "topic_id": topic_id,
        "description": description,
        "created_at": _now(),
        "config_overrides": config_overrides or {},
        "paths": {
            "db": str(layout["db"].relative_to(REPO_ROOT)),
            "pdf_dir": str(layout["pdf_dir"].relative_to(REPO_ROOT)),
            "report": str(layout["report"].relative_to(REPO_ROOT)),
            "structure": str(layout["structure"].relative_to(REPO_ROOT)),
        },
    }
    save_task_meta(task_id, meta)
    # touch empty sqlite with schema
    from .schema import connect

    conn = connect(layout["db"])
    conn.close()
    if make_active:
        set_active_task(task_id)
    return {"task": meta, "layout": layout}


def clone_task(src_id: str, dst_id: str, copy_data: bool = False) -> dict:
    """Clone task meta (and optionally data) to a new task folder."""
    src = task_dir(src_id)
    if not (src / "task.json").exists():
        raise FileNotFoundError(f"source task not found: {src_id}")
    return create_task(
        dst_id,
        name=f"{load_task_meta(src_id).get('name', src_id)}-copy",
        topic_id=load_task_meta(src_id).get("topic_id"),
        description=f"cloned from {src_id}",
        make_active=False,
        config_overrides=load_task_meta(src_id).get("config_overrides"),
    )


def delete_task(task_id: str, purge_files: bool = False) -> None:
    d = task_dir(task_id)
    if not (d / "task.json").exists():
        raise FileNotFoundError(f"task not found: {task_id}")
    if purge_files:
        shutil.rmtree(d)
    else:
        # archive: just mark deleted in meta, keep files
        meta = load_task_meta(task_id)
        meta["status"] = "archived"
        meta["archived_at"] = _now()
        save_task_meta(task_id, meta)
    cfg = load_config()
    if active_task_id(cfg) == task_id:
        remaining = [t["id"] for t in list_tasks() if t["id"] != task_id and t.get("status") != "archived"]
        cfg.setdefault("tasks", {})["active_id"] = remaining[0] if remaining else None
        save_raw_cfg(cfg)


def active_paths() -> dict:
    """Resolve storage paths for the active task (or root fallback)."""
    cfg = load_config()
    tid = active_task_id(cfg)
    if tid and (task_dir(tid) / "task.json").exists():
        layout = ensure_task_layout(tid)
        return {
            "task_id": tid,
            "db": layout["db"],
            "pdf_dir": layout["pdf_dir"],
            "report": layout["report"],
            "structure": layout["structure"],
        }
    # fallback: config.storage at repo root (legacy)
    from .config import storage_paths

    paths = storage_paths(cfg)
    paths["task_id"] = None
    return paths
