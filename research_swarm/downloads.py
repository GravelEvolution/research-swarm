"""Fetch one known paper's public PDF; no keys, scraping, or paywall bypass.

Only task sources owned by prepare_source may be changed. Candidate URLs come
from this paper's source records or a recognized arXiv identifier. HTTPS, an
academic-host allowlist, public DNS, pinned IP connections, bounded redirects,
size/time limits, and a PDF signature are enforced before database publication.
"""

from __future__ import annotations

from contextlib import closing
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import socket
import sqlite3
import ssl
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, build_opener
import uuid


_MAX_BYTES = 24 * 1024 * 1024
_TIMEOUT = 20.0
_ACADEMIC_HOSTS = frozenset({
    "arxiv.org", "www.arxiv.org", "export.arxiv.org", "openreview.net",
    "aclanthology.org", "proceedings.mlr.press", "jmlr.org", "www.jmlr.org",
    "papers.nips.cc", "papers.neurips.cc", "proceedings.neurips.cc",
    "openaccess.thecvf.com", "www.cv-foundation.org", "proceedings.iclr.cc",
    "pmc.ncbi.nlm.nih.gov", "europepmc.org", "zenodo.org", "hal.science",
    "www.nature.com", "nature.com", "link.springer.com", "www.frontiersin.org",
    "journals.plos.org", "www.pnas.org", "proceedings.univie.ac.at",
})
_ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})(?:v\d+)?", re.I)
_LOCK_GUARD = threading.Lock()
_PAPER_LOCKS: dict[tuple[str, str], threading.Lock] = {}


class _Unavailable(ValueError):
    """A safe, user-readable reason for keeping the abstract."""


def _url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 4096 or re.search(r"[\x00-\x20\\]", value):
        raise _Unavailable("公开全文地址无效")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() != "https" or host not in _ACADEMIC_HOSTS:
            raise _Unavailable("全文来源不在允许的公开学术 HTTPS 域名中")
        if parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
            raise _Unavailable("全文地址包含凭证或不允许的端口")
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            if key.lower() in {"key", "email", "mailto"} or any(word in key.lower() for word in ("api_key", "apikey", "token", "secret", "password", "auth", "signature")):
                raise _Unavailable("全文地址需要凭证，未使用该地址")
        return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
    except ValueError as error:
        if isinstance(error, _Unavailable):
            raise
        raise _Unavailable("公开全文地址无效") from None


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        mapped = getattr(ip, "ipv4_mapped", None)
        return ip.is_global and (mapped is None or mapped.is_global)
    except ValueError:
        return False


def _public_addresses(host: str, deadline: float) -> list[str]:
    # System DNS has no portable timeout argument. A daemon resolver bounds the
    # caller's wait; a late result cannot initiate a connection or write a file.
    result = queue.Queue(maxsize=1)
    def resolve():
        try:
            result.put((True, socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
        except OSError as error:
            result.put((False, error))
    threading.Thread(target=resolve, name="public-pdf-dns", daemon=True).start()
    try:
        success, records = result.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        raise TimeoutError("public PDF DNS deadline") from None
    if not success:
        raise records
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not _is_public(address) for address in addresses):
        raise _Unavailable("公开全文域名解析到非公网地址，未建立连接")
    return sorted(addresses, key=lambda address: ":" in address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, addresses, deadline, **kwargs):
        self._addresses, self._deadline = addresses, deadline
        super().__init__(host, **kwargs)

    def connect(self):
        # No proxy/tunnel and no second DNS lookup: connect to a validated IP,
        # while TLS still verifies the original academic hostname and certificate.
        if self._tunnel_host:
            raise _Unavailable("公开全文下载不使用代理隧道")
        last_error = None
        for address in self._addresses:
            remaining = min(_TIMEOUT, self._deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError("public PDF request deadline")
            raw = None
            try:
                raw = socket.create_connection((address, self.port), timeout=remaining,
                                               source_address=self.source_address)
                if not _is_public(raw.getpeername()[0]):
                    raise _Unavailable("全文连接目标不是公网地址")
                remaining = min(_TIMEOUT, self._deadline - time.monotonic())
                if remaining <= 0:
                    raise TimeoutError("public PDF TLS deadline")
                raw.settimeout(remaining)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except (OSError, _Unavailable) as error:
                if raw is not None:
                    raw.close()
                last_error = error
        if last_error:
            raise last_error
        raise _Unavailable("无法连接公开全文来源")


class _PublicHTTPSHandler(HTTPSHandler):
    def __init__(self, deadline):
        self._deadline = deadline
        super().__init__(context=ssl.create_default_context())

    def https_open(self, request):
        clean = _url(request.full_url)
        addresses = _public_addresses(urlsplit(clean).hostname, self._deadline)
        remaining = min(_TIMEOUT, self._deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("public PDF request deadline")
        request.timeout = remaining
        def connection(host, **kwargs):
            return _PinnedHTTPSConnection(host, addresses, self._deadline, **kwargs)
        return self.do_open(connection, request, context=self._context)


class _PublicRedirect(HTTPRedirectHandler):
    max_repeats = 1
    max_redirections = 2

    def redirect_request(self, request, response, code, message, headers, new_url):
        _url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def _fetch_pdf(url: str) -> bytes:
    url = _url(url)
    deadline = time.monotonic() + _TIMEOUT
    opener = build_opener(ProxyHandler({}), _PublicHTTPSHandler(deadline), _PublicRedirect())
    opener.addheaders = [("User-Agent", "research-swarm/1.0 (public-paper-reader)"),
                         ("Accept", "application/pdf"), ("Accept-Encoding", "identity")]
    with opener.open(url, timeout=_TIMEOUT) as response:
        _url(response.geturl())
        if response.status != 200:
            raise _Unavailable("公开全文来源未返回完整文件")
        if "text/html" in (response.headers.get("Content-Type") or "").lower():
            raise _Unavailable("公开地址返回网页而非 PDF")
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                raise _Unavailable("全文大小声明无效") from None
            if length < 0 or length > _MAX_BYTES:
                raise _Unavailable("公开 PDF 超过 24 MiB 限制")
        data = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("public PDF body deadline")
            raw = getattr(getattr(response, "fp", None), "raw", None)
            sock = getattr(raw, "_sock", None)
            if sock is not None:
                sock.settimeout(min(_TIMEOUT, remaining))
            block = response.read(min(65536, _MAX_BYTES - len(data) + 1))
            if not block:
                break
            data.extend(block)
            if len(data) > _MAX_BYTES:
                raise _Unavailable("公开 PDF 超过 24 MiB 限制")
            if len(data) >= 5 and not data.startswith(b"%PDF-"):
                raise _Unavailable("公开地址没有返回有效 PDF 文件头")
        if not data.startswith(b"%PDF-"):
            raise _Unavailable("公开地址没有返回有效 PDF 文件头")
        if declared is not None and len(data) != length:
            raise _Unavailable("公开 PDF 下载未完成")
        if b"%%EOF" not in data[-2048:]:
            raise _Unavailable("公开 PDF 缺少完整文件结束标记")
        return bytes(data)


def _arxiv_identifier(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^10\.48550/arxiv\.", "", value, flags=re.I)
    value = re.sub(r"^arxiv:", "", value, flags=re.I)
    return value if _ARXIV_ID.fullmatch(value) else None


def _candidate(value, explicit=False) -> str | None:
    if not isinstance(value, str):
        return None
    # Older public metadata uses http arXiv links. Upgrade before any request.
    value = re.sub(r"^http://", "https://", value.strip(), flags=re.I)
    try:
        clean = _url(value)
    except _Unavailable:
        return None
    parsed = urlsplit(clean)
    if parsed.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        identifier = re.sub(r"^/(?:abs|pdf)/", "", parsed.path)
        identifier = re.sub(r"\.pdf$", "", identifier, flags=re.I)
        if _ARXIV_ID.fullmatch(identifier):
            return "https://arxiv.org/pdf/" + identifier
    if parsed.hostname == "openreview.net" and parsed.path in {"/forum", "/pdf"}:
        identifier = dict(parse_qsl(parsed.query)).get("id", "")
        if re.fullmatch(r"[A-Za-z0-9_-]{4,128}", identifier):
            return "https://openreview.net/pdf?" + urlencode({"id": identifier})
    if explicit or parsed.path.lower().endswith(".pdf"):
        return clean
    return None


def _candidates(paper: dict, source_rows: list[dict]) -> list[str]:
    candidates = []
    def add(value, explicit=False):
        candidate = _candidate(value, explicit)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for row in source_rows:
        raw = row.get("RawJson") or ""
        try:
            data = json.loads(raw) if isinstance(raw, str) and len(raw) <= 512 * 1024 else {}
        except ValueError:
            data = {}
        if isinstance(data, dict):
            for key in ("pdf_url", "PDFURL", "url_for_pdf"):
                add(data.get(key), True)
            for item in data.get("OAUrls", []) if isinstance(data.get("OAUrls"), list) else []:
                add(item)
            locations = [data.get("best_oa_location"), data.get("primary_location")]
            for key in ("locations", "oa_locations"):
                if isinstance(data.get(key), list):
                    locations.extend(data[key])
            for location in locations:
                if isinstance(location, dict):
                    add(location.get("pdf_url") or location.get("url_for_pdf"), True)
                    add(location.get("landing_page_url"))
            if isinstance(data.get("open_access"), dict):
                add(data["open_access"].get("oa_url"))
            for link in data.get("link", []) if isinstance(data.get("link"), list) else []:
                if isinstance(link, dict) and link.get("content-type") == "application/pdf":
                    add(link.get("URL"), True)
            add(data.get("URL") or data.get("url"))
        add(row.get("URL"))
    for value in (paper.get("ArxivId"), paper.get("DOI")):
        identifier = _arxiv_identifier(value)
        if identifier:
            add("https://arxiv.org/pdf/" + identifier)
    return candidates[:3]


def _managed(adapter) -> dict:
    layout = adapter._layout()
    root = layout["root"].resolve()
    marker = root / ".research-swarm-source.json"
    if not marker.resolve().is_relative_to(root) or not marker.is_file():
        raise _Unavailable("仅在独立受管任务中获取全文，原始资料库保持只读")
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise _Unavailable("独立任务标记无法校验") from None
    task_id = manifest.get("taskId") if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", task_id):
        raise _Unavailable("独立任务标记无法校验")
    task = (root / "tasks" / task_id).resolve()
    db = (task / "data" / "paper_research.sqlite").resolve()
    pdf = (task / "papers").resolve()
    if not all(path.is_relative_to(root) for path in (task, db, pdf)):
        raise _Unavailable("独立任务的全文存储路径越界")
    if layout["db"].resolve() != db or layout["activeDb"].resolve() != db or not db.is_file():
        raise _Unavailable("当前资料库不是该独立任务的资料库")
    return {"root": root, "db": db, "pdf": pdf}


def _paper_records(db: Path, paper_id: str) -> tuple[dict | None, list[dict]]:
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("SELECT * FROM Paper WHERE PaperID=?", (paper_id,)).fetchone()
        if row is None:
            return None, []
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND lower(name)='srcrecord'").fetchone()
        sources = [dict(record) for record in connection.execute(
            "SELECT * FROM SrcRecord WHERE PaperID=? LIMIT 20", (paper_id,))] if exists else []
        return dict(row), sources


def _save(adapter, layout, paper, paper_id, data) -> Path:
    current = _managed(adapter)
    if current != layout:
        raise _Unavailable("下载期间任务来源发生变化，未写入全文")
    directory = layout["pdf"]
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{int(paper_id):04d}" if paper_id.isdecimal() else hashlib.sha256(paper_id.encode()).hexdigest()[:16]
    final = directory / (stem + "-" + uuid.uuid4().hex[:8] + ".pdf")
    temporary = None
    committed = False
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".paper-", suffix=".part", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        os.replace(temporary, final)
        temporary = None
        with closing(sqlite3.connect(layout["db"].as_uri() + "?mode=rw", uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM Paper WHERE PaperID=?", (paper_id,)).fetchone()
                if row is None or any(dict(row).get(key) != paper.get(key) for key in ("Title", "DOI", "ArxivId")):
                    raise _Unavailable("下载期间论文记录发生变化，未关联全文")
                updated = connection.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=? AND COALESCE(PDFPath,'')=?",
                                             (str(final), paper_id, paper.get("PDFPath") or ""))
                if updated.rowcount != 1:
                    raise _Unavailable("论文全文记录已由另一次操作更新")
            committed = True
        return final
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if not committed:
            final.unlink(missing_ok=True)


def ensure_paper_pdf(adapter, paper_id) -> dict:
    """Return ``{available,message}``; only publish a verified task-local PDF."""
    paper_id = str(paper_id)
    try:
        if adapter.pdf_path(paper_id):
            return {"available": True, "message": "本地 PDF 已可读取。"}
        layout = _managed(adapter)
        with _LOCK_GUARD:
            lock = _PAPER_LOCKS.setdefault((str(layout["root"]), paper_id), threading.Lock())
        with lock:
            if adapter.pdf_path(paper_id):
                return {"available": True, "message": "本地 PDF 已可读取。"}
            paper, sources = _paper_records(layout["db"], paper_id)
            if paper is None:
                return {"available": False, "message": "未找到这篇论文，未下载文件。"}
            candidates = _candidates(paper, sources)
            if not candidates:
                return {"available": False, "message": "已有来源未提供可直接获取的公开 PDF，保留摘要。"}
            reason = "公开全文暂不可用或来源未开放 PDF"
            for candidate in candidates:
                try:
                    data = _fetch_pdf(candidate)
                    path = _save(adapter, layout, paper, paper_id, data)
                    verified = adapter.pdf_path(paper_id)
                    if verified and verified.resolve() == path.resolve():
                        return {"available": True, "message": "已获取并校验公开 PDF，可尝试读取全文。"}
                    return {"available": False, "message": "已保存全文，但读取校验未通过；暂保留摘要。"}
                except _Unavailable as error:
                    reason = str(error)
                except (OSError, URLError, http.client.HTTPException, sqlite3.DatabaseError, TimeoutError):
                    reason = "公开全文连接失败、超时或暂不可读"
            return {"available": False, "message": reason + "，保留摘要。"}
    except _Unavailable as error:
        return {"available": False, "message": str(error) + "；保留摘要。"}
    except (OSError, ValueError, sqlite3.DatabaseError):
        return {"available": False, "message": "无法校验该任务的论文资料，保留摘要。"}
