"""
Document processor module for handling large PDFs with Docling.
Provides intelligent chunking and text extraction for documents that exceed model limits.
"""

import base64
import hashlib
import logging
import os
from io import BytesIO
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import DoclingDocument
    from docling.chunking import HybridChunker
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

    DOCLING_AVAILABLE = True
    logger.info("Docling is available for document processing")

    # Check if RapidOCR is available (installed separately in ECS Docker)
    try:
        from rapidocr_onnxruntime import RapidOCR

        RAPIDOCR_AVAILABLE = True
        logger.info("RapidOCR is available for enhanced OCR processing")
    except ImportError:
        RAPIDOCR_AVAILABLE = False
        logger.info("RapidOCR not available, using Docling's built-in OCR")

except ImportError:
    logger.warning(
        "Docling not available. Large PDF processing will fall back to simple chunking."
    )
    DOCLING_AVAILABLE = False
    RAPIDOCR_AVAILABLE = False
    # Define placeholder classes
    DocumentConverter = None
    PdfFormatOption = None
    InputFormat = None
    DoclingDocument = None
    HybridChunker = None
    PyPdfiumDocumentBackend = None
    RapidOCR = None


@dataclass
class ProcessedChunk:
    """Represents a processed document chunk with metadata."""

    text: str
    chunk_index: int
    total_chunks: int
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    section_title: Optional[str] = None
    token_estimate: int = 0


class DoclingProcessor:
    """
    Processes large documents using Docling for intelligent text extraction and chunking.
    """

    def __init__(
        self,
        chunk_size_tokens: int = 6000,
        chunk_overlap_tokens: int = 200,
        enable_ocr: bool = True,
        max_pages_direct: int = 50,
    ):
        """
        Initialize the Docling processor.

        Args:
            chunk_size_tokens: Target size for each chunk in tokens (roughly 4 chars per token)
            chunk_overlap_tokens: Number of tokens to overlap between chunks
            enable_ocr: Enable OCR for scanned PDFs
            max_pages_direct: Maximum pages to process without chunking
        """
        self.chunk_size_tokens = chunk_size_tokens
        self.chunk_overlap_tokens = chunk_overlap_tokens
        self.enable_ocr = enable_ocr
        self.max_pages_direct = max_pages_direct

        # Initialize Docling converter if available
        if DOCLING_AVAILABLE:
            # Configure PDF processing with RapidOCR
            # Only enable OCR if both requested and RapidOCR is available
            actual_ocr = enable_ocr and RAPIDOCR_AVAILABLE

            if enable_ocr and not RAPIDOCR_AVAILABLE:
                logger.warning(
                    "OCR requested but RapidOCR not available, proceeding without OCR"
                )

            pdf_options = PdfFormatOption(
                do_ocr=actual_ocr,  # Uses RapidOCR when enabled
                do_table_structure=True,
                # Use pypdfium2 backend for better performance with ONNX
                backend=PyPdfiumDocumentBackend if PyPdfiumDocumentBackend else None,
            )

            # Initialize converter with OCR backend if available
            self.converter = DocumentConverter(
                format_options={InputFormat.PDF: pdf_options}
            )

            # Initialize RapidOCR separately for potential custom use
            if RAPIDOCR_AVAILABLE and enable_ocr:
                self.ocr_engine = RapidOCR()
                logger.info("RapidOCR engine initialized successfully")
            else:
                self.ocr_engine = None
        else:
            self.converter = None
            self.ocr_engine = None
            logger.warning("Docling not available, using fallback text extraction")

    def should_process_with_docling(
        self, file_bytes: bytes, file_type: str, page_count: Optional[int] = None
    ) -> bool:
        """
        Determine if a document should be processed with Docling.

        Args:
            file_bytes: Raw file bytes
            file_type: File extension (pdf, docx, etc.)
            page_count: Number of pages if known

        Returns:
            True if document should be processed with Docling
        """
        # Process if it's a PDF with more than threshold pages
        if file_type.lower() == "pdf" and page_count:
            if page_count > self.max_pages_direct:
                logger.info(
                    f"PDF has {page_count} pages, exceeding {self.max_pages_direct}, will process with Docling"
                )
                return True

        # Process if file is larger than 2MB (likely to have significant content)
        file_size_mb = len(file_bytes) / (1024 * 1024)
        if file_size_mb > 2:
            logger.info(
                f"File size {file_size_mb:.2f}MB exceeds 2MB threshold, will process with Docling"
            )
            return True

        return False

    def extract_and_chunk(
        self, file_bytes: bytes, filename: str, file_type: str
    ) -> List[ProcessedChunk]:
        """
        Extract text from document and split into chunks.

        Args:
            file_bytes: Raw file bytes
            filename: Original filename
            file_type: File extension

        Returns:
            List of processed chunks with metadata
        """
        # If Docling is not available, use fallback extraction
        if not DOCLING_AVAILABLE or not self.converter:
            logger.warning(f"Using fallback text extraction for {filename}")
            return self._fallback_extract_and_chunk(file_bytes, filename, file_type)

        try:
            # Convert document using Docling
            logger.info(f"Processing {filename} with Docling")

            # Create BytesIO object from bytes
            file_stream = BytesIO(file_bytes)

            # Convert document
            result = self.converter.convert(file_stream, input_format=InputFormat.PDF)

            # Get the document
            doc: DoclingDocument = result.document

            # Extract full text with structure
            full_text = doc.export_to_markdown()

            # If document is small enough, return as single chunk
            estimated_tokens = self._estimate_tokens(full_text)
            if estimated_tokens <= self.chunk_size_tokens:
                logger.info(
                    f"Document fits in single chunk ({estimated_tokens} tokens)"
                )
                return [
                    ProcessedChunk(
                        text=full_text,
                        chunk_index=0,
                        total_chunks=1,
                        token_estimate=estimated_tokens,
                    )
                ]

            # Otherwise, chunk the document
            logger.info(f"Document needs chunking ({estimated_tokens} tokens)")
            chunks = self._chunk_document(doc, full_text)

            return chunks

        except Exception as e:
            logger.error(f"Error processing document with Docling: {e}")
            # Fall back to simple extraction
            return self._fallback_extract_and_chunk(file_bytes, filename, file_type)

    def _chunk_document(
        self, doc: DoclingDocument, full_text: str
    ) -> List[ProcessedChunk]:
        """
        Split document into semantic chunks.

        Args:
            doc: Docling document object
            full_text: Full extracted text

        Returns:
            List of chunks with metadata
        """
        # Use Docling's hybrid chunker for intelligent splitting
        chunker = HybridChunker(
            max_tokens=self.chunk_size_tokens,
            min_tokens=100,  # Minimum chunk size to avoid tiny fragments
            merge=True,  # Merge small consecutive chunks
            delimiter="paragraph",  # Split on paragraph boundaries
        )

        chunks = []
        chunk_texts: List[str] = []

        # Perform chunking
        try:
            # Export to text for chunking
            doc_chunks = chunker.chunk(doc)

            for idx, chunk in enumerate(doc_chunks):
                # Get chunk text
                chunk_text = chunk.export_to_markdown()

                # Extract page numbers if available
                page_start = None
                page_end = None
                if hasattr(chunk, "page_numbers") and chunk.page_numbers:
                    page_start = min(chunk.page_numbers)
                    page_end = max(chunk.page_numbers)

                # Create processed chunk
                processed_chunk = ProcessedChunk(
                    text=chunk_text,
                    chunk_index=idx,
                    total_chunks=len(doc_chunks),
                    page_start=page_start,
                    page_end=page_end,
                    token_estimate=self._estimate_tokens(chunk_text),
                )

                chunks.append(processed_chunk)

        except Exception as e:
            logger.warning(
                f"Advanced chunking failed, falling back to simple chunking: {e}"
            )
            # Fall back to simple text splitting if hybrid chunking fails
            chunks = self._simple_chunk_text(full_text)

        logger.info(f"Created {len(chunks)} chunks from document")
        return chunks

    def _fallback_extract_and_chunk(
        self, file_bytes: bytes, filename: str, file_type: str
    ) -> List[ProcessedChunk]:
        """
        Fallback text extraction when Docling is not available.

        Args:
            file_bytes: Raw file bytes
            filename: Original filename
            file_type: File extension

        Returns:
            List of processed chunks
        """
        text = ""

        # Try to extract text based on file type
        if file_type.lower() == "pdf":
            try:
                import pypdf
                from io import BytesIO

                pdf_stream = BytesIO(file_bytes)
                pdf_reader = pypdf.PdfReader(pdf_stream)

                # Extract text from all pages
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n\n"

                logger.info(
                    f"Extracted text from PDF using pypdf: {len(text)} characters"
                )

            except Exception as e:
                logger.error(f"Failed to extract text from PDF: {e}")
                # Return placeholder text
                text = f"[PDF Document: {filename}]\n\nNote: Unable to extract text from this PDF. The document requires Docling for proper processing."
        else:
            # For non-PDF files, try to decode as text
            try:
                text = file_bytes.decode("utf-8", errors="ignore")
            except:
                text = f"[Document: {filename}]\n\nNote: Unable to extract text from this file."

        # If we have text, chunk it
        if text and len(text.strip()) > 0:
            return self._simple_chunk_text(text)
        else:
            # Return a single chunk with error message
            return [
                ProcessedChunk(
                    text=f"[Document: {filename}]\n\nNote: Unable to extract text content.",
                    chunk_index=0,
                    total_chunks=1,
                    token_estimate=10,
                )
            ]

    def _simple_chunk_text(self, text: str) -> List[ProcessedChunk]:
        """
        Simple text chunking as fallback.

        Args:
            text: Full text to chunk

        Returns:
            List of chunks
        """
        # Estimate 4 characters per token (rough approximation)
        chars_per_token = 4
        chunk_size_chars = self.chunk_size_tokens * chars_per_token
        overlap_chars = self.chunk_overlap_tokens * chars_per_token

        chunks = []
        start = 0
        chunk_index = 0

        while start < len(text):
            # Calculate end position
            end = start + chunk_size_chars

            # Try to break at paragraph boundary
            if end < len(text):
                # Look for paragraph break
                paragraph_break = text.rfind("\n\n", start, end)
                if paragraph_break > start:
                    end = paragraph_break
                else:
                    # Look for sentence break
                    sentence_break = text.rfind(". ", start, end)
                    if sentence_break > start:
                        end = sentence_break + 1

            # Extract chunk
            chunk_text = text[start:end].strip()

            if chunk_text:
                chunks.append(
                    ProcessedChunk(
                        text=chunk_text,
                        chunk_index=chunk_index,
                        total_chunks=0,  # Will update after all chunks created
                        token_estimate=self._estimate_tokens(chunk_text),
                    )
                )
                chunk_index += 1

            # Move start position with overlap
            start = end - overlap_chars if end < len(text) else end

        # Update total chunks count
        for chunk in chunks:
            chunk.total_chunks = len(chunks)

        return chunks

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text.

        Args:
            text: Text to estimate tokens for

        Returns:
            Estimated token count
        """
        # Rough estimation: ~4 characters per token for English text
        # This is approximate but sufficient for chunking decisions
        return len(text) // 4

    def format_chunks_for_bedrock(
        self, chunks: List[ProcessedChunk], include_metadata: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Format chunks for sending to Bedrock.

        Args:
            chunks: Processed chunks
            include_metadata: Whether to include chunk metadata in text

        Returns:
            List of formatted text blocks
        """
        formatted_chunks = []

        for chunk in chunks:
            # Add metadata header if requested
            if include_metadata and (chunk.page_start or chunk.chunk_index > 0):
                metadata_parts = []

                if chunk.page_start and chunk.page_end:
                    if chunk.page_start == chunk.page_end:
                        metadata_parts.append(f"Page {chunk.page_start}")
                    else:
                        metadata_parts.append(
                            f"Pages {chunk.page_start}-{chunk.page_end}"
                        )

                if chunk.total_chunks > 1:
                    metadata_parts.append(
                        f"Section {chunk.chunk_index + 1}/{chunk.total_chunks}"
                    )

                if chunk.section_title:
                    metadata_parts.append(chunk.section_title)

                if metadata_parts:
                    header = f"[{', '.join(metadata_parts)}]\n\n"
                    formatted_text = header + chunk.text
                else:
                    formatted_text = chunk.text
            else:
                formatted_text = chunk.text

            formatted_chunks.append(
                {
                    "text": formatted_text,
                    "metadata": {
                        "chunk_index": chunk.chunk_index,
                        "total_chunks": chunk.total_chunks,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "token_estimate": chunk.token_estimate,
                    },
                }
            )

        return formatted_chunks

    def get_cache_key(self, file_bytes: bytes, processing_params: Dict) -> str:
        """
        Generate cache key for processed document.

        Args:
            file_bytes: Raw file bytes
            processing_params: Processing parameters

        Returns:
            Cache key string
        """
        # Create hash of file content and parameters
        hasher = hashlib.sha256()
        hasher.update(file_bytes)
        hasher.update(str(processing_params).encode())
        return hasher.hexdigest()


# Singleton instance
_processor_instance: Optional[DoclingProcessor] = None


def get_docling_processor() -> DoclingProcessor:
    """
    Get or create the Docling processor singleton instance.

    Returns:
        DoclingProcessor instance
    """
    global _processor_instance

    if _processor_instance is None:
        # Get configuration from environment or use defaults
        chunk_size = int(os.environ.get("DOCLING_CHUNK_SIZE_TOKENS", "6000"))
        chunk_overlap = int(os.environ.get("DOCLING_CHUNK_OVERLAP_TOKENS", "200"))
        enable_ocr = os.environ.get("DOCLING_ENABLE_OCR", "true").lower() == "true"
        max_pages = int(os.environ.get("DOCLING_MAX_PAGES_DIRECT", "50"))

        _processor_instance = DoclingProcessor(
            chunk_size_tokens=chunk_size,
            chunk_overlap_tokens=chunk_overlap,
            enable_ocr=enable_ocr,
            max_pages_direct=max_pages,
        )

        logger.info(
            f"Initialized Docling processor with chunk_size={chunk_size}, "
            f"overlap={chunk_overlap}, ocr={enable_ocr}, max_pages={max_pages}"
        )

    return _processor_instance
