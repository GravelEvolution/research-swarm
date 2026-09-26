"""Read real PDF page text only through a verified source-library adapter."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path


MAX_PDF_BYTES = 24 * 1024 * 1024
MAX_PAGE_COUNT = 5
MAX_QUOTE_CHARS = 6000


def read_pdf_evidence(library_adapter, paper_id, page_start=1, page_count=3) -> dict:
    """Return text and page evidence from an adapter-verified, immutable byte snapshot.

    Page numbers are one based. Invalid windows raise a safe ``ValueError``;
    unavailable/unreadable material returns an empty result with limitations.
    This reads no external URL, performs no OCR, and never changes the source.
    """
    if isinstance(page_start, bool) or not isinstance(page_start, int) or page_start < 1:
        raise ValueError("PDF 起始页必须是从 1 开始的整数")
    if isinstance(page_count, bool) or not isinstance(page_count, int) or not 1 <= page_count <= MAX_PAGE_COUNT:
        raise ValueError("每次 PDF 全文读取只能请求 1 至 5 页")

    paper_id = str(paper_id)
    result = {"paperId": paper_id, "totalPages": 0, "pages": [], "evidence": [], "limitations": []}
    limitations = result["limitations"]
    try:
        path = library_adapter.pdf_path(paper_id)
    except Exception:
        # Source configuration and filesystem exceptions may contain secrets.
        limitations.append("无法核验该论文的本地 PDF 来源；未读取全文。")
        return result
    if path is None:
        limitations.append("该论文暂无已验证的本地 PDF；未读取全文，不能生成全文证据。")
        return result
    try:
        with Path(path).open("rb") as file:
            if os.fstat(file.fileno()).st_size > MAX_PDF_BYTES:
                limitations.append("PDF 超过单文件 24 MB 读取上限；未读取全文。")
                return result
            raw = file.read(MAX_PDF_BYTES + 1)
        if len(raw) > MAX_PDF_BYTES:
            limitations.append("PDF 超过单文件 24 MB 读取上限；未读取全文。")
            return result
    except Exception:
        limitations.append("已登记的本地 PDF 无法读取，可能已移动或不可访问；未读取全文。")
        return result

    try:
        from pypdf import PdfReader
        from pypdf.errors import FileNotDecryptedError, WrongPasswordError
    except ImportError:
        limitations.append("缺少 pypdf 全文读取依赖，请先安装 requirements.txt；未读取全文。")
        return result

    # The exact bytes that determine the ID are also the bytes parsed below.
    # A source replacement during extraction cannot mix old text with a new hash.
    digest = hashlib.sha256(raw).hexdigest()[:12]
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            limitations.append("PDF 已加密且需要密码；未读取全文，未生成全文证据。")
            return result
        total_pages = len(reader.pages)
    except (FileNotDecryptedError, WrongPasswordError):
        limitations.append("PDF 已加密且需要密码；未读取全文，未生成全文证据。")
        return result
    except Exception:
        limitations.append("PDF 已损坏或其格式无法解析；未读取全文，未生成全文证据。")
        return result

    result["totalPages"] = total_pages
    if page_start > total_pages:
        limitations.append(f"请求起始页超出 PDF 总页数（{total_pages} 页）；未读取该范围。")
        return result
    for number in range(page_start, min(total_pages + 1, page_start + page_count)):
        try:
            text = (reader.pages[number - 1].extract_text() or "").strip()
        except Exception:
            limitations.append(f"PDF 第 {number} 页文字提取失败；未生成该页的全文证据。")
            continue
        result["pages"].append({"page": number, "text": text})
        if not text:
            limitations.append(f"PDF 第 {number} 页没有可提取文字，可能为扫描、图片或空白页；未执行 OCR，未生成该页的全文证据。")
            continue
        result["evidence"].append({
            "id": f"pdf:{paper_id}:{digest}:p{number}", "paperId": paper_id,
            "quote": text[:MAX_QUOTE_CHARS], "locator": f"PDF 第 {number} 页",
            "type": "full_text", "extractor": "pypdf", "confidence": 1.0,
        })
        if len(text) > MAX_QUOTE_CHARS:
            limitations.append(f"PDF 第 {number} 页证据摘录仅保留前 6000 个字符；完整提取文字保留在 pages 中。")
    return result
