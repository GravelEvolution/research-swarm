"""PDF text evidence uses real PDF bytes; fixtures never touch the source library."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from research_swarm.fulltext import read_pdf_evidence


class FixtureAdapter:
    def __init__(self, path):
        self.path = path

    def pdf_path(self, paper_id):
        return self.path if paper_id == "7" else None


class FullTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "known.pdf"
        self.adapter = FixtureAdapter(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def write_pdf(self, texts, password=None):
        writer = PdfWriter()
        for text in texts:
            page = writer.add_blank_page(width=612, height=792)
            if text is not None:
                font = DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                })
                page[NameObject("/Resources")] = DictionaryObject({
                    NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
                })
                escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                stream = DecodedStreamObject()
                stream.set_data(("BT /F1 12 Tf 30 700 Td (" + escaped + ") Tj ET").encode("ascii"))
                page[NameObject("/Contents")] = writer._add_object(stream)
        if password is not None:
            writer.encrypt(password)
        with self.path.open("wb") as file:
            writer.write(file)
        return self.path.read_bytes()

    def test_real_text_has_page_and_file_hash_provenance_without_source_writes(self):
        original = self.write_pdf(["Page one observed result.", "Page two method.", "Page three limit.", "Page four unused."])
        before = self.path.stat().st_mtime_ns
        result = read_pdf_evidence(self.adapter, "7")
        digest = hashlib.sha256(original).hexdigest()[:12]
        self.assertEqual(result["paperId"], "7")
        self.assertEqual(result["totalPages"], 4)
        self.assertEqual([p["page"] for p in result["pages"]], [1, 2, 3])
        self.assertEqual(result["pages"][0]["text"], "Page one observed result.")
        self.assertEqual(result["evidence"][0], {
            "id": f"pdf:7:{digest}:p1", "paperId": "7",
            "quote": "Page one observed result.", "locator": "PDF 第 1 页",
            "type": "full_text", "extractor": "pypdf", "confidence": 1.0,
        })
        self.assertEqual(result["limitations"], [])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_reads_requested_one_based_window_with_maximum_five_pages(self):
        self.write_pdf([f"Observed page {number}." for number in range(1, 9)])
        result = read_pdf_evidence(self.adapter, "7", page_start=2, page_count=5)
        self.assertEqual(result["totalPages"], 8)
        self.assertEqual([p["page"] for p in result["pages"]], [2, 3, 4, 5, 6])
        self.assertEqual(result["evidence"][-1]["quote"], "Observed page 6.")

    def test_invalid_ranges_fail_before_any_pdf_lookup(self):
        class NoLookup:
            def pdf_path(self, paper_id):
                raise AssertionError("Invalid range must be rejected before source access")
        for start, count in ((0, 1), (-1, 1), (1, 0), (1, 6), (1, True), (False, 1), ("1", 1), (1, 1.5)):
            with self.subTest(start=start, count=count), self.assertRaises(ValueError):
                read_pdf_evidence(NoLookup(), "7", start, count)

    def test_short_final_window_does_not_invent_pages(self):
        self.write_pdf(["First.", "Last."])
        result = read_pdf_evidence(self.adapter, "7", page_start=2, page_count=5)
        self.assertEqual([p["page"] for p in result["pages"]], [2])
        self.assertEqual(len(result["evidence"]), 1)

    def test_page_past_document_returns_visible_gap(self):
        self.write_pdf(["Only page."])
        result = read_pdf_evidence(self.adapter, "7", page_start=2)
        self.assertEqual(result["totalPages"], 1)
        self.assertEqual(result["pages"], [])
        self.assertEqual(result["evidence"], [])
        self.assertTrue(result["limitations"])

    def test_unknown_paper_or_unavailable_path_has_no_fabricated_evidence(self):
        for adapter, paper_id in ((self.adapter, "999"), (FixtureAdapter(None), "7"), (self.adapter, "7")):
            with self.subTest(paper_id=paper_id, path=adapter.path):
                result = read_pdf_evidence(adapter, paper_id)
                self.assertEqual(result["totalPages"], 0)
                self.assertEqual(result["pages"], [])
                self.assertEqual(result["evidence"], [])
                self.assertTrue(result["limitations"])

    def test_image_or_blank_page_is_not_fulltext_evidence(self):
        self.write_pdf([None, "Actual extracted method.", "  "])
        result = read_pdf_evidence(self.adapter, "7")
        self.assertEqual([p["page"] for p in result["pages"]], [1, 2, 3])
        self.assertEqual(result["pages"][0]["text"], "")
        self.assertEqual([e["locator"] for e in result["evidence"]], ["PDF 第 2 页"])
        self.assertTrue(any("OCR" in text for text in result["limitations"]))

    def test_quote_is_real_prefix_bounded_to_6000_characters(self):
        content = "Actual measurement text " * 350
        self.write_pdf([content])
        result = read_pdf_evidence(self.adapter, "7", page_count=1)
        page_text = result["pages"][0]["text"]
        self.assertGreater(len(page_text), 6000)
        self.assertEqual(result["evidence"][0]["quote"], page_text[:6000])
        self.assertTrue(result["limitations"])

    def test_replaced_pdf_gets_new_evidence_id(self):
        self.write_pdf(["Original measurement."])
        first = read_pdf_evidence(self.adapter, "7")
        self.write_pdf(["Corrected measurement."])
        second = read_pdf_evidence(self.adapter, "7")
        self.assertNotEqual(first["evidence"][0]["id"], second["evidence"][0]["id"])
        self.assertEqual(second["evidence"][0]["quote"], "Corrected measurement.")

    def test_file_over_24_megabytes_is_rejected_without_parsing(self):
        with self.path.open("wb") as file:
            file.write(b"%PDF-1.7\n")
            file.truncate(24 * 1024 * 1024 + 1)
        result = read_pdf_evidence(self.adapter, "7")
        self.assertEqual(result["evidence"], [])
        self.assertTrue(any("24" in text for text in result["limitations"]))

    def test_corrupt_pdf_returns_a_material_gap_without_raw_error_details(self):
        secret = "sk-private-must-never-appear"
        self.path.write_bytes(("%PDF-1.7\n" + secret + "\n%%EOF\n").encode())
        result = read_pdf_evidence(self.adapter, "7")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["pages"], [])
        self.assertTrue(result["limitations"])
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(str(self.path), json.dumps(result))

    def test_adapter_error_is_redacted(self):
        class FailedAdapter:
            def pdf_path(self, paper_id):
                raise RuntimeError("api_key=private-value; secret/config.json")
        result = read_pdf_evidence(FailedAdapter(), "7")
        self.assertEqual(result["evidence"], [])
        self.assertTrue(result["limitations"])
        self.assertNotIn("private-value", json.dumps(result))
        self.assertNotIn("secret/config", json.dumps(result))

    def test_password_protected_pdf_is_not_reported_as_read(self):
        self.write_pdf(["Protected measurement."], password="private-password")
        result = read_pdf_evidence(self.adapter, "7")
        self.assertEqual(result["pages"], [])
        self.assertEqual(result["evidence"], [])
        self.assertTrue(any("加密" in text or "密码" in text for text in result["limitations"]))
        self.assertNotIn("private-password", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
