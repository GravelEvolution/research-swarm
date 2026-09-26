# -*- coding: utf-8 -*-
"""Config loader — single source of truth is <repo>/config.json."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"


@lru_cache(maxsize=1)
def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def reload_config(path: str | Path | None = None) -> dict:
    load_config.cache_clear()
    return load_config(path)


def save_raw_cfg(cfg: dict, path: str | Path | None = None) -> Path:
    p = Path(path) if path else CONFIG_PATH
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_config.cache_clear()
    return p


def _abs(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (REPO_ROOT / p)


def storage_paths(cfg: dict | None = None) -> dict:
    """Resolve data paths.

    Prefer per-task folders:
      tasks/<active_id>/{data,papers,exports}
    Fallback to config.storage at repo root (legacy / no active task).
    """
    cfg = cfg or load_config()
    tasks_cfg = cfg.get("tasks") or {}
    active_id = tasks_cfg.get("active_id")
    if active_id:
        root = REPO_ROOT / tasks_cfg.get("root", "tasks") / active_id
        return {
            "task_id": active_id,
            "db": root / "data" / "paper_research.sqlite",
            "pdf_dir": root / "papers",
            "report": root / "data" / "decision_report.md",
            "structure": root / "exports" / "structure_export.json",
        }
    st = cfg.get("storage", {})
    return {
        "task_id": None,
        "db": _abs(st.get("db_path", "data/paper_research.sqlite")),
        "pdf_dir": _abs(st.get("pdf_dir", "papers")),
        "report": _abs(st.get("report_path", "data/decision_report.md")),
        "structure": _abs(st.get("structure_export", "data/structure_export.json")),
    }


def get_topic(cfg: dict | None = None, topic_id: str | None = None) -> dict:
    cfg = cfg or load_config()
    topics = cfg.get("topics") or []
    if not topics:
        raise ValueError("config.topics is empty")
    if topic_id is None:
        topics_sorted = sorted(topics, key=lambda t: t.get("priority", 99))
        return topics_sorted[0]
    for t in topics:
        if t.get("id") == topic_id or t.get("name") == topic_id:
            return t
    raise KeyError(f"topic not found: {topic_id}")


def lexicon_pairs(topic: dict, kind: str) -> list[tuple[str, str]]:
    """Return [(pattern, label), ...] from topic.lexicon.method|task|solution."""
    lex = (topic.get("lexicon") or {}).get(kind) or []
    out = []
    for item in lex:
        if isinstance(item, dict) and item.get("pattern") and item.get("label"):
            out.append((item["pattern"], item["label"]))
    return out


def scoring_weights(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    w = (cfg.get("scoring") or {}).get("weights") or {}
    return {
        "novelty": float(w.get("novelty", 0.2)),
        "relevance": float(w.get("relevance", 0.35)),
        "impact": float(w.get("impact", 0.25)),
        "repro": float(w.get("repro", 0.15)),
        "urgency": float(w.get("urgency", 0.05)),
    }


def ensure_dirs(cfg: dict | None = None) -> dict:
    paths = storage_paths(cfg)
    paths["db"].parent.mkdir(parents=True, exist_ok=True)
    paths["pdf_dir"].mkdir(parents=True, exist_ok=True)
    paths["report"].parent.mkdir(parents=True, exist_ok=True)
    paths["structure"].parent.mkdir(parents=True, exist_ok=True)
    return paths
