import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_client import call_mcp_tool
from pypdf import PdfReader

import app
import mcp_server


class PDFGenerationTests(unittest.TestCase):
    def test_mcp_creates_and_verifies_readable_pdf(self):
        documents_root = Path.home() / "Documents" / "My AI Agent"
        documents_root.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(tempfile.mkdtemp(prefix='pdf-test-', dir=documents_root))
        folder = temporary_dir.relative_to(Path.home()).as_posix()
        filename = "pdf_generation_regression_test.pdf"
        expected_path = temporary_dir / filename
        try:
            result = call_mcp_tool(
                "mac_create_document",
                {
                    "filename": filename,
                    "format": "pdf",
                    "folder": folder,
                    "content": "# PDF Regression Test\n## List rendering\n- First bullet\n* Second bullet\n"
                    + ("Long paragraph " * 120 + "\n") * 12
                    + "Source: https://example.org/report",
                },
            )
            self.assertIn("Document created successfully:", result)
            self.assertTrue(expected_path.is_file())
            self.assertGreater(expected_path.stat().st_size, 0)
            self.assertTrue(expected_path.read_bytes().startswith(b"%PDF-"))
            reader = PdfReader(str(expected_path), strict=True)
            self.assertGreater(len(reader.pages), 1)
            extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertIn("PDF Regression Test", extracted)
            self.assertIn("List rendering", extracted)
            self.assertIn("- First bullet", extracted)
            self.assertIn("- Second bullet", extracted)
            self.assertIn("https://example.org/report", extracted)
            relative = expected_path.relative_to(documents_root).as_posix()
            self.assertIn("PDF Regression Test", app.read_document_content(relative)["content"])
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)

    def test_pdf_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            mcp_server, "MAC_HOME", Path(directory)
        ):
            target = Path(directory) / "report.pdf"
            target.write_bytes(b"previous content")
            with self.assertRaisesRegex(ValueError, "empty"):
                mcp_server.mac_create_document("report", content=" ", format="pdf", folder=".", overwrite=True)
            with patch("reportlab.platypus.SimpleDocTemplate.build"):
                with self.assertRaisesRegex(RuntimeError, "Could not verify PDF"):
                    mcp_server.mac_create_document("report", content="New content", format="pdf", folder=".", overwrite=True)
            self.assertEqual(target.read_bytes(), b"previous content")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_original_download_and_preview_failure(self):
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory, TestClient(app.app) as client:
            root = Path(directory) / "documents"
            root.mkdir()
            payload = b"this PDF is corrupt"
            (root / "report.pdf").write_bytes(payload)
            (root / ".hidden").write_text("private")
            (Path(directory) / "outside.txt").write_text("outside")
            (root / "link.txt").symlink_to(Path(directory) / "outside.txt")
            with patch.object(app, "DOCUMENTS_ROOT", root):
                response = client.get("/api/documents/download", params={"path": "report.pdf"})
                self.assertEqual(response.content, payload)
                self.assertIn("attachment", response.headers["content-disposition"])
                self.assertEqual(client.get("/api/documents/content", params={"path": "report.pdf"}).status_code, 415)
                for path in (".hidden", "../outside.txt", "link.txt"):
                    self.assertEqual(client.get("/api/documents/download", params={"path": path}).status_code, 403)
                self.assertEqual(client.get("/api/documents/download", params={"path": "missing.pdf"}).status_code, 404)

    def test_office_previews_keep_line_breaks(self):
        from docx import Document
        from openpyxl import Workbook
        from pptx import Presentation

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DOCUMENTS_ROOT", Path(directory)
        ):
            root = Path(directory)
            document = Document()
            document.add_paragraph("First paragraph")
            document.add_paragraph("Second paragraph")
            document.save(root / "notes.docx")
            workbook = Workbook()
            workbook.active.append(["First row"])
            workbook.active.append(["Second row"])
            workbook.save(root / "data.xlsx")
            slides = Presentation()
            slide = slides.slides.add_slide(slides.slide_layouts[1])
            slide.shapes.title.text = "Title"
            slide.placeholders[1].text = "Slide body"
            slides.save(root / "deck.pptx")

            for name, expected in (
                ("notes.docx", "First paragraph\nSecond paragraph"),
                ("data.xlsx", "First row\nSecond row"),
                ("deck.pptx", "Title\nSlide body"),
            ):
                with self.subTest(name=name):
                    self.assertIn(expected, app.read_document_content(name)["content"])


if __name__ == "__main__":
    unittest.main()
