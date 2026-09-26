from contextlib import closing, contextmanager
import hashlib
import io
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from research_swarm.downloads import ensure_paper_pdf
from research_swarm.library import Library


PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def response(body=PDF, status="200 OK", headers=None):
    fields = {"Content-Type": "application/pdf", "Content-Length": str(len(body)), **(headers or {})}
    lines = ["HTTP/1.1 " + status] + [f"{key}: {value}" for key, value in fields.items() if value is not None]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


class FakeSocket:
    def __init__(self, body, requests):
        self.body, self.requests = io.BytesIO(body), requests
        self.request = bytearray()
        self.requests.append(self.request)
    def makefile(self, mode, *args, **kwargs): return self.body
    def sendall(self, content): self.request.extend(content)
    def settimeout(self, timeout): pass
    def getpeername(self): return ("1.1.1.1", 443)
    def close(self): pass


class FakeTLS:
    check_hostname = True
    verify_mode = ssl.CERT_REQUIRED
    def wrap_socket(self, sock, server_hostname=None): return sock


@contextmanager
def wire(responses, addresses=None):
    """Replace the wire only; urllib redirects and HTTP parsing remain real."""
    pending = iter(responses)
    requests, deadlines, destinations = [], [], []
    resolved = iter(addresses) if addresses is not None else None
    def lookup(host, port, *args, **kwargs):
        ips = next(resolved) if resolved is not None else ["1.1.1.1"]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip,443)) for ip in ips]
    def connect(address, timeout=None, *args, **kwargs):
        destinations.append(address);deadlines.append(timeout)
        return FakeSocket(next(pending), requests)
    with patch("research_swarm.downloads.socket.getaddrinfo", side_effect=lookup), \
         patch("research_swarm.downloads.socket.create_connection", side_effect=connect), \
         patch("research_swarm.downloads.ssl.create_default_context", return_value=FakeTLS()):
        yield {"requests":requests,"timeouts":deadlines,"destinations":destinations}


class DownloadsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "managed"
        self.task = self.root / "tasks" / "research-one"
        self.db = self.task / "data" / "paper_research.sqlite"
        self.db.parent.mkdir(parents=True)
        (self.task / "papers").mkdir()
        (self.root / "config.json").write_text(json.dumps({"tasks":{"active_id":"research-one","root":"tasks"},"topics":[{"id":"research-one","name":"Research"}]}))
        (self.task / "task.json").write_text(json.dumps({"id":"research-one","topic_id":"research-one","name":"Research"}))
        (self.root / ".research-swarm-source.json").write_text(json.dumps({"version":1,"taskId":"research-one"}))
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript("""
                CREATE TABLE Paper (PaperID INTEGER PRIMARY KEY,Title TEXT,Abstract TEXT,DOI TEXT,ArxivId TEXT,PDFPath TEXT);
                CREATE TABLE SrcRecord (SourceID INTEGER PRIMARY KEY,PaperID INTEGER,SourceType TEXT,URL TEXT,RawJson TEXT);
                INSERT INTO Paper VALUES (7,'Known paper','Only its abstract','10.48550/arXiv.2301.12345',NULL,NULL);
                INSERT INTO Paper VALUES (12,'Other paper','Other abstract',NULL,NULL,NULL);
            """)
            conn.commit()
        self.adapter = Library(self.root)

    def execute(self, sql, params=()):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute(sql, params);conn.commit()

    def add_source(self, urls, paper_id=7):
        self.execute("INSERT INTO SrcRecord (PaperID,SourceType,URL,RawJson) VALUES (?,?,?,?)",
                     (paper_id,"openalex","https://openalex.org/work",json.dumps({"OAUrls":urls})))

    def no_doi(self):
        self.execute("UPDATE Paper SET DOI=NULL WHERE PaperID=7")

    def test_arxiv_doi_download_is_bounded_and_reopens_as_known_task_pdf(self):
        with wire([response()]) as network:
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertTrue(result.get("available"), result)
        path = self.adapter.pdf_path("7")
        self.assertTrue(path and path.is_relative_to(self.task / "papers"))
        self.assertEqual(path.read_bytes(), PDF)
        self.assertIn(b"GET /pdf/2301.12345 ",network["requests"][0])
        self.assertEqual(network["destinations"],[("1.1.1.1",443)])
        self.assertTrue(all(0 < timeout <= 20 for timeout in network["timeouts"]))
        self.assertNotIn(b"Authorization:",network["requests"][0])

    def test_unmanaged_original_source_is_never_written(self):
        (self.root / ".research-swarm-source.json").unlink()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with wire([]) as network:
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertIn("摘要",result["message"])
        self.assertEqual(network["requests"],[])
        self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.assertEqual(list((self.task / "papers").iterdir()),[])

    def test_existing_pdf_returns_without_network_even_in_original_source(self):
        pdf = self.task / "papers/0007.pdf";pdf.write_bytes(PDF)
        self.execute("UPDATE Paper SET PDFPath=? WHERE PaperID=7",(str(pdf),))
        (self.root / ".research-swarm-source.json").unlink()
        with wire([]) as network:
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertTrue(result["available"])
        self.assertEqual(network["requests"],[])

    def test_only_requested_paper_sources_are_used(self):
        self.no_doi();self.add_source(["https://arxiv.org/pdf/2301.12345"],paper_id=12)
        with wire([]) as network:
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertEqual(network["requests"],[])
        self.assertIsNone(self.adapter.pdf_path("7"))

    def test_unapproved_and_non_pdf_urls_never_become_requests(self):
        self.no_doi()
        self.add_source(["https://example.com/paper.pdf", "http://127.0.0.1/private.pdf",
                         "https://arxiv.org.evil.example/paper.pdf", "https://aclanthology.org/",
                         "https://arxiv.org/pdf/2301.12345?api_key=private"])
        with wire([]) as network:
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertEqual(network["requests"],[])

    def test_redirect_to_unapproved_host_is_blocked_before_following(self):
        with wire([response(b"",status="302 Found",headers={"Location":"https://127.0.0.1/private.pdf"})]) as network:
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertEqual(len(network["requests"]),1)
        self.assertIsNone(self.adapter.pdf_path("7"))

    def test_private_dns_address_is_rejected_before_connection(self):
        for addresses in (["127.0.0.1"],["10.2.3.4"],["169.254.169.254"],["::1"],["1.1.1.1","192.168.1.4"]):
            with self.subTest(addresses=addresses), wire([],addresses=[addresses]) as network:
                result = ensure_paper_pdf(self.adapter,"7")
                self.assertFalse(result["available"])
                self.assertEqual(network["requests"],[])

    def test_dns_wait_is_included_in_request_deadline(self):
        released = threading.Event()
        def stalled_lookup(*args, **kwargs):
            released.wait(1)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]
        started = time.monotonic()
        try:
            with wire([]) as network, \
                 patch("research_swarm.downloads.socket.getaddrinfo", side_effect=stalled_lookup), \
                 patch("research_swarm.downloads._TIMEOUT", .02):
                result = ensure_paper_pdf(self.adapter,"7")
        finally:
            released.set()
        self.assertLess(time.monotonic() - started, .5)
        self.assertFalse(result["available"])
        self.assertEqual(network["requests"],[])

    def test_redirect_dns_rebinding_cannot_connect_to_private_address(self):
        with wire([response(b"",status="302 Found",headers={"Location":"https://export.arxiv.org/pdf/2301.12345"})],
                  addresses=[["1.1.1.1"],["127.0.0.1"]]) as network:
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertEqual(len(network["requests"]),1)

    def test_content_length_limit_rejects_oversized_pdf_without_saving(self):
        with wire([response(headers={"Content-Length":"25165825"})]):
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertEqual(list((self.task / "papers").iterdir()),[])
        self.assertIsNone(self.adapter.pdf_path("7"))

    def test_stream_limit_also_rejects_oversized_body_without_length(self):
        large = b"%PDF-1.7\n" + b"x" * (24*1024*1024)
        with wire([response(large,headers={"Content-Length":None})]):
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertEqual(list((self.task / "papers").iterdir()),[])

    def test_html_disguised_as_pdf_is_not_saved(self):
        with wire([response(b"<!doctype html><title>Sign in to download</title>")]):
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertIn("摘要",result["message"])
        self.assertEqual(list((self.task / "papers").iterdir()),[])

    def test_truncated_pdf_signature_is_not_published_as_readable(self):
        with wire([response(b"%PDF-1.7\ntruncated")]):
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertIsNone(self.adapter.pdf_path("7"))
        self.assertEqual(list((self.task / "papers").iterdir()),[])

    def test_invalid_management_marker_is_a_readable_failure(self):
        (self.root / ".research-swarm-source.json").write_text("[]")
        with wire([]) as network:
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertEqual(network["requests"],[])

    def test_tries_at_most_three_existing_candidates(self):
        self.no_doi()
        self.add_source([f"https://aclanthology.org/2025.test-{number}.pdf" for number in range(5)])
        with wire([response(b"",status="404 Not Found") for _ in range(3)]) as network:
            result = ensure_paper_pdf(self.adapter,"7")
        self.assertFalse(result["available"])
        self.assertEqual(len(network["requests"]),3)
        self.assertIsNone(self.adapter.pdf_path("7"))

    def test_unknown_paper_id_cannot_create_a_file(self):
        with wire([]) as network:
            result = ensure_paper_pdf(self.adapter,"../../secret")
        self.assertFalse(result["available"])
        self.assertEqual(network["requests"],[])


if __name__ == "__main__":
    unittest.main()
