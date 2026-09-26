# -*- coding: utf-8 -*-
"""Core library: OpenAlex/Crossref fetch, facet mount, score, store.

Used by retrieve_rank.py (protocol executor). No third-party deps.
"""

from __future__ import annotations

import json
import re
import socket
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

socket.setdefaulttimeout(30)

# package config
import sys
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from paper_research.config import (  # noqa: E402
    get_topic,
    lexicon_pairs,
    load_config,
    scoring_weights,
    storage_paths,
)

_cfg = load_config()
_paths = storage_paths(_cfg)
ROOT = _ROOT
DB_PATH = _paths["db"]
PDF_DIR = _paths["pdf_dir"]
OPENALEX = _cfg["sources"]["openalex"]["base_url"]
CROSSREF = _cfg["sources"]["crossref"]["base_url"]
MAILTO = _cfg["sources"]["openalex"]["mailto"]
UA = f"paper-research/1.0 (mailto:{MAILTO})"
DEFAULT_TOPIC = get_topic(_cfg)
WEIGHTS = scoring_weights(_cfg)
IEEE_ONLY = bool(_cfg["sources"]["openalex"].get("ieee_only", True))

# --- lexicons from config (per-topic; fall back to default topic) ---
def _lex(kind: str, topic: dict | None = None) -> list[tuple[str, str]]:
    return lexicon_pairs(topic or DEFAULT_TOPIC, kind)


def METHOD_LEXICON(topic: dict | None = None) -> list[tuple[str, str]]:
    return _lex("method", topic)


def TASK_LEXICON(topic: dict | None = None) -> list[tuple[str, str]]:
    return _lex("task", topic)


def SOLUTION_LEXICON(topic: dict | None = None) -> list[tuple[str, str]]:
    return _lex("solution", topic)


# backward-compatible module-level defaults (default topic)
METHOD_LEXICON_DEFAULT = METHOD_LEXICON()
TASK_LEXICON_DEFAULT = TASK_LEXICON()
SOLUTION_LEXICON_DEFAULT = SOLUTION_LEXICON()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_json(url: str, retries: int = 3) -> dict:
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.0 * (i + 1))
    raise RuntimeError("http_json failed")


def abstract_from_inverted(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = [(i, w) for w, idxs in inv.items() for i in idxs]
    pos.sort()
    return " ".join(w for _, w in pos)


def work_to_paper(work: dict) -> dict:
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    authors = []
    for a in work.get("authorships") or []:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    oa_urls = []
    oa = work.get("open_access") or {}
    if oa.get("oa_url"):
        oa_urls.append(oa["oa_url"])
    for loc in (work.get("best_oa_location"), work.get("primary_location"), *(work.get("locations") or [])):
        if not isinstance(loc, dict):
            continue
        for key in ("pdf_url", "landing_page_url"):
            u = loc.get(key)
            if u and u not in oa_urls:
                oa_urls.append(u)
    return {
        "Title": work.get("display_name") or "(untitled)",
        "Abstract": abstract_from_inverted(work.get("abstract_inverted_index")),
        "Year": work.get("publication_year"),
        "Venue": source.get("display_name") or "",
        "DOI": doi or None,
        "OpenAlexId": work.get("id"),
        "Authors": "; ".join(authors[:20]),
        "CitationCount": work.get("cited_by_count") or 0,
        "Publisher": source.get("host_organization_name") or "IEEE",
        "URL": loc.get("landing_page_url") or work.get("id"),
        "OAUrls": oa_urls,
    }


def looks_ieee(paper: dict) -> bool:
    blob = " ".join(
        str(x)
        for x in [
            paper.get("Venue"),
            paper.get("Publisher"),
            paper.get("URL"),
            paper.get("OpenAlexId"),
        ]
        if x
    ).lower()
    return any(k in blob for k in ("ieee", "ieeexplore", "institute of electrical"))


def fetch_openalex(query: str, year_from: int | None = None, max_results: int = 20,
                   ieee_only: bool = True) -> list[dict]:
    results: list[dict] = []
    cursor = "*"
    pages = 0
    while pages < 6 and (not max_results or len(results) < max_results):
        pages += 1
        params = {
            "search": query,
            "per-page": 25,
            "cursor": cursor,
            "sort": "relevance_score:desc",
            "mailto": MAILTO,
        }
        if year_from:
            params["filter"] = f"from_publication_date:{year_from}-01-01"
        data = http_json(f"{OPENALEX}?{urllib.parse.urlencode(params)}")
        batch = data.get("results") or []
        for w in batch:
            p = work_to_paper(w)
            if ieee_only and not looks_ieee(p):
                continue
            results.append(p)
            if max_results and len(results) >= max_results:
                return results
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor or not batch:
            break
        time.sleep(0.15)
    return results


def fetch_crossref_ieee(query: str, max_results: int = 10) -> list[dict]:
    """Crossref fallback: IEEE member filter is not exact; post-filter on container."""
    params = {
        "query": query,
        "rows": min(max_results, 20),
        "mailto": MAILTO,
    }
    url = f"{CROSSREF}?{urllib.parse.urlencode(params)}"
    data = http_json(url)
    out: list[dict] = []
    for item in (data.get("message") or {}).get("items") or []:
        cont = " ".join(item.get("container-title") or []) or ""
        pub = (item.get("publisher") or "")
        blob = f"{cont} {pub}".lower()
        if not any(k in blob for k in ("ieee", "institute of electrical")):
            continue
        title = " ".join(item.get("title") or ["(untitled)"])
        authors = []
        for a in item.get("author") or []:
            authors.append(f"{a.get('given','')} {a.get('family','')}".strip())
        year = None
        for key in ("published-print", "published-online", "issued"):
            parts = ((item.get(key) or {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                year = parts[0]
                break
        doi = (item.get("DOI") or "")
        out.append({
            "Title": title,
            "Abstract": (item.get("abstract") or "").strip()[:2000],
            "Year": year,
            "Venue": cont,
            "DOI": doi or None,
            "OpenAlexId": f"https://doi.org/{doi}" if doi else None,
            "Authors": "; ".join(authors[:20]),
            "CitationCount": item.get("is-referenced-by-count") or 0,
            "Publisher": pub or "IEEE",
            "URL": item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        })
        if len(out) >= max_results:
            break
    return out


# --- facet auto-mount ---
def match_lexicon(text: str, lexicon: list[tuple[str, str]]) -> list[str]:
    t = (text or "").lower()
    hits = []
    for pat, label in lexicon:
        if re.search(pat, t, flags=re.I):
            if label not in hits:
                hits.append(label)
    return hits


def ensure_node(conn: sqlite3.Connection, facet_id: int, name: str,
                parent_id: int | None, level: int, kind: str = "problem") -> int:
    row = conn.execute(
        "SELECT NodeID FROM FacetNode WHERE FacetID=? AND NodeName=? AND (ParentNodeID IS ? OR ParentNodeID = ?)",
        (facet_id, name, parent_id, parent_id if parent_id is not None else -1),
    ).fetchone()
    if row:
        return row["NodeID"]
    path = f"/{name}/"
    cur = conn.execute(
        """INSERT INTO FacetNode (FacetID, NodeName, ParentNodeID, NodeLevel, Path, NodeKind, Status, CanPromote, CreatedAt)
           VALUES (?,?,?,?,?,?, 'active', 1, ?)""",
        (facet_id, name, parent_id, level, path, kind, now_iso()),
    )
    nid = cur.lastrowid
    conn.execute("UPDATE FacetNode SET Path=? WHERE NodeID=?", (f"/{nid}/", nid))
    return nid


def get_or_create_facet(conn: sqlite3.Connection, name: str, topology: str, role: str) -> int:
    row = conn.execute("SELECT FacetID FROM Facet WHERE FacetName=?", (name,)).fetchone()
    if row:
        return row["FacetID"]
    return conn.execute(
        "INSERT INTO Facet (FacetName, FacetTopology, DecisionRole) VALUES (?,?,?)",
        (name, topology, role),
    ).lastrowid


def mount_facets(conn: sqlite3.Connection, paper_id: int, title: str, abstract: str) -> dict:
    text = f"{title} {abstract}"
    method_facet = get_or_create_facet(conn, "方法树", "Tree", "技术路线定位")
    task_facet = get_or_create_facet(conn, "任务树", "Tree", "问题空间定位")
    net_facet = get_or_create_facet(conn, "技术谱系网", "Network", "影响/借鉴关系")

    methods = match_lexicon(text, METHOD_LEXICON_DEFAULT)
    tasks = match_lexicon(text, TASK_LEXICON_DEFAULT)
    solutions = match_lexicon(text, SOLUTION_LEXICON_DEFAULT)

    # tree: method root = 深度学习 / Transformer branch when applicable
    root_dl = ensure_node(conn, method_facet, "深度学习", None, 0, "mixed")
    for m in methods:
        parent = root_dl
        level = 1
        if m in ("稀疏注意力", "线性注意力", "精确注意力加速"):
            parent = ensure_node(conn, method_facet, "Transformer", root_dl, 1, "mixed")
            parent = ensure_node(conn, method_facet, "注意力机制", parent, 2, "problem")
            level = 3
        elif m == "Transformer":
            continue
        nid = ensure_node(conn, method_facet, m, parent, level, "problem")
        conn.execute(
            """INSERT OR IGNORE INTO PaperFacet (PaperID, NodeID, Confidence, IsHumanConfirmed, CreatedAt)
               VALUES (?,?,?,?,?)""",
            (paper_id, nid, 0.75, 0, now_iso()),
        )

    root_task = ensure_node(conn, task_facet, "研究任务", None, 0, "mixed")
    for t in tasks:
        nid = ensure_node(conn, task_facet, t, root_task, 1, "problem")
        conn.execute(
            """INSERT OR IGNORE INTO PaperFacet (PaperID, NodeID, Confidence, IsHumanConfirmed, CreatedAt)
               VALUES (?,?,?,?,?)""",
            (paper_id, nid, 0.75, 0, now_iso()),
        )

    # solution clusters under 效率问题 node
    problem = ensure_node(conn, method_facet, "推理效率问题空间", root_dl, 1, "problem")
    for s in solutions:
        row = conn.execute(
            "SELECT ClusterID FROM SolutionCluster WHERE NodeID=? AND ApproachName=?",
            (problem, s),
        ).fetchone()
        if row:
            cid = row["ClusterID"]
        else:
            cid = conn.execute(
                "INSERT INTO SolutionCluster (NodeID, ApproachName, Description, Status) VALUES (?,?,?, 'active')",
                (problem, s, f"自动聚类：{s}"),
            ).lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO PaperSolution (ClusterID, PaperID, RankInSolution, IsHighValue) VALUES (?,?,1,0)",
            (cid, paper_id),
        )
        conn.execute(
            "UPDATE SolutionCluster SET PaperCount=(SELECT COUNT(*) FROM PaperSolution WHERE ClusterID=?) WHERE ClusterID=?",
            (cid, cid),
        )

    # association: topic relevance
    topic = conn.execute("SELECT TopicID FROM ResearchTopic ORDER BY TopicID LIMIT 1").fetchone()
    if topic:
        deg = 0.9 if any(k in text.lower() for k in ("efficient", "token", "sparse", "inference")) else 0.6
        if "survey" in text.lower():
            deg = max(deg, 0.55)
        conn.execute(
            """INSERT INTO Association
               (Dim, SourceRefType, SourceRefID, TargetRefType, TargetRefID, Degree, Method, CreatedAt)
               VALUES ('topic_relevance','Paper',?, 'ResearchTopic', ?, ?, 'lexicon', ?)""",
            (paper_id, topic["TopicID"], deg, now_iso()),
        )

    return {"methods": methods, "tasks": tasks, "solutions": solutions}


def link_network(conn: sqlite3.Connection, paper_id: int, title: str, all_papers: list[dict]) -> int:
    """Create similar_to / extends edges vs existing papers (title token overlap)."""
    rt = conn.execute("SELECT RelTypeID FROM RelationType WHERE TypeName='similar_to'").fetchone()
    if not rt:
        rt_id = conn.execute(
            "INSERT INTO RelationType (TypeName, IsDirected, FacetID) VALUES ('similar_to', 0, NULL)"
        ).lastrowid
    else:
        rt_id = rt["RelTypeID"]
    tset = set(re.findall(r"[a-z]{3,}", (title or "").lower()))
    created = 0
    for other in all_papers:
        if other["PaperID"] == paper_id:
            continue
        oset = set(re.findall(r"[a-z]{3,}", (other["Title"] or "").lower()))
        if not tset or not oset:
            continue
        inter = len(tset & oset)
        union = len(tset | oset)
        sim = inter / union if union else 0.0
        if sim >= 0.28:
            conn.execute(
                """INSERT INTO RelEdge (RelTypeID, FromPaperID, ToPaperID, Weight, Confidence, CreatedAt)
                   VALUES (?,?,?,?,?,?)""",
                (rt_id, paper_id, other["PaperID"], round(sim, 3), 0.7, now_iso()),
            )
            created += 1
    return created


# --- scoring (heuristic, LLM-hook compatible) ---
def heuristic_score(title: str, abstract: str, citations: int, success: str = "") -> dict:
    t = f"{title} {abstract}".lower()
    n, r, i, rep, u = 3, 3, 3, 3, 3
    is_survey = bool(re.search(r"\bsurvey\b|\breview\b|taxonomy", t))
    # efficiency / multimodal core
    eff = bool(re.search(
        r"efficient|accelerat|token\s*(prune|pruning|reduction|guided|merge)|sparse\s*attent|"
        r"linear\s*attent|inference\s*cost|latency|compress|distill|quantiz",
        t,
    ))
    mm = bool(re.search(r"multimodal|multi-modal|vision-language|image-text|vqa|visual\s*ground|llava", t))
    attn = bool(re.search(r"attention|transformer", t))

    if is_survey:
        n = 2
    if eff:
        r = max(r, 5)
        i = max(i, 4)
    if mm:
        r = max(r, 4)
    if attn:
        r = max(r, max(r, 3))
    # novelty: actual efficiency techniques
    if re.search(
        r"token\s*(prune|pruning|reduction|guided|merge)|sparse\s*attent|linear\s*attent|"
        r"flash\s*attent|retention|retnet|nas\b",
        t,
    ):
        n = max(n, 4)
        r = max(r, 5)
    if citations >= 400:
        i = max(i, 4)
    if citations >= 800:
        i = max(i, 5)
    if re.search(r"github|open.?source|code is available", t):
        rep = max(rep, 5)
    if re.search(r"2024|2025|2026", str(title)) or re.search(r"2024|2025|2026", t):
        u = max(u, 3)
    # topic mismatch dampener: clearly off-topic classics
    if not eff and not mm and re.search(r"knowledge\s*graph|explainab|head\s*pose|cycle-consistent|emotion|gpt\b", t):
        r = min(r, 2)
        i = min(i, 3)

    overall = round(
        WEIGHTS["novelty"] * n
        + WEIGHTS["relevance"] * r
        + WEIGHTS["impact"] * i
        + WEIGHTS["repro"] * rep
        + WEIGHTS["urgency"] * u,
        2,
    )
    if r >= 5 and n >= 4:
        rel = "competitive"
    elif eff and r >= 4:
        rel = "complementary"
    elif r >= 4:
        rel = "complementary"
    elif r >= 3:
        rel = "borrowable"
    else:
        rel = "unrelated"
    return {
        "NoveltyScore": n,
        "RelevanceScore": r,
        "ImpactScore": i,
        "ReproValue": rep,
        "Urgency": u,
        "OverallScore": overall,
        "ImpactRelation": rel,
        "OneLinePosition": (abstract or title)[:160].replace("\n", " "),
        "SolveWhat": "多模态/注意力推理效率",
        "ImpactNote": "lexicon+heuristic score",
        "KeyAdvantage": f"citations={citations}",
        "KeyLimitation": "auto score; optional LLM refine",
        "DecisionConfidence": 0.62 if (eff or mm) else 0.55,
    }


def upsert_paper(conn: sqlite3.Connection, paper: dict) -> tuple[int, bool]:
    """Return (paper_id, is_new)."""
    key = paper.get("OpenAlexId") or paper.get("DOI")
    row = None
    if paper.get("OpenAlexId"):
        row = conn.execute(
            "SELECT PaperID FROM Paper WHERE OpenAlexId=?", (paper["OpenAlexId"],)
        ).fetchone()
    if not row and paper.get("DOI"):
        row = conn.execute("SELECT PaperID FROM Paper WHERE DOI=?", (paper["DOI"],)).fetchone()
    ts = now_iso()
    if row:
        pid = row["PaperID"]
        conn.execute(
            """UPDATE Paper SET Title=?, Abstract=?, Year=?, Venue=?, DOI=?, Authors=?,
               CitationCount=?, Publisher=? WHERE PaperID=?""",
            (paper["Title"], paper["Abstract"], paper["Year"], paper["Venue"], paper["DOI"],
             paper["Authors"], paper["CitationCount"], paper["Publisher"], pid),
        )
        return pid, False
    pid = conn.execute(
        """INSERT INTO Paper
           (Title, Abstract, Year, Venue, DOI, OpenAlexId, Authors, CitationCount, Publisher, ImportedAt)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (paper["Title"], paper["Abstract"], paper["Year"], paper["Venue"], paper["DOI"],
         paper["OpenAlexId"], paper["Authors"], paper["CitationCount"], paper["Publisher"], ts),
    ).lastrowid
    if paper.get("URL"):
        conn.execute(
            "INSERT INTO SrcRecord (PaperID, SourceType, ExternalId, URL, RawJson, FetchedAt) VALUES (?,?,?,?,?,?)",
            (pid, "openalex", key, paper["URL"], json.dumps(paper, ensure_ascii=False)[:8000], ts),
        )
    return pid, True


def write_decision(conn: sqlite3.Connection, paper_id: int, scores: dict) -> int:
    ts = now_iso()
    ev = conn.execute(
        "SELECT EvidenceID FROM Evidence WHERE PaperID=? ORDER BY EvidenceID DESC LIMIT 1",
        (paper_id,),
    ).fetchone()
    fact = conn.execute(
        "SELECT FactID FROM Fact WHERE PaperID=? ORDER BY FactID DESC LIMIT 1",
        (paper_id,),
    ).fetchone()
    existing = conn.execute(
        "SELECT CardID FROM DecisionCard WHERE PaperID=?", (paper_id,)
    ).fetchone()
    if existing:
        card_id = existing["CardID"]
        conn.execute(
            """UPDATE DecisionCard SET OneLinePosition=?, SolveWhat=?, NoveltyScore=?, RelevanceScore=?,
               ImpactScore=?, ReproValue=?, Urgency=?, OverallScore=?, ImpactRelation=?, ImpactNote=?,
               KeyAdvantage=?, KeyLimitation=?, DecisionConfidence=?, DecidedAt=? WHERE CardID=?""",
            (scores["OneLinePosition"], scores["SolveWhat"], scores["NoveltyScore"],
             scores["RelevanceScore"], scores["ImpactScore"], scores["ReproValue"],
             scores["Urgency"], scores["OverallScore"], scores["ImpactRelation"],
             scores.get("ImpactNote", ""), scores.get("KeyAdvantage", ""),
             scores.get("KeyLimitation", ""), scores.get("DecisionConfidence", 0.6),
             ts, card_id),
        )
    else:
        card_id = conn.execute(
            """INSERT INTO DecisionCard
               (PaperID, OneLinePosition, SolveWhat, NoveltyScore, RelevanceScore, ImpactScore,
                ReproValue, Urgency, OverallScore, ImpactRelation, ImpactNote, KeyAdvantage,
                KeyLimitation, DecisionConfidence, IsHumanReviewed, DecidedAt)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
            (paper_id, scores["OneLinePosition"], scores["SolveWhat"], scores["NoveltyScore"],
             scores["RelevanceScore"], scores["ImpactScore"], scores["ReproValue"],
             scores["Urgency"], scores["OverallScore"], scores["ImpactRelation"],
             scores.get("ImpactNote", ""), scores.get("KeyAdvantage", ""),
             scores.get("KeyLimitation", ""), scores.get("DecisionConfidence", 0.6), ts),
        ).lastrowid
    conn.execute("DELETE FROM ScoreBasis WHERE CardID=?", (card_id,))
    for field, key in [
        ("NoveltyScore", "NoveltyScore"),
        ("RelevanceScore", "RelevanceScore"),
        ("ImpactScore", "ImpactScore"),
        ("ReproValue", "ReproValue"),
        ("Urgency", "Urgency"),
        ("OverallScore", "OverallScore"),
    ]:
        conn.execute(
            """INSERT INTO ScoreBasis (CardID, ScoreField, FactID, AssociationID, EvidenceID, Weight, Note)
               VALUES (?,?,?,?,?,1.0,?)""",
            (card_id, field, fact["FactID"] if fact else None, None,
             ev["EvidenceID"] if ev else None, scores.get("ImpactNote", "")),
        )
    return card_id


def ensure_evidence_fact(conn: sqlite3.Connection, paper_id: int, abstract: str) -> None:
    if not abstract:
        return
    has = conn.execute(
        "SELECT EvidenceID FROM Evidence WHERE PaperID=? LIMIT 1", (paper_id,)
    ).fetchone()
    if has:
        return
    ev = conn.execute(
        """INSERT INTO Evidence (PaperID, EvType, QuoteText, Locator, Extractor, Confidence, CreatedAt)
           VALUES (?,?,?,?,?,?,?)""",
        (paper_id, "abstract", abstract[:800], "Abstract", "auto", 0.8, now_iso()),
    ).lastrowid
    conn.execute(
        "INSERT INTO Fact (PaperID, FactType, Content, EvidenceID, CreatedAt) VALUES (?,?,?,?,?)",
        (paper_id, "finding", abstract[:300], ev, now_iso()),
    )
