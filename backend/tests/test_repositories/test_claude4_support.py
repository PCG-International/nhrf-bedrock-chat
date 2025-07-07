import base64
import sys
import unittest

sys.path.insert(0, ".")

from app.repositories.models.conversation import (
    AttachmentContentModel,
    ImageContentModel,
)
from app.bedrock import is_claude_4_model


class TestClaude4DocumentSupport(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.test_pdf_content = b"%PDF-1.4\n1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n>>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000074 00000 n \n0000000120 00000 n \ntrailer\n<<\n/Size 4\n/Root 1 0 R\n>>\nstartxref\n179\n%%EOF"
        self.test_pdf_b64 = base64.b64encode(self.test_pdf_content).decode("utf-8")

        self.test_image_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc\xf8\x0f\x00\x00\x01\x00\x01\x00\x00\x00\x00IEND\xaeB`\x82"
        self.test_image_b64 = base64.b64encode(self.test_image_content).decode("utf-8")

    def test_claude_4_model_detection(self):
        """Test that Claude 4 models are correctly identified"""
        claude_4_models = ["claude-v4-opus", "claude-v4-sonnet"]
        non_claude_4_models = [
            "claude-v3.5-sonnet",
            "claude-v3-haiku",
            "claude-v3-opus",
        ]

        for model in claude_4_models:
            with self.subTest(model=model):
                self.assertTrue(is_claude_4_model(model))

        for model in non_claude_4_models:
            with self.subTest(model=model):
                self.assertFalse(is_claude_4_model(model))

    def test_attachment_invoke_format_structure(self):
        """Test that attachments use proper invoke format for Claude 4"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=self.test_pdf_b64,
            file_name="test_document.pdf",
        )

        result = attachment.to_contents_for_invoke()

        # Verify invoke format structure
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "document")
        self.assertIn("source", result[0])
        self.assertEqual(result[0]["source"]["type"], "base64")
        self.assertEqual(result[0]["source"]["media_type"], "application/pdf")
        self.assertIn("data", result[0]["source"])

    def test_image_invoke_format_structure(self):
        """Test that images use proper invoke format for Claude 4"""
        image = ImageContentModel(
            content_type="image", media_type="image/png", body=self.test_image_b64
        )

        result = image.to_contents_for_invoke()

        # Verify invoke format structure
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "image")
        self.assertIn("source", result[0])
        self.assertEqual(result[0]["source"]["type"], "base64")
        self.assertEqual(result[0]["source"]["media_type"], "image/png")
        self.assertIn("data", result[0]["source"])

    def test_attachment_different_file_types(self):
        """Test that different file types get correct MIME types in invoke format"""
        test_cases = [
            ("document.pdf", "application/pdf"),
            (
                "spreadsheet.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            (
                "presentation.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            ("text.txt", "text/plain"),
            ("data.csv", "text/csv"),
            ("webpage.html", "text/html"),
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

    def test_base64_encoding_consistency(self):
        """Test that base64 encoding is consistent between different input formats"""
        # Test with bytes input
        attachment_bytes = AttachmentContentModel(
            content_type="attachment",
            body=self.test_pdf_content,  # Raw bytes
            file_name="test_bytes.pdf",
        )

        # Test with base64 string input
        attachment_string = AttachmentContentModel(
            content_type="attachment",
            body=self.test_pdf_b64,  # Base64 string
            file_name="test_string.pdf",
        )

        result_bytes = attachment_bytes.to_contents_for_invoke()
        result_string = attachment_string.to_contents_for_invoke()

        # Both should produce the same base64 output
        self.assertEqual(
            result_bytes[0]["source"]["data"], result_string[0]["source"]["data"]
        )

    def test_image_base64_encoding_consistency(self):
        """Test that image base64 encoding is consistent"""
        # Test with bytes input
        image_bytes = ImageContentModel(
            content_type="image",
            media_type="image/png",
            body=self.test_image_content,  # Raw bytes
        )

        # Test with base64 string input
        image_string = ImageContentModel(
            content_type="image",
            media_type="image/png",
            body=self.test_image_b64,  # Base64 string
        )

        result_bytes = image_bytes.to_contents_for_invoke()
        result_string = image_string.to_contents_for_invoke()

        # Both should produce the same base64 output
        self.assertEqual(
            result_bytes[0]["source"]["data"], result_string[0]["source"]["data"]
        )

    def test_large_document_handling(self):
        """Test that large documents are handled properly"""
        # Create a large document (>1MB)
        large_content = b"Large document content " * 50000  # ~1.15MB
        large_content_b64 = base64.b64encode(large_content).decode("utf-8")

        attachment = AttachmentContentModel(
            content_type="attachment",
            body=large_content_b64,
            file_name="large_document.txt",
        )

        result = attachment.to_contents_for_invoke()

        # Should still process large documents
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "document")
        self.assertEqual(result[0]["source"]["media_type"], "text/plain")
        # Verify the data can be decoded back to original content
        decoded_data = base64.b64decode(result[0]["source"]["data"])
        self.assertEqual(decoded_data, large_content)

    def test_empty_document_handling(self):
        """Test that empty documents are handled gracefully"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=base64.b64encode(b"").decode("utf-8"),
            file_name="empty.txt",
        )

        result = attachment.to_contents_for_invoke()

        # Should still process empty documents
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "document")
        self.assertEqual(result[0]["source"]["media_type"], "text/plain")

    def test_special_characters_in_content(self):
        """Test that documents with special characters are handled properly"""
        special_content = "Hello 世界! 🌍 Special chars: àáâãäåæçèéêë"
        special_content_bytes = special_content.encode("utf-8")
        special_content_b64 = base64.b64encode(special_content_bytes).decode("utf-8")

        attachment = AttachmentContentModel(
            content_type="attachment",
            body=special_content_b64,
            file_name="special_chars.txt",
        )

        result = attachment.to_contents_for_invoke()

        # Should handle special characters correctly
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "document")
        # Verify the data can be decoded back to original content
        decoded_data = base64.b64decode(result[0]["source"]["data"])
        self.assertEqual(decoded_data.decode("utf-8"), special_content)


if __name__ == "__main__":
    unittest.main()
