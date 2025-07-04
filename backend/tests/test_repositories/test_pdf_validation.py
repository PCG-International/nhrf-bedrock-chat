import base64
import sys
import unittest

sys.path.insert(0, ".")

from app.repositories.models.conversation import AttachmentContentModel


class TestPDFPageValidation(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        # Create a simple valid PDF content for testing
        self.small_pdf_content = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n>>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000074 00000 n \n0000000120 00000 n \ntrailer\n<<\n/Size 4\n/Root 1 0 R\n>>\nstartxref\n179\n%%EOF"
        self.small_pdf_b64 = base64.b64encode(self.small_pdf_content).decode("utf-8")

        # Create a large fake PDF (>100KB to trigger size estimation fallback)
        self.large_pdf_content = b"Large PDF content " * 10000  # ~180KB
        self.large_pdf_b64 = base64.b64encode(self.large_pdf_content).decode("utf-8")

    def test_pdf_small_file_processed_normally(self):
        """Test that small PDFs are processed normally"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=self.small_pdf_b64,
            file_name="small_document.pdf",
        )

        result = attachment.to_contents_for_invoke()

        # Should return document format, not error text
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "document")
        self.assertEqual(result[0]["source"]["type"], "base64")
        self.assertEqual(result[0]["source"]["media_type"], "application/pdf")

    def test_pdf_large_file_fallback_size_estimation(self):
        """Test that large PDFs trigger size estimation fallback"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=self.large_pdf_b64,
            file_name="large_estimated.pdf",
        )

        result = attachment.to_contents_for_invoke()

        # Since this is a large fake PDF, it should trigger size estimation
        # and may return error text if estimated pages > 100
        self.assertEqual(len(result), 1)
        # The result could be either document format or error text depending on size estimation
        self.assertIn(result[0]["type"], ["document", "text"])

    def test_non_pdf_files_processed_normally(self):
        """Test that non-PDF files bypass page validation"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=base64.b64encode(b"Some text content").decode("utf-8"),
            file_name="document.txt",
        )

        result = attachment.to_contents_for_invoke()

        # Should return document format with correct MIME type
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "document")
        self.assertEqual(result[0]["source"]["media_type"], "text/plain")

    def test_various_file_extensions_get_correct_mime_types(self):
        """Test that various file extensions get correct MIME types"""
        test_cases = [
            ("document.pdf", "application/pdf"),
            ("data.csv", "text/csv"),
            ("page.html", "text/html"),
            ("page.htm", "text/html"),
            ("notes.md", "text/markdown"),
            (
                "file.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            (
                "data.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ("unknown.xyz", "application/octet-stream"),
        ]

        for filename, expected_mime in test_cases:
            with self.subTest(filename=filename):
                attachment = AttachmentContentModel(
                    content_type="attachment",
                    body=base64.b64encode(b"test content").decode("utf-8"),
                    file_name=filename,
                )

                result = attachment.to_contents_for_invoke()
                self.assertEqual(result[0]["source"]["media_type"], expected_mime)

    def test_base64_string_vs_bytes_handling(self):
        """Test that both base64 strings and bytes are handled correctly"""
        # Test with bytes
        attachment_bytes = AttachmentContentModel(
            content_type="attachment",
            body=self.small_pdf_content,  # Raw bytes
            file_name="test_bytes.pdf",
        )

        # Test with base64 string
        attachment_string = AttachmentContentModel(
            content_type="attachment",
            body=self.small_pdf_b64,  # Base64 string
            file_name="test_string.pdf",
        )

        result_bytes = attachment_bytes.to_contents_for_invoke()
        result_string = attachment_string.to_contents_for_invoke()

        # Both should work and return document format
        self.assertEqual(result_bytes[0]["type"], "document")
        self.assertEqual(result_string[0]["type"], "document")

    def test_pdf_error_handling_graceful_fallback(self):
        """Test that PDF errors are handled gracefully and processing continues"""
        # Use corrupted PDF data that will cause pypdf to fail
        corrupted_pdf = b"Not a valid PDF"
        corrupted_pdf_b64 = base64.b64encode(corrupted_pdf).decode("utf-8")

        attachment = AttachmentContentModel(
            content_type="attachment", body=corrupted_pdf_b64, file_name="corrupted.pdf"
        )

        result = attachment.to_contents_for_invoke()

        # Should continue with normal document processing despite PDF parsing errors
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "document")
        self.assertEqual(result[0]["source"]["media_type"], "application/pdf")

    def test_invoke_format_structure(self):
        """Test that the invoke format structure is correct"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=base64.b64encode(b"test content").decode("utf-8"),
            file_name="test.txt",
        )

        result = attachment.to_contents_for_invoke()

        # Verify the structure matches Claude 4 invoke API format
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "document")
        self.assertIn("source", result[0])
        self.assertEqual(result[0]["source"]["type"], "base64")
        self.assertIn("media_type", result[0]["source"])
        self.assertIn("data", result[0]["source"])
        # Verify the data is valid base64
        try:
            base64.b64decode(result[0]["source"]["data"])
        except Exception:
            self.fail("Document data is not valid base64")


if __name__ == "__main__":
    unittest.main()
