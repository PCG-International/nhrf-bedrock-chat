from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self, TypeGuard
from urllib.parse import urlparse

from app.repositories.common import decompose_conv_id
from app.repositories.models.common import Base64EncodedBytes
from app.routes.schemas.conversation import (
    AttachmentContent,
    Content,
    DocumentToolResult,
    ImageContent,
    ImageToolResult,
    JsonToolResult,
    MessageInput,
    ReasoningContent,
    RelatedDocument,
    SimpleMessage,
    TextContent,
    TextToolResult,
    ToolResult,
    ToolResultContent,
    ToolResultContentBody,
    ToolUseContent,
    ToolUseContentBody,
    type_model_name,
)
from app.utils import generate_presigned_url
from mypy_boto3_bedrock_runtime.literals import DocumentFormatType, ImageFormatType
from mypy_boto3_bedrock_runtime.type_defs import (
    ContentBlockTypeDef,
    ToolResultBlockTypeDef,
    ToolResultContentBlockOutputTypeDef,
    ToolUseBlockOutputTypeDef,
    ToolUseBlockTypeDef,
)
from pydantic import BaseModel, Discriminator, Field, JsonValue, field_validator

if TYPE_CHECKING:
    from app.agents.tools.agent_tool import ToolRunResult

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class TextContentModel(BaseModel):
    content_type: Literal["text"]
    body: str = Field(
        ...,
        description="Text string.",
    )

    @classmethod
    def from_text_content(cls, content: TextContent) -> Self:
        return cls(
            content_type="text",
            body=content.body,
        )

    def to_content(self) -> Content:
        return TextContent(
            content_type="text",
            body=self.body,
        )

    def to_contents_for_converse(self) -> list[ContentBlockTypeDef]:
        return [
            {
                "text": self.body,
            }
        ]

    def to_contents_for_invoke(self) -> list[dict[str, Any]]:
        """Convert to Claude 4 invoke API format"""
        return [
            {
                "type": "text",
                "text": self.body,
            }
        ]


def _is_converse_supported_image_format(format: str) -> TypeGuard[ImageFormatType]:
    return format in {"gif", "jpeg", "png", "webp"}


class ImageContentModel(BaseModel):
    content_type: Literal["image"]
    media_type: str
    body: Base64EncodedBytes = Field(
        ...,
        description="Image bytes.",
    )

    @classmethod
    def from_image_content(cls, content: ImageContent) -> Self:
        return cls(
            content_type="image",
            media_type=content.media_type,
            body=content.body,
        )

    def to_content(self) -> Content:
        return ImageContent(
            content_type="image",
            media_type=self.media_type,
            body=self.body,
        )

    def to_contents_for_converse(self) -> list[ContentBlockTypeDef]:
        # e.g. "image/png" -> "png"
        format = self.media_type.split("/")[1] if self.media_type else "unknown"

        return (
            [
                {
                    "image": {
                        "format": format,
                        "source": {"bytes": self.body},
                    },
                },
            ]
            if _is_converse_supported_image_format(format)
            else []
        )

    def to_contents_for_invoke(self) -> list[dict[str, Any]]:
        """Convert to Claude 4 invoke API format"""
        # Handle Base64-encoded image data properly
        import base64

        if isinstance(self.body, bytes):
            # If body is bytes, convert to base64 string
            data_str = base64.b64encode(self.body).decode("utf-8")
        else:
            # If body is already a string (base64-encoded), use it directly
            data_str = self.body

        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": self.media_type,
                    "data": data_str,
                },
            }
        ]


def _is_converse_supported_document_format(ext: str) -> TypeGuard[DocumentFormatType]:
    supported_formats = {
        "pdf",
        "csv",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "html",
        "txt",
        "md",
        "epub",
    }
    return ext in supported_formats


def _convert_to_valid_file_name(file_name: str) -> str:
    # Note: The document file name can only contain alphanumeric characters,
    # whitespace characters, hyphens, parentheses, and square brackets.
    # The name can't contain more than one consecutive whitespace character.

    # Handle None or empty filename
    if not file_name:
        return "document"

    file_name = re.sub(r"[^a-zA-Z0-9\s\-\(\)\[\]]", "", file_name)
    file_name = re.sub(r"\s+", " ", file_name)
    file_name = file_name.strip()

    # If stripping results in empty string, return default
    if not file_name:
        return "document"

    return file_name


class AttachmentContentModel(BaseModel):
    content_type: Literal["attachment"]
    body: Base64EncodedBytes = Field(
        ...,
        description="Attachment file bytes.",
    )
    file_name: str

    @classmethod
    def from_attachment_content(cls, content: AttachmentContent) -> Self:
        return cls(
            content_type="attachment",
            body=content.body,
            file_name=content.file_name,
        )

    def to_content(self) -> Content:
        return AttachmentContent(
            content_type="attachment",
            body=self.body,
            file_name=self.file_name,
        )

    def to_contents_for_converse(self) -> list[ContentBlockTypeDef]:
        # e.g. "document.txt" -> "txt"
        format = Path(self.file_name).suffix[1:]

        # e.g. "document.txt" -> "document"
        name = Path(self.file_name).stem

        return (
            [
                {
                    "document": {
                        "format": format,
                        "name": _convert_to_valid_file_name(name),
                        "source": {"bytes": self.body},
                    },
                },
            ]
            if _is_converse_supported_document_format(format)
            else []
        )

    def to_contents_for_invoke(self) -> list[dict[str, Any]]:
        """Convert to Claude 4 invoke API format for document attachments"""
        # Handle Base64-encoded document data properly
        import base64
        import io

        # Get file extension to determine media type
        file_ext = Path(self.file_name).suffix.lower()

        # Map file extensions to MIME types
        mime_type_map = {
            ".pdf": "application/pdf",
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".html": "text/html",
            ".htm": "text/html",
            ".csv": "text/csv",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".epub": "application/epub+zip",
        }

        media_type = mime_type_map.get(file_ext, "application/octet-stream")

        # Get raw bytes for processing
        if isinstance(self.body, bytes):
            file_bytes = self.body
            data_str = base64.b64encode(self.body).decode("utf-8")
        else:
            # If body is already a string (base64-encoded), decode to get bytes for validation
            file_bytes = base64.b64decode(self.body)
            data_str = self.body

        # Special handling for PDFs - use Docling for large documents
        if file_ext == ".pdf":
            try:
                # Try to count PDF pages using pypdf if available
                page_count = None
                try:
                    import pypdf

                    pdf_stream = io.BytesIO(file_bytes)
                    pdf_reader = pypdf.PdfReader(pdf_stream)
                    page_count = len(pdf_reader.pages)
                except ImportError:
                    # pypdf not available, fall back to size-based estimation
                    # Rough estimation: assume ~5KB per page on average
                    page_count = len(file_bytes) // (5 * 1024)

                # Check if we should use Docling for processing
                from app.config import DOCLING_CONFIG

                if DOCLING_CONFIG["enable_processing"]:
                    from app.document_processor import get_docling_processor

                    processor = get_docling_processor()

                    # Check if document should be processed with Docling
                    if processor.should_process_with_docling(
                        file_bytes, file_ext.lstrip("."), page_count
                    ):
                        try:
                            # Extract and chunk the document
                            chunks = processor.extract_and_chunk(
                                file_bytes, self.file_name, file_ext.lstrip(".")
                            )

                            # Format chunks for Bedrock
                            formatted_chunks = processor.format_chunks_for_bedrock(
                                chunks
                            )

                            # Return chunks as text blocks
                            result = []
                            for chunk_data in formatted_chunks:
                                result.append(
                                    {"type": "text", "text": chunk_data["text"]}
                                )

                            logger.info(
                                f"Processed PDF {self.file_name} into {len(chunks)} chunks using Docling"
                            )
                            return result

                        except Exception as e:
                            logger.error(
                                f"Docling processing failed for {self.file_name}: {e}"
                            )
                            # Fall back to validation message if processing fails
                            if page_count and page_count > 100:
                                return [
                                    {
                                        "type": "text",
                                        "text": f"[PDF Document: {self.file_name}]\n\nNote: This PDF has {page_count} pages. Processing failed, please try with a smaller PDF (≤100 pages) or contact support.",
                                    }
                                ]

                # Regular validation for non-Docling path or smaller PDFs
                if page_count and page_count > 100:
                    return [
                        {
                            "type": "text",
                            "text": f"[PDF Document: {self.file_name}]\n\nNote: This PDF has {page_count} pages, which exceeds the 100-page limit for PDF processing. Please try with a smaller PDF (≤100 pages).",
                        }
                    ]

            except Exception as e:
                logger.warning(
                    f"Failed to validate PDF page count for {self.file_name}: {e}"
                )
                # Continue with normal processing if validation fails

        # Special handling for EPUB files - extract text and return as text for invoke API
        # (Claude 4 invoke API only accepts application/pdf for documents)
        if file_ext == ".epub":
            try:
                extracted_text = self._extract_epub_text(file_bytes)
                if extracted_text:
                    # Return as text format since invoke API doesn't support EPUB documents
                    return [
                        {
                            "type": "text",
                            "text": f"[EPUB Document: {self.file_name}]\n\n{extracted_text}",
                        }
                    ]
                else:
                    return [
                        {
                            "type": "text",
                            "text": f"[EPUB Document: {self.file_name}]\n\nNote: Could not extract text from this EPUB file. The file may be corrupted or protected.",
                        }
                    ]
            except Exception as e:
                logger.warning(
                    f"Failed to extract text from EPUB {self.file_name}: {e}"
                )
                return [
                    {
                        "type": "text",
                        "text": f"[EPUB Document: {self.file_name}]\n\nNote: Error processing EPUB file. Please try with a different file format.",
                    }
                ]

        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data_str,
                },
            }
        ]

    def _extract_epub_text(self, file_bytes: bytes) -> str:
        """Extract text content from EPUB file bytes"""
        try:
            import ebooklib
            from ebooklib import epub
            from bs4 import BeautifulSoup
            import tempfile
            import os

            # Create temporary file since ebooklib.epub.read_epub needs a file path
            with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as temp_file:
                temp_file.write(file_bytes)
                temp_file_path = temp_file.name

            try:
                # Create EPUB book from temporary file
                book = epub.read_epub(temp_file_path)

                # Extract text from all document items
                text_content = []

                # Get all document items (HTML/XHTML content)
                for item in book.get_items():
                    if item.get_type() == ebooklib.ITEM_DOCUMENT:
                        # Get the content and parse with BeautifulSoup
                        content = item.get_content().decode("utf-8", errors="ignore")
                        soup = BeautifulSoup(content, "html.parser")

                        # Extract text from paragraphs, maintaining some structure
                        for paragraph in soup.find_all(
                            ["p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6"]
                        ):
                            text = paragraph.get_text(strip=True)
                            if text:
                                text_content.append(text)

                # Join all text with double newlines for readability
                return "\n\n".join(text_content)
            finally:
                # Clean up temporary file
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)

        except ImportError:
            logger.error(
                "ebooklib or beautifulsoup4 not installed - cannot process EPUB files"
            )
            return ""
        except Exception as e:
            logger.error(f"Error extracting text from EPUB: {e}")
            return ""


class FeedbackModel(BaseModel):
    thumbs_up: bool
    category: str
    comment: str


class ChunkModel(BaseModel):
    content: str
    content_type: str = Field(default="s3")
    source: str
    rank: int


class ToolUseContentModelBody(BaseModel):
    tool_use_id: str
    name: str
    input: dict[str, JsonValue]

    @classmethod
    def from_tool_use_content(cls, tool_use_content: ToolUseBlockOutputTypeDef) -> Self:
        return cls(
            tool_use_id=tool_use_content["toolUseId"],
            name=tool_use_content["name"],
            input=tool_use_content["input"],
        )

    @classmethod
    def from_tool_use_content_body(cls, body: ToolUseContentBody) -> Self:
        return cls(
            tool_use_id=body.tool_use_id,
            name=body.name,
            input=body.input,
        )

    def to_tool_use_content_body(self) -> ToolUseContentBody:
        return ToolUseContentBody(
            tool_use_id=self.tool_use_id,
            name=self.name,
            input=self.input,
        )

    def to_tool_use_for_converse(self) -> ToolUseBlockTypeDef:
        return {
            "toolUseId": self.tool_use_id,
            "name": self.name,
            "input": self.input,
        }


class ToolUseContentModel(BaseModel):
    content_type: Literal["toolUse"] = Field(
        ..., description="Content type. Note that image is only available for claude 3."
    )
    body: ToolUseContentModelBody

    @classmethod
    def from_tool_use_content(cls, content: ToolUseContent) -> Self:
        return cls(
            content_type="toolUse",
            body=ToolUseContentModelBody.from_tool_use_content_body(body=content.body),
        )

    def to_content(self) -> Content:
        return ToolUseContent(
            content_type="toolUse",
            body=self.body.to_tool_use_content_body(),
        )

    def to_contents_for_converse(self) -> list[ContentBlockTypeDef]:
        return [
            {
                "toolUse": self.body.to_tool_use_for_converse(),
            },
        ]

    def to_contents_for_invoke(self) -> list[dict[str, Any]]:
        """Convert to Claude 4 invoke API format"""
        return [
            {
                "type": "tool_use",
                "id": self.body.tool_use_id,
                "name": self.body.name,
                "input": self.body.input,
            }
        ]


class TextToolResultModel(BaseModel):
    text: str

    @classmethod
    def from_text_tool_result(cls, tool_result: TextToolResult) -> Self:
        return cls(
            text=tool_result.text,
        )

    def to_tool_result(self) -> ToolResult:
        return TextToolResult(
            text=self.text,
        )

    def to_content_for_converse(self) -> ToolResultContentBlockOutputTypeDef:
        return {
            "text": self.text,
        }


class JsonToolResultModel(BaseModel):
    json_: dict[str, JsonValue] = Field(
        alias="json"
    )  # `json` is a reserved keyword on pydantic

    @classmethod
    def from_json_tool_result(cls, tool_result: JsonToolResult) -> Self:
        return cls(
            json=tool_result.json_,
        )

    def to_tool_result(self) -> ToolResult:
        return JsonToolResult(
            json=self.json_,
        )

    def to_content_for_converse(self) -> ToolResultContentBlockOutputTypeDef:
        return {
            "json": self.json_,
        }


class ImageToolResultModel(BaseModel):
    format: ImageFormatType
    image: Base64EncodedBytes

    @classmethod
    def from_image_tool_result(cls, tool_result: ImageToolResult) -> Self:
        return cls(
            format=tool_result.format,
            image=tool_result.image,
        )

    def to_tool_result(self) -> ToolResult:
        return ImageToolResult(
            format=self.format,
            image=self.image,
        )

    def to_content_for_converse(self) -> ToolResultContentBlockOutputTypeDef:
        return {
            "image": {
                "format": self.format,
                "source": {
                    "bytes": self.image,
                },
            },
        }


class DocumentToolResultModel(BaseModel):
    format: DocumentFormatType
    name: str
    document: Base64EncodedBytes

    @classmethod
    def from_document_tool_result(cls, tool_result: DocumentToolResult) -> Self:
        return cls(
            format=tool_result.format,
            name=tool_result.name,
            document=tool_result.document,
        )

    def to_tool_result(self) -> ToolResult:
        return DocumentToolResult(
            format=self.format,
            name=self.name,
            document=self.document,
        )

    def to_content_for_converse(self) -> ToolResultContentBlockOutputTypeDef:
        return {
            "document": {
                "format": self.format,
                "name": self.name,
                "source": {
                    "bytes": self.document,
                },
            },
        }


ToolResultModel = (
    TextToolResultModel
    | JsonToolResultModel
    | ImageToolResultModel
    | DocumentToolResultModel
)


def tool_result_model_from_tool_result(tool_result: ToolResult) -> ToolResultModel:
    if isinstance(tool_result, TextToolResult):
        return TextToolResultModel.from_text_tool_result(tool_result=tool_result)

    elif isinstance(tool_result, JsonToolResult):
        return JsonToolResultModel.from_json_tool_result(tool_result=tool_result)

    elif isinstance(tool_result, ImageToolResult):
        return ImageToolResultModel.from_image_tool_result(tool_result=tool_result)

    elif isinstance(tool_result, DocumentToolResult):
        return DocumentToolResultModel.from_document_tool_result(
            tool_result=tool_result
        )

    else:
        raise ValueError(f"Unknown tool result type")


def tool_result_model_from_tool_result_content(
    content: ToolResultContentBlockOutputTypeDef,
) -> ToolResultModel:
    if "text" in content:
        return TextToolResultModel(text=content["text"])

    elif "json" in content:
        return JsonToolResultModel(json=content["json"])

    elif "image" in content:
        return ImageToolResultModel(
            format=content["image"]["format"],
            image=(
                content["image"]["source"]["bytes"]
                if "bytes" in content["image"]["source"]
                else b""
            ),
        )

    elif "document" in content:
        return DocumentToolResultModel(
            format=content["document"]["format"],
            name=content["document"]["name"],
            document=(
                content["document"]["source"]["bytes"]
                if "bytes" in content["document"]["source"]
                else b""
            ),
        )

    else:
        raise ValueError(f"Unknown tool result type")


class ToolResultContentModelBody(BaseModel):
    tool_use_id: str
    content: list[ToolResultModel]
    status: Literal["error", "success"]

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, v: Any) -> list:
        if type(v) == list:
            return v

        else:
            # For backward compatibility
            return [v]

    @classmethod
    def from_tool_result_content_body(cls, body: ToolResultContentBody) -> Self:
        return cls(
            tool_use_id=body.tool_use_id,
            content=[
                tool_result_model_from_tool_result(tool_result=tool_result)
                for tool_result in body.content
            ],
            status=body.status,
        )

    def to_tool_result_for_converse(self) -> ToolResultBlockTypeDef:
        return {
            "toolUseId": self.tool_use_id,
            "status": self.status,
            "content": [content.to_content_for_converse() for content in self.content],
        }

    def to_tool_result_content_body(self) -> ToolResultContentBody:
        return ToolResultContentBody(
            tool_use_id=self.tool_use_id,
            content=[content.to_tool_result() for content in self.content],
            status=self.status,
        )


class ToolResultContentModel(BaseModel):
    content_type: Literal["toolResult"] = Field(
        ..., description="Content type. Note that image is only available for claude 3."
    )
    body: ToolResultContentModelBody

    @classmethod
    def from_tool_result_content(cls, content: ToolResultContent) -> Self:
        return cls(
            content_type="toolResult",
            body=ToolResultContentModelBody.from_tool_result_content_body(content.body),
        )

    @classmethod
    def from_tool_run_result(
        cls,
        run_result: ToolRunResult,
        model: type_model_name,
        display_citation: bool,
    ) -> Self:
        result_contents = [
            related_document.to_tool_result_model(
                display_citation=display_citation,
            )
            for related_document in run_result["related_documents"]
        ]

        from app.bedrock import is_nova_model

        if is_nova_model(model=model):
            text_or_json_contents = [
                result_content
                for result_content in result_contents
                if isinstance(result_content, TextToolResultModel)
                or isinstance(result_content, JsonToolResultModel)
            ]
            if len(text_or_json_contents) > 1:
                return cls(
                    content_type="toolResult",
                    body=ToolResultContentModelBody(
                        tool_use_id=run_result["tool_use_id"],
                        content=[
                            TextToolResultModel(
                                text=json.dumps(
                                    [
                                        (
                                            content.json_
                                            if isinstance(content, JsonToolResultModel)
                                            else content.text
                                        )
                                        for content in text_or_json_contents
                                    ]
                                ),
                            ),
                        ],
                        status=run_result["status"],
                    ),
                )

        return cls(
            content_type="toolResult",
            body=ToolResultContentModelBody(
                tool_use_id=run_result["tool_use_id"],
                content=result_contents,
                status=run_result["status"],
            ),
        )

    def to_content(self) -> Content:
        return ToolResultContent(
            content_type="toolResult",
            body=self.body.to_tool_result_content_body(),
        )

    def to_contents_for_converse(self) -> list[ContentBlockTypeDef]:
        return [
            {
                "toolResult": self.body.to_tool_result_for_converse(),
            },
        ]

    def to_contents_for_invoke(self) -> list[dict[str, Any]]:
        """Convert to Claude 4 invoke API format"""
        # Convert tool result content to text format for invoke API
        content_text = ""
        for content in self.body.content:
            if isinstance(content, TextToolResultModel):
                content_text += content.text
            elif isinstance(content, JsonToolResultModel):
                content_text += json.dumps(content.json_)
            elif isinstance(content, ImageToolResultModel):
                content_text += f"[Image: {content.format} format]"
            elif isinstance(content, DocumentToolResultModel):
                content_text += f"[Document: {content.name}]"

        return [
            {
                "type": "tool_result",
                "tool_use_id": self.body.tool_use_id,
                "content": content_text,
            }
        ]


class ReasoningContentModel(BaseModel):
    content_type: Literal["reasoning"]
    text: str
    signature: str
    redacted_content: Base64EncodedBytes

    def to_content(self) -> Content:
        return ReasoningContent(
            content_type="reasoning",
            text=self.text,
            signature=self.signature,
            redacted_content=self.redacted_content,
        )

    def to_contents_for_converse(self) -> list[ContentBlockTypeDef]:
        # Ref: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-runtime/client/converse.html
        # Ref: https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking

        if self.text:
            return [
                {
                    "reasoningContent": {  # type: ignore
                        "reasoningText": {
                            "text": self.text,
                            "signature": self.signature,
                        },
                    }
                }
            ]
        else:
            return [
                {
                    "reasoningContent": {  # type: ignore
                        "redactedContent": {"data": self.redacted_content},
                    }
                }
            ]

    def to_contents_for_invoke(self) -> list[dict[str, Any]]:
        """Convert to Claude 4 invoke API format"""
        # Reasoning content is handled differently in invoke API
        if self.text:
            return [
                {
                    "type": "text",
                    "text": f"<thinking>\n{self.text}\n</thinking>",
                }
            ]
        else:
            return [
                {
                    "type": "text",
                    "text": "<thinking>[Redacted reasoning content]</thinking>",
                }
            ]


ContentModel = Annotated[
    TextContentModel
    | ImageContentModel
    | AttachmentContentModel
    | ToolUseContentModel
    | ToolResultContentModel
    | ReasoningContentModel,
    Discriminator("content_type"),
]


def content_model_from_content(content: Content) -> ContentModel:

    if isinstance(content, TextContent):
        return TextContentModel.from_text_content(content=content)

    elif isinstance(content, ImageContent):
        return ImageContentModel.from_image_content(content=content)

    elif isinstance(content, AttachmentContent):
        return AttachmentContentModel.from_attachment_content(content=content)

    elif isinstance(content, ToolUseContent):
        return ToolUseContentModel.from_tool_use_content(content=content)

    elif isinstance(content, ToolResultContent):
        return ToolResultContentModel.from_tool_result_content(content=content)
    else:
        raise ValueError(f"Unknown content type")


class SimpleMessageModel(BaseModel):
    role: str
    content: list[ContentModel]

    @classmethod
    def from_message_model(cls, message: MessageModel):
        return SimpleMessageModel(
            role=message.role,
            content=message.content,
        )

    def to_schema(self) -> SimpleMessage:
        return SimpleMessage(
            role=self.role,
            content=[content.to_content() for content in self.content],
        )


class MessageModel(BaseModel):
    role: str
    content: list[ContentModel]
    model: type_model_name
    children: list[str]
    parent: str | None
    create_time: float
    feedback: FeedbackModel | None = None
    used_chunks: list[ChunkModel] | None = None
    thinking_log: list[SimpleMessageModel] | None = Field(
        default=None, description="Only available for agent."
    )

    @field_validator("thinking_log", mode="before")
    @classmethod
    def validate_thinking_log(cls, v: Any) -> list | None:
        if type(v) == list:
            return v

        else:
            # For backward compatibility
            return None

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, v: Any) -> list:
        if type(v) == list:
            return v

        else:
            # For backward compatibility
            return [v]

    @classmethod
    def from_message_input(cls, message_input: MessageInput):
        return MessageModel(
            role=message_input.role,
            content=[
                content_model_from_content(content=content)
                for content in message_input.content
            ],
            model=message_input.model,
            children=[],
            parent=message_input.parent_message_id,
            create_time=0,
            feedback=None,
            used_chunks=None,
            thinking_log=None,
        )


class ConversationModel(BaseModel):
    id: str
    create_time: float
    title: str
    total_price: float
    message_map: dict[str, MessageModel]
    last_message_id: str
    bot_id: str | None
    should_continue: bool


class ConversationMeta(BaseModel):
    id: str
    title: str
    create_time: float
    model: str
    bot_id: str | None


class RelatedDocumentModel(BaseModel):
    content: ToolResultModel
    source_id: str
    source_name: str | None = None
    source_link: str | None = None
    page_number: int | None = None

    def to_tool_result_model(self, display_citation: bool) -> ToolResultModel:
        if isinstance(self.content, TextToolResultModel):
            if display_citation:
                return JsonToolResultModel(
                    json={
                        "source_id": self.source_id,
                        "content": self.content.text,
                    },
                )

            else:
                return self.content

        elif isinstance(self.content, JsonToolResultModel):
            if display_citation:
                return JsonToolResultModel(
                    json={
                        "source_id": self.source_id,
                        "content": self.content.json_,
                    },
                )

            else:
                return self.content

        else:
            return self.content

    def get_source_link_for_schema(self) -> str | None:
        if self.source_link is None:
            return None

        url = urlparse(url=self.source_link)
        if url.scheme == "s3":
            source_link = generate_presigned_url(
                bucket=url.netloc,
                key=url.path.removeprefix("/"),
                client_method="get_object",
            )
            return source_link

        else:
            # Return the source as is for knowledge base references
            return self.source_link

    def to_schema(self) -> RelatedDocument:
        return RelatedDocument(
            content=self.content.to_tool_result(),
            source_id=self.source_id,
            source_name=self.source_name,
            source_link=self.get_source_link_for_schema(),
            page_number=self.page_number,
        )
