# -*- coding: utf-8 -*-
"""Download full-text PDF / OA landing as soon as a paper is ingested.

Priority:
  1. OpenAlex best_oa_location.pdf_url / oa_url (already on work if available)
  2. Unpaywall (email required) for DOI
  3. DOI landing URL as fallback (no PDF guarantee)

Files land in  <repo>/papers/<PaperID>.pdf  (or .html note if only landing).
"""

from __future__ import annotations

import json
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

socket.setdefaulttimeout(45)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from paper_research.config import load_config, storage_paths  # noqa: E402

_cfg = load_config()
_paths = storage_paths(_cfg)
ROOT = _ROOT
PDF_DIR = _paths["pdf_dir"]
UA = f"paper-research/1.0 (mailto:{_cfg['sources']['openalex']['mailto']})"
UNPAYWALL_EMAIL = _cfg["sources"].get("unpaywall", {}).get(
    "email", _cfg["sources"]["openalex"]["mailto"]
)


def _get(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _looks_like_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-" or data[:4] == b"%PDF"


def unpaywall_pdf(doi: str) -> str | None:
    if not doi:
        return None
    doi = doi.replace("https://doi.org/", "")
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(UNPAYWALL_EMAIL)}"
    try:
        data = json.loads(_get(url).decode("utf-8", errors="replace"))
    except Exception:
        return None
    best = data.get("best_oa_location") or {}
    for key in ("url_for_pdf", "url"):
        u = best.get(key)
        if u and re.match(r"https?://", u):
            return u
    for loc in data.get("oa_locations") or []:
        for key in ("url_for_pdf", "url"):
            u = loc.get(key)
            if u and re.match(r"https?://", u):
                return u
    return None


def resolve_pdf_urls(work: dict) -> list[str]:
    """Collect candidate PDF / OA URLs for a paper dict."""
    urls: list[str] = []
    for u in work.get("OAUrls") or []:
        if u and u not in urls:
            urls.append(u)
    oa = (work.get("open_access") or {})
    for key in ("oa_url",):
        u = oa.get(key)
        if u and u not in urls:
            urls.append(u)
    # primary / best locations
    for loc in (
        work.get("best_oa_location"),
        work.get("primary_location"),
        *(work.get("locations") or []),
    ):
        if not isinstance(loc, dict):
            continue
        for key in ("pdf_url", "landing_page_url"):
            u = loc.get(key)
            if u and u not in urls:
                urls.append(u)
    doi = work.get("doi") or work.get("DOI")
    if doi:
        pu = unpaywall_pdf(doi if isinstance(doi, str) else str(doi))
        if pu and pu not in urls:
            urls.append(pu)
        d = str(doi).replace("https://doi.org/", "")
        urls.append(f"https://doi.org/{d}")
    return urls


def download_one(urls: list[str], dest_base: Path) -> Path | None:
    """Try each URL; save PDF if we get one. Return saved path."""
    dest_base.parent.mkdir(parents=True, exist_ok=True)
    for url in urls:
        try:
            data = _get(url, timeout=40)
        except Exception:
            continue
        if _looks_like_pdf(data):
            path = dest_base.with_suffix(".pdf")
            path.write_bytes(data)
            return path
        # some OA servers serve PDF with odd headers — sniff
        if b"%PDF-" in data[:2048] and len(data) > 1000:
            path = dest_base.with_suffix(".pdf")
            path.write_bytes(data)
            return path
        # save landing as .html fallback only if nothing else
        if b"<html" in data[:2048].lower() or b"<!doctype" in data[:2048].lower():
            continue
    return None


def download_for_work(work: dict, paper_id: int) -> Path | None:
    """Download best available fulltext for this work into papers/."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = PDF_DIR / f"{paper_id:04d}"
    urls = resolve_pdf_urls(work)
    return download_one(urls, dest)


def download_missing(conn, limit: int = 50) -> list[tuple[int, str]]:
    """Download papers that have no PDFPath yet. Uses SrcRecord raw JSON + DOI."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        SELECT P.PaperID, P.DOI, P.OpenAlexId, P.Title, S.RawJson
        FROM Paper P
        LEFT JOIN SrcRecord S ON S.PaperID = P.PaperID
        WHERE (P.PDFPath IS NULL OR P.PDFPath = '')
        ORDER BY P.PaperID
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    done: list[tuple[int, str]] = []
    for r in rows:
        work: dict = {}
        if r["RawJson"]:
            try:
                work = json.loads(r["RawJson"])
            except Exception:
                work = {}
        if not work:
            work = {
                "doi": r["DOI"],
                "open_access": {},
                "best_oa_location": None,
                "primary_location": {"landing_page_url": r["OpenAlexId"]},
            }
            if r["DOI"]:
                pu = unpaywall_pdf(r["DOI"])
                if pu:
                    work["best_oa_location"] = {"url_for_pdf": pu}
        path = download_for_work(work, r["PaperID"])
        if path:
            conn.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=?", (str(path), r["PaperID"]))
            done.append((r["PaperID"], str(path)))
            print(f"  downloaded #{r['PaperID']} -> {path.name}")
        else:
            print(f"  skip #{r['PaperID']} (no OA PDF): {(r['Title'] or '')[:50]}")
    conn.commit()
    return done


if __name__ == "__main__":
    import argparse
    import sqlite3

    ap = argparse.ArgumentParser(description="Download missing OA PDFs")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    conn = sqlite3.connect(str(_paths["db"]))
    conn.row_factory = sqlite3.Row
    done = download_missing(conn, limit=args.limit)
    print(f"downloaded {len(done)} PDFs -> {PDF_DIR}")
    conn.close()
