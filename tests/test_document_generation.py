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

    def test_unreadable_documents_report_preview_failure(self):
        from fastapi.testclient import TestClient
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DOCUMENTS_ROOT", Path(directory)
        ), TestClient(app.app) as client:
            root = Path(directory)
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.write(root / "blank.pdf")
            (root / "text.txt").write_text("Example", encoding="utf-8-sig")
            (root / "binary.txt").write_bytes(b"A\x00B")
            for name in ("bad.docx", "bad.xlsx", "bad.pptx"):
                (root / name).write_bytes(b"invalid Office archive")
            for name in ("blank.pdf", "binary.txt", "bad.docx", "bad.xlsx", "bad.pptx"):
                with self.subTest(name=name):
                    params = {"path": name}
                    self.assertEqual(client.get("/api/documents/content", params=params).status_code, 415)
                    self.assertEqual(client.get("/api/documents/download", params=params).status_code, 200)
            self.assertEqual(app.read_document_content("text.txt")["content"], "Example")

    def test_chat_checks_document_reference_before_contacting_provider(self):
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DOCUMENTS_ROOT", Path(directory)
        ), patch("chat.get_provider_client") as provider, TestClient(app.app) as client:
            (Path(directory) / "large.txt").write_text("A" * 12001)
            conversation = client.post("/api/conversations").json()["id"]
            for path, expected in (("../outside.txt", 403), ("missing.txt", 404), ("large.txt", 413)):
                with self.subTest(path=path):
                    response = client.post("/api/chat", json={
                        "conversation_id": conversation, "message": "Summarize this.", "document_path": path,
                    })
                    self.assertEqual(response.status_code, expected)
            provider.assert_not_called()

    def test_native_office_creation_and_errors_through_mcp(self):
        documents = Path.home() / "Documents" / "My AI Agent"
        documents.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="office-test-", dir=documents) as directory:
            root = Path(directory)
            folder = root.relative_to(Path.home()).as_posix()
            cases = [
                ("docx", "First line\nSecond line", "Second line"),
                ("xlsx", '{"sheets":[{"name":"Data","rows":[["Label"],["Second line"]]}]}', "Second line"),
                ("pptx", '{"slides":[{"title":"","body":"First line\\nSecond line"}]}', "Second line"),
            ]
            for extension, content, expected in cases:
                with self.subTest(extension=extension):
                    result = call_mcp_tool("mac_create_document", {
                        "filename": "native", "format": extension, "folder": folder, "content": content,
                    })
                    target = root / f"native.{extension}"
                    self.assertIn(str(target), result)
                    self.assertIn(expected, app.read_document_content(
                        target.relative_to(documents).as_posix()
                    )["content"])
            with self.assertRaisesRegex(RuntimeError, "PDF content is empty"):
                call_mcp_tool("mac_create_document", {
                    "filename": "empty", "format": "pdf", "folder": folder, "content": " ",
                })
            self.assertFalse((root / "empty.pdf").exists())

    def test_layout_previews_preserve_document_structure(self):
        from docx import Document
        from openpyxl import Workbook
        from pptx import Presentation
        from pypdf import PdfWriter
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DOCUMENTS_ROOT", Path(directory)
        ), TestClient(app.app) as client:
            root = Path(directory)
            document = Document()
            document.add_heading("Heading", 1)
            document.add_table(rows=1, cols=1).cell(0, 0).text = "Cell"
            document.add_paragraph("After the table")
            document.save(root / "report.docx")
            sheet = Workbook()
            sheet.active.append(["First", None, "Third"])
            sheet.save(root / "data.xlsx")
            deck = Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = "Title"
            deck.save(root / "slides.pptx")
            pdf = PdfWriter()
            pdf.add_blank_page(width=100, height=100)
            pdf.write(root / "scan.pdf")

            blocks = app.read_document_content("report.docx", layout=True)["layout"]
            self.assertEqual([block["kind"] for block in blocks], ["paragraph", "table", "paragraph"])
            self.assertEqual(blocks[0]["style"], "Heading 1")
            self.assertEqual(app.read_document_content("data.xlsx", layout=True)["layout"][0]["rows"],
                             [["First", "", "Third"]])
            shape = app.read_document_content("slides.pptx", layout=True)["layout"][0]["shapes"][0]
            self.assertTrue(shape["heading"])
            self.assertAlmostEqual(shape["left"], 5)
            response = client.get("/api/documents/pdf", params={"path": "scan.pdf"})
            self.assertEqual(response.headers["content-type"], "application/pdf")
            self.assertEqual(response.content, (root / "scan.pdf").read_bytes())
            self.assertEqual(client.get("/api/documents/pdf", params={"path": "report.docx"}).status_code, 415)
            self.assertEqual(client.get("/api/documents/pdf", params={"path": "../scan.pdf"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
