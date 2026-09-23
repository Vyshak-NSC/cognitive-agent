import os
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen import canvas


class DocumentCognitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROJECTS_ROOT"] = self.tmp.name
        # Import after PROJECTS_ROOT is set because settings reads it at import time.
        from ragapp.cognition.store import CognitionStore
        self.CognitionStore = CognitionStore

    def tearDown(self):
        self.tmp.cleanup()

    def test_pdf_chapter_range_and_direct_read(self):
        store = self.CognitionStore("u", "p")
        store.ensure_initialized()
        root = store.source
        path = root / "novel.pdf"
        c = canvas.Canvas(str(path))
        c.drawString(50, 750, "Chapter 1: Arrival")
        c.drawString(50, 720, "Elara arrived at Veyr.")
        c.showPage()
        c.drawString(50, 750, "More of chapter one.")
        c.showPage()
        c.drawString(50, 750, "Chapter 2: Oath")
        c.drawString(50, 720, "Rowan refused.")
        c.save()

        from ragapp.document_parser import parse_document
        artifact = store.register_artifact("source", "novel.pdf")
        parsed = parse_document(path, "source", 5000)
        parsed.path = "novel.pdf"
        parsed.artifact_id = artifact["id"]
        store.document_index.replace_document(parsed)

        section = store.resolve_document_section("Chapter 1")["section"]
        self.assertEqual(section["locator"]["page_start"], 1)
        self.assertEqual(section["locator"]["page_end"], 2)
        fetched = store.read_source_location(artifact["id"], section["locator"])
        self.assertEqual([p["page"] for p in fetched["pages"]], [1, 2])
        self.assertIn("Elara arrived", fetched["pages"][0]["text"])

    def test_index_never_persists_source_text(self):
        store = self.CognitionStore("u", "p")
        store.ensure_initialized()
        root = store.source
        path = root / "novel.txt"
        path.write_text("Chapter 1: A\n\nsecret body text", encoding="utf-8")
        from ragapp.document_parser import parse_document
        artifact = store.register_artifact("source", "novel.txt")
        parsed = parse_document(path, "source", 5000)
        parsed.path = "novel.txt"
        parsed.artifact_id = artifact["id"]
        store.document_index.replace_document(parsed)
        raw = store.document_index.path.read_text(encoding="utf-8")
        self.assertNotIn("secret body text", raw)


if __name__ == "__main__":
    unittest.main()

class MetadataFirstRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PROJECTS_ROOT"] = self.tmp.name
        from ragapp.cognition.store import CognitionStore
        self.CognitionStore = CognitionStore

    def tearDown(self):
        self.tmp.cleanup()

    def test_metadata_search_prefers_specific_numbered_section(self):
        store = self.CognitionStore("u", "p")
        store.ensure_initialized()
        path = store.source / "policy.pdf"
        c = canvas.Canvas(str(path))
        c.drawString(50, 750, "3. Identity and Access Management")
        c.drawString(50, 730, "3.1 Account Provisioning")
        c.drawString(50, 710, "Access requests require approval.")
        c.drawString(50, 690, "3.4 Privileged Access")
        c.drawString(50, 670, "Privileged accounts require stronger authentication.")
        c.save()
        from ragapp.document_parser import parse_document
        artifact = store.register_artifact("source", "policy.pdf")
        parsed = parse_document(path, "source", 5000)
        parsed.path = "policy.pdf"
        parsed.artifact_id = artifact["id"]
        store.document_index.replace_document(parsed)
        candidates = store.search_cognition_metadata("requirements for privileged access", limit=5)["candidates"]
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["name"], "3.4 Privileged Access")
        self.assertFalse(candidates[0].get("source_content"))
        fetched = store.read_source_location(artifact["id"], candidates[0]["locator"])
        text = fetched["pages"][0]["text"]
        self.assertIn("3.4 Privileged Access", text)
        self.assertIn("Privileged accounts require stronger authentication", text)
        self.assertNotIn("Access requests require approval", text)

    def test_generic_cognition_retrieval_never_includes_source_body(self):
        store = self.CognitionStore("u", "p")
        store.ensure_initialized()
        meta = store.master_metadata()
        meta.setdefault("entities", {})["control:1"] = {
            "id": "control:1", "name": "Privileged Access", "type": "control",
            "summary": "Privileged accounts require review.", "path": "cognition/entities/control/1.json",
            "locations": [], "attributes": {}
        }
        store._write_master(meta)
        result = store.retrieve([{"entity_id": "control:1", "detail": "full", "include_source": True}], max_chars=5000)
        self.assertNotIn("source_content", result["requests"][0])
