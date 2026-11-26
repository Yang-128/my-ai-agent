import shutil
import tempfile
import unittest
from pathlib import Path

from mcp_client import call_mcp_tool
from pypdf import PdfReader


class PDFGenerationTests(unittest.TestCase):
    def test_mcp_creates_and_verifies_readable_pdf(self):
        documents_root = Path.home() / "Documents" / "My AI Agent"
        documents_root.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(tempfile.mkdtemp(prefix='.pdf-test-', dir=documents_root))
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
                    "content": "# PDF Regression Test\n## List rendering\n- First bullet\n* Second bullet\n",
                },
            )
            self.assertIn("Document created successfully:", result)
            self.assertTrue(expected_path.is_file())
            self.assertGreater(expected_path.stat().st_size, 0)
            self.assertTrue(expected_path.read_bytes().startswith(b"%PDF-"))
            reader = PdfReader(str(expected_path), strict=True)
            self.assertGreaterEqual(len(reader.pages), 1)
            extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertIn("PDF Regression Test", extracted)
            self.assertIn("List rendering", extracted)
            self.assertIn("- First bullet", extracted)
            self.assertIn("- Second bullet", extracted)
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
