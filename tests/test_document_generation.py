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


if __name__ == "__main__":
    unittest.main()
