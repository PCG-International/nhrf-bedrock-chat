import base64
import io
import sys
import unittest

# Mock imports removed - not needed for current tests
import zipfile

sys.path.insert(0, ".")

from app.repositories.models.conversation import AttachmentContentModel


class TestEPUBSupport(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        # Create a minimal valid EPUB file for testing
        self.minimal_epub_content = self._create_minimal_epub()
        self.minimal_epub_b64 = base64.b64encode(self.minimal_epub_content).decode(
            "utf-8"
        )

        # Create corrupted EPUB data
        self.corrupted_epub_content = b"Not a valid EPUB file"
        self.corrupted_epub_b64 = base64.b64encode(self.corrupted_epub_content).decode(
            "utf-8"
        )

    def _create_minimal_epub(self) -> bytes:
        """Create a minimal valid EPUB file for testing"""
        epub_buffer = io.BytesIO()

        with zipfile.ZipFile(epub_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # Add mimetype file (must be uncompressed and first)
            zip_file.writestr(
                "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
            )

            # Add META-INF/container.xml
            container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""
            zip_file.writestr("META-INF/container.xml", container_xml)

            # Add content.opf
            content_opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>Test Book</dc:title>
        <dc:creator>Test Author</dc:creator>
        <dc:identifier id="bookid">test-book-1</dc:identifier>
        <dc:language>en</dc:language>
    </metadata>
    <manifest>
        <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    </manifest>
    <spine>
        <itemref idref="chapter1"/>
    </spine>
</package>"""
            zip_file.writestr("OEBPS/content.opf", content_opf)

            # Add chapter1.xhtml
            chapter1_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <title>Chapter 1</title>
</head>
<body>
    <h1>Chapter 1: The Beginning</h1>
    <p>This is the first paragraph of the test book.</p>
    <p>This is the second paragraph with some content.</p>
    <div>This is a div with text content.</div>
</body>
</html>"""
            zip_file.writestr("OEBPS/chapter1.xhtml", chapter1_xhtml)

        epub_buffer.seek(0)
        return epub_buffer.read()

    def test_epub_file_type_recognized(self):
        """Test that .epub files are recognized as supported document format"""
        from app.repositories.models.conversation import (
            _is_converse_supported_document_format,
        )

        self.assertTrue(_is_converse_supported_document_format("epub"))

    def test_epub_mime_type_mapping(self):
        """Test that .epub files get correct handling for invoke API"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=self.minimal_epub_b64,
            file_name="test_book.epub",
        )

        result = attachment.to_contents_for_invoke()

        # Should extract text and return as text format for invoke API
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")
        self.assertIn("[EPUB Document: test_book.epub]", result[0]["text"])

    def test_epub_text_extraction_success(self):
        """Test successful EPUB text extraction for invoke API"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=self.minimal_epub_b64,
            file_name="test_book.epub",
        )

        result = attachment.to_contents_for_invoke()

        # Should return text format for invoke API
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")

        # Verify text content contains extracted text
        text_content = result[0]["text"]
        self.assertIn("[EPUB Document: test_book.epub]", text_content)
        self.assertIn("Chapter 1: The Beginning", text_content)
        self.assertIn("first paragraph", text_content)
        self.assertIn("second paragraph", text_content)

    def test_epub_corrupted_file_handling(self):
        """Test graceful handling of corrupted EPUB files"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=self.corrupted_epub_b64,
            file_name="corrupted.epub",
        )

        result = attachment.to_contents_for_invoke()

        # Should return error message as text
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")
        self.assertIn("Could not extract text", result[0]["text"])
        self.assertIn("corrupted.epub", result[0]["text"])

    def test_epub_empty_content_handling(self):
        """Test handling of EPUB files that don't extract any text"""
        # Create EPUB with no readable content
        epub_buffer = io.BytesIO()
        with zipfile.ZipFile(epub_buffer, "w") as zip_file:
            zip_file.writestr("mimetype", "application/epub+zip")
            # Add minimal structure but no readable content
            container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""
            zip_file.writestr("META-INF/container.xml", container_xml)

        empty_epub_content = epub_buffer.getvalue()
        empty_epub_b64 = base64.b64encode(empty_epub_content).decode("utf-8")

        attachment = AttachmentContentModel(
            content_type="attachment",
            body=empty_epub_b64,
            file_name="empty.epub",
        )

        result = attachment.to_contents_for_invoke()

        # Should return error message about no extractable text
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")
        self.assertIn("Could not extract text", result[0]["text"])
        self.assertIn("empty.epub", result[0]["text"])

    def test_epub_missing_dependency_handling(self):
        """Test handling when ebooklib produces an error"""
        # Use an invalid EPUB that will cause ebooklib to fail
        invalid_epub = b"Invalid EPUB content that will fail"
        invalid_epub_b64 = base64.b64encode(invalid_epub).decode("utf-8")

        attachment = AttachmentContentModel(
            content_type="attachment",
            body=invalid_epub_b64,
            file_name="invalid.epub",
        )

        result = attachment.to_contents_for_invoke()

        # Should return error message when processing fails
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")
        self.assertIn("Could not extract text", result[0]["text"])

    def test_epub_converse_api_format(self):
        """Test EPUB processing for Converse API format"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=self.minimal_epub_b64,
            file_name="test_book.epub",
        )

        result = attachment.to_contents_for_converse()

        # Should return document format for Converse API since epub is supported
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["document"]["format"], "epub")
        self.assertEqual(result[0]["document"]["name"], "testbook")

    def test_epub_various_text_elements_extraction(self):
        """Test that various HTML elements are properly extracted from EPUB"""
        # Create EPUB with various text elements
        epub_buffer = io.BytesIO()

        with zipfile.ZipFile(epub_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr(
                "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
            )

            container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""
            zip_file.writestr("META-INF/container.xml", container_xml)

            content_opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>Test Book</dc:title>
    </metadata>
    <manifest>
        <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    </manifest>
    <spine>
        <itemref idref="chapter1"/>
    </spine>
</package>"""
            zip_file.writestr("content.opf", content_opf)

            # Chapter with various text elements
            chapter1_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <title>Test Chapter</title>
</head>
<body>
    <h1>Main Heading</h1>
    <h2>Sub Heading</h2>
    <p>Paragraph text</p>
    <div>Div text content</div>
    <span>Span text content</span>
    <h3>Another heading</h3>
    <p>More paragraph content</p>
</body>
</html>"""
            zip_file.writestr("chapter1.xhtml", chapter1_xhtml)

        complex_epub_content = epub_buffer.getvalue()
        complex_epub_b64 = base64.b64encode(complex_epub_content).decode("utf-8")

        attachment = AttachmentContentModel(
            content_type="attachment",
            body=complex_epub_b64,
            file_name="complex_book.epub",
        )

        result = attachment.to_contents_for_invoke()

        # Verify various text elements are extracted
        text_content = result[0]["text"]
        self.assertIn("Main Heading", text_content)
        self.assertIn("Sub Heading", text_content)
        self.assertIn("Paragraph text", text_content)
        self.assertIn("Div text content", text_content)
        self.assertIn("Span text content", text_content)
        self.assertIn("Another heading", text_content)

    def test_epub_base64_vs_bytes_handling(self):
        """Test that both base64 strings and bytes are handled correctly for EPUB"""
        # Test with bytes
        attachment_bytes = AttachmentContentModel(
            content_type="attachment",
            body=self.minimal_epub_content,  # Raw bytes
            file_name="test_bytes.epub",
        )

        # Test with base64 string
        attachment_string = AttachmentContentModel(
            content_type="attachment",
            body=self.minimal_epub_b64,  # Base64 string
            file_name="test_string.epub",
        )

        result_bytes = attachment_bytes.to_contents_for_invoke()
        result_string = attachment_string.to_contents_for_invoke()

        # Both should work and return text format for invoke API
        self.assertEqual(result_bytes[0]["type"], "text")
        self.assertIn("[EPUB Document:", result_bytes[0]["text"])
        self.assertEqual(result_string[0]["type"], "text")
        self.assertIn("[EPUB Document:", result_string[0]["text"])

    def test_epub_invoke_format_structure(self):
        """Test that the EPUB invoke format structure is correct"""
        attachment = AttachmentContentModel(
            content_type="attachment",
            body=self.minimal_epub_b64,
            file_name="test.epub",
        )

        result = attachment.to_contents_for_invoke()

        # Verify the structure matches Claude 4 invoke API format (text format for EPUB)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "text")
        self.assertIn("text", result[0])
        
        # Verify the text contains the document header and extracted content
        text_content = result[0]["text"]
        self.assertIn("[EPUB Document: test.epub]", text_content)
        self.assertIsInstance(text_content, str)


if __name__ == "__main__":
    unittest.main()
