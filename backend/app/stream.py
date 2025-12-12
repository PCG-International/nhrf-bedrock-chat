import json
import logging
import re
from typing import Callable, TypedDict, TypeGuard

from app.agents.tools.agent_tool import AgentTool
from app.bedrock import (
    BedrockThrottlingException,
    calculate_price,
    compose_args_for_converse_api,
    compose_args_for_invoke_api,
    is_claude_4_model,
)
from app.repositories.models.conversation import (
    ContentModel,
    MessageModel,
    ReasoningContentModel,
    SimpleMessageModel,
    TextContentModel,
    ToolUseContentModel,
    ToolUseContentModelBody,
)
from app.repositories.models.custom_bot import GenerationParamsModel
from app.repositories.models.custom_bot_guardrails import BedrockGuardrailsModel
from app.routes.schemas.conversation import type_model_name
from app.utils import BEDROCK_REGION, get_bedrock_runtime_client, get_current_time
from botocore.exceptions import ClientError
from mypy_boto3_bedrock_runtime.literals import ConversationRoleType, StopReasonType
from mypy_boto3_bedrock_runtime.type_defs import GuardrailConverseContentBlockTypeDef
from pydantic import JsonValue
from reretry import retry

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class OnStopInput(TypedDict):
    message: MessageModel
    stop_reason: StopReasonType
    input_token_count: int
    output_token_count: int
    cache_read_input_count: int
    cache_write_input_count: int
    price: float


class OnThinking(TypedDict):
    tool_use_id: str
    name: str
    input: dict[str, JsonValue]


class _PartialTextContent(TypedDict):
    text: str


class _PartialToolUseContentBody(TypedDict):
    tool_use_id: str
    name: str
    input: str


class _PartialToolUseContent(TypedDict):
    tool_use: _PartialToolUseContentBody


class _PartialReasoningContent(TypedDict):
    text: str
    signature: str
    redacted_content: bytes


class _PartialMessage(TypedDict):
    role: ConversationRoleType
    contents: dict[
        int, _PartialTextContent | _PartialToolUseContent | _PartialReasoningContent
    ]


def _is_text_content(
    content: _PartialTextContent | _PartialToolUseContent | _PartialReasoningContent,
) -> TypeGuard[_PartialTextContent]:
    return "text" in content and (
        "signature" not in content and "redacted_content" not in content
    )


def _is_tool_use_content(
    content: _PartialTextContent | _PartialToolUseContent | _PartialReasoningContent,
) -> TypeGuard[_PartialToolUseContent]:
    return "tool_use" in content


def _is_reasoning_content(
    content: _PartialTextContent | _PartialToolUseContent | _PartialReasoningContent,
) -> TypeGuard[_PartialReasoningContent]:
    return "signature" in content or "redacted_content" in content


def _sanitize_text(text: str) -> str:
    """Remove internal processing artifacts from text content."""
    # Handle None or empty text
    if not text:
        return ""

    # Remove internal tool use patterns (not legitimate citations)
    text = re.sub(r"\[\^tooluse_[^]]+\]", "", text)
    text = re.sub(r"\[\^new-message-assistant[^]]*\]", "", text)

    # Remove any stray tool_use_id patterns
    text = re.sub(r"tool_use_id:\s*[A-Za-z0-9_-]+", "", text)

    # Remove JSON fragments that might leak
    text = re.sub(r'{"tool_use":[^}]*}', "", text)

    # Remove Claude 4 internal search reasoning tags that leak into response
    text = re.sub(
        r"<search_quality_score>.*?</search_quality_score>\s*",
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"<search_quality_reasoning>.*?</search_quality_reasoning>\s*",
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"<search_query>.*?</search_query>\s*", "", text, flags=re.DOTALL)

    # Remove leaked tool use XML blocks (Claude 4 sometimes outputs these in text)
    text = re.sub(r"<function_calls>.*?</function_calls>\s*", "", text, flags=re.DOTALL)
    text = re.sub(r"<invoke[^>]*>.*?</invoke>\s*", "", text, flags=re.DOTALL)
    text = re.sub(
        r"<function_result>.*?</function_result>\s*", "", text, flags=re.DOTALL
    )
    text = re.sub(r"<parameter[^>]*>.*?</parameter>\s*", "", text, flags=re.DOTALL)

    return text.strip()


def _content_model_from_partial_content(
    content: _PartialTextContent | _PartialToolUseContent,
) -> ContentModel:
    if _is_text_content(content=content):
        text = content.get("text") or ""
        return TextContentModel(
            content_type="text",
            body=_sanitize_text(text.rstrip()),
        )

    elif _is_tool_use_content(content=content):
        return ToolUseContentModel(
            content_type="toolUse",
            body=ToolUseContentModelBody(
                tool_use_id=content["tool_use"]["tool_use_id"],
                name=content["tool_use"]["name"],
                input=json.loads(content["tool_use"]["input"] or "{}"),
            ),
        )

    elif _is_reasoning_content(content=content):
        return ReasoningContentModel(
            content_type="reasoning",
            text=content["text"],
            signature=content["signature"],
            redacted_content=content["redacted_content"],
        )

    else:
        raise ValueError(f"Unknown content type")


def _content_model_to_partial_content(
    content: ContentModel,
) -> _PartialTextContent | _PartialToolUseContent | _PartialReasoningContent:
    if isinstance(content, TextContentModel):
        return {
            "text": content.body,
        }

    elif isinstance(content, ToolUseContentModel):
        return {
            "tool_use": {
                "tool_use_id": content.body.tool_use_id,
                "name": content.body.name,
                "input": json.dumps(content.body.input),
            },
        }
    elif isinstance(content, ReasoningContentModel):
        return {
            "text": content.text,
            "signature": content.signature,
            "redacted_content": content.redacted_content,
        }

    else:
        raise ValueError(f"Unknown content type")


class ConverseApiStreamHandler:
    """Stream handler using Converse API.
    Ref: https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
    """

    def __init__(
        self,
        model: type_model_name,
        instructions: list[str] = [],
        generation_params: GenerationParamsModel | None = None,
        guardrail: BedrockGuardrailsModel | None = None,
        tools: dict[str, AgentTool] | None = None,
        on_stream: Callable[[str], None] | None = None,
        on_thinking: Callable[[OnThinking], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ):
        """Base class for stream handlers.
        :param model: Model name.
        :param on_stream: Callback function for streaming.
        :param on_stop: Callback function for stopping the stream.
        """
        self.model: type_model_name = model
        self.instructions = instructions
        self.generation_params = generation_params
        self.guardrail = guardrail
        self.tools = tools
        self.on_stream = on_stream
        self.on_thinking = on_thinking
        self.on_reasoning = on_reasoning

    def _requires_invoke_api_for_cross_region(self) -> bool:
        """Check if model requires Invoke API for cross-region inference.

        Models not available in eu-central-1 that need US cross-region routing
        must use Invoke API because Converse API doesn't support cross-region inference profiles.
        """
        cross_region_only_models = ["deepseek-r1", "claude-v4.1-opus", "claude-v4-opus"]
        return self.model in cross_region_only_models

    @retry(
        exceptions=(BedrockThrottlingException,),
        tries=3,
        delay=60,
        backoff=2,
        jitter=(0, 2),
        logger=logger,
    )
    def run(
        self,
        messages: list[SimpleMessageModel],
        grounding_source: GuardrailConverseContentBlockTypeDef | None = None,
        message_for_continue_generate: SimpleMessageModel | None = None,
        enable_reasoning: bool = False,
        prompt_caching_enabled: bool = False,
    ) -> OnStopInput:
        try:
            # Check if model requires Invoke API (Claude 4 or cross-region only models)
            # BUT: Use Converse API when tools are present (for proper tool execution)
            use_invoke_api = (
                is_claude_4_model(self.model)
                or self._requires_invoke_api_for_cross_region()
            ) and not self.tools  # Don't use invoke API if we have tools

            if use_invoke_api:
                return self._run_invoke_api(
                    messages=messages,
                    message_for_continue_generate=message_for_continue_generate,
                )

            # Create payload to invoke Bedrock (original converse API)
            args = compose_args_for_converse_api(
                messages=messages,
                model=self.model,
                instructions=self.instructions,
                generation_params=self.generation_params,
                guardrail=self.guardrail,
                grounding_source=grounding_source,
                tools=self.tools,
                enable_reasoning=enable_reasoning,
                prompt_caching_enabled=prompt_caching_enabled,
            )
            logger.info(f"args for converse_stream: {args}")

            # Use the appropriate region for the model
            # US-only models (Claude 4 Opus, Claude 4.1 Opus) must be called from a US region
            model_region = self._get_region_for_model()
            logger.info(f"Using region: {model_region} for model: {self.model}")
            client = get_bedrock_runtime_client(region=model_region)
            try:
                response = client.converse_stream(**args)
            except ClientError as e:
                error_msg = str(e) if e else ""
                # Check if error is due to document size limit (4.5 MB for ConverseStream)
                if "maximum document size is 4.5 MB" in error_msg:
                    logger.warning(
                        f"Document size exceeds ConverseStream limit, falling back to InvokeModel API"
                    )
                    return self._run_invoke_api(
                        messages=messages,
                        message_for_continue_generate=message_for_continue_generate,
                    )
                elif e.response.get("Error", {}).get("Code") == "ThrottlingException":
                    raise BedrockThrottlingException(
                        "Bedrock API is throttling requests"
                    ) from e
                raise

            current_message = _PartialMessage(
                role="assistant",
                contents=(
                    {
                        index: _content_model_to_partial_content(content=content)
                        for index, content in enumerate(
                            message_for_continue_generate.content
                        )
                    }
                    if message_for_continue_generate is not None
                    else {}
                ),
            )
            current_errors: list[Exception] = []
            stop_reason: StopReasonType = "end_turn"
            input_token_count = 0
            output_token_count = 0
            cache_read_input_count = 0
            cache_write_input_count = 0
            for event in response["stream"]:
                logger.debug(f"event: {event}")
                if "messageStart" in event:
                    message_start = event["messageStart"]
                    current_message["role"] = message_start["role"]

                elif "contentBlockStart" in event:
                    content_block_start = event["contentBlockStart"]
                    index = content_block_start["contentBlockIndex"]
                    start = content_block_start.get("start", {})
                    tool_use = start.get("toolUse")
                    if tool_use is not None:
                        tool_use_id = tool_use["toolUseId"]
                        tool_name = tool_use["name"]

                        tool_use_content: _PartialToolUseContent = {
                            "tool_use": {
                                "tool_use_id": tool_use_id,
                                "name": tool_name,
                                "input": "",
                            }
                        }
                        current_message["contents"][index] = tool_use_content

                elif "contentBlockDelta" in event:
                    content_block_delta = event["contentBlockDelta"]
                    index = content_block_delta["contentBlockIndex"]
                    delta = content_block_delta["delta"]

                    if "reasoningContent" in delta:
                        reasoning = delta["reasoningContent"]
                        if index in current_message["contents"]:
                            content = current_message["contents"][index]
                            if _is_reasoning_content(content=content):
                                content["text"] += reasoning.get("text", "")
                                if "signature" in reasoning:
                                    content["signature"] = reasoning["signature"]
                                if "redactedContent" in reasoning:
                                    content["redacted_content"] = reasoning[
                                        "redactedContent"
                                    ]
                            else:
                                # Should not happen
                                logger.warning(
                                    f"Unexpected reasoning content: {content}"
                                )
                        else:
                            # If the block is not started, create a new block
                            current_message["contents"][index] = {
                                "text": reasoning.get("text", ""),
                                "signature": reasoning.get("signature", ""),
                                "redacted_content": reasoning.get(
                                    "redactedContent", b""
                                ),
                            }
                        if self.on_reasoning:
                            # Only text is streamed
                            self.on_reasoning(reasoning.get("text", ""))

                    elif "toolUse" in delta:
                        input = delta["toolUse"]["input"]
                        if index in current_message["contents"]:
                            content = current_message["contents"][index]
                            if _is_tool_use_content(content=content):
                                content["tool_use"]["input"] += input

                    elif "text" in delta:
                        text = delta["text"]
                        if index in current_message["contents"]:
                            content = current_message["contents"][index]
                            if _is_text_content(content=content):
                                content["text"] += text

                        else:
                            text_content: _PartialTextContent = {
                                "text": text,
                            }
                            current_message["contents"][index] = text_content

                        if self.on_stream:
                            self.on_stream(text)

                elif "contentBlockStop" in event:
                    content_block_stop = event["contentBlockStop"]
                    index = content_block_stop["contentBlockIndex"]
                    content = current_message["contents"][index]
                    if _is_tool_use_content(content=content):
                        tool_use = content["tool_use"]
                        tool_use_id = tool_use["tool_use_id"]
                        tool_name = tool_use["name"]
                        input = json.loads(tool_use["input"] or "{}")

                        if self.on_thinking:
                            self.on_thinking(
                                {
                                    "tool_use_id": tool_use_id,
                                    "name": tool_name,
                                    "input": input,
                                }
                            )

                elif "messageStop" in event:
                    stop_reason = event["messageStop"]["stopReason"]

                elif "metadata" in event:
                    metadata = event["metadata"]
                    usage = metadata["usage"]
                    input_token_count = usage["inputTokens"]
                    output_token_count = usage["outputTokens"]
                    cache_read_input_count = usage.get("cacheReadInputTokens") or 0
                    cache_write_input_count = usage.get("cacheWriteInputTokens") or 0

                elif "modelStreamErrorException" in event:
                    exception = event["modelStreamErrorException"]
                    message = exception.get("message")
                    original_status_code = exception.get("originalStatusCode")
                    original_message = exception.get("originalMessage")
                    current_errors.append(
                        client.exceptions.ModelStreamErrorException(
                            error_response={
                                "Error": {
                                    "Code": "ModelStreamErrorException",
                                    "Message": message,
                                    "OriginalStatusCode": original_status_code,
                                    "OriginalMessage": original_message,
                                },
                            },
                            operation_name="ConverseStream",
                        )
                    )

                elif "throttlingException" in event:
                    exception = event["throttlingException"]
                    message = exception.get("message")
                    current_errors.append(
                        client.exceptions.ThrottlingException(
                            error_response={
                                "Error": {
                                    "Code": "ThrottlingException",
                                    "Message": message,
                                },
                            },
                            operation_name="ConverseStream",
                        )
                    )

                elif "internalServerException" in event:
                    exception = event["internalServerException"]
                    message = exception.get("message")
                    current_errors.append(
                        client.exceptions.InternalServerException(
                            error_response={
                                "Error": {
                                    "Code": "InternalServerException",
                                    "Message": message,
                                },
                            },
                            operation_name="ConverseStream",
                        )
                    )

                elif "serviceUnavailableException" in event:
                    exception = event["serviceUnavailableException"]
                    message = exception.get("message")
                    current_errors.append(
                        client.exceptions.ServiceUnavailableException(
                            error_response={
                                "Error": {
                                    "Code": "ServiceUnavailableException",
                                    "Message": message,
                                },
                            },
                            operation_name="ConverseStream",
                        )
                    )

                elif "validationException" in event:
                    exception = event["validationException"]
                    message = exception.get("message")
                    current_errors.append(
                        client.exceptions.ValidationException(
                            error_response={
                                "Error": {
                                    "Code": "ValidationException",
                                    "Message": message,
                                },
                            },
                            operation_name="ConverseStream",
                        )
                    )

            if len(current_errors) > 0:
                if len(current_errors) == 1:
                    raise current_errors[0]

                else:
                    raise ExceptionGroup("Exceptions in ConverseStream", current_errors)

            # Validate that we received content - empty messages will corrupt conversations
            if not current_message["contents"]:
                raise ValueError(
                    "Received empty response from Bedrock API. This may be due to a timeout or service interruption."
                )

            # Check if all text content is empty
            has_non_empty_content = False
            for content in current_message["contents"].values():
                if _is_text_content(content) and content.get("text", "").strip():
                    has_non_empty_content = True
                    break
                elif _is_tool_use_content(content):
                    has_non_empty_content = True
                    break
                elif _is_reasoning_content(content):
                    has_non_empty_content = True
                    break

            if not has_non_empty_content:
                raise ValueError(
                    "Received empty text response from Bedrock API. This may be due to a timeout or service interruption."
                )

            # Append entire completion as the last message
            message = MessageModel(
                role="assistant",
                content=[
                    _content_model_from_partial_content(content=content)
                    for _, content in sorted(current_message["contents"].items())
                ],
                model=self.model,
                children=[],
                parent=None,
                create_time=get_current_time(),
                feedback=None,
                used_chunks=None,
                thinking_log=None,
            )

            price = calculate_price(
                model=self.model,
                input_tokens=input_token_count,
                output_tokens=output_token_count,
                cache_read_input_tokens=cache_read_input_count,
                cache_write_input_tokens=cache_write_input_count,
            )
            logger.info(
                f"token count: {json.dumps({
                    'input': input_token_count,
                    'output': output_token_count,
                    'cache_read_input': cache_read_input_count,
                    'cache_write_input': cache_write_input_count
                })}"
            )
            logger.info(f"price: {price}")

            result = OnStopInput(
                message=message,
                stop_reason=stop_reason,
                input_token_count=input_token_count,
                output_token_count=output_token_count,
                cache_read_input_count=cache_read_input_count,
                cache_write_input_count=cache_write_input_count,
                price=price,
            )
            return result

        except Exception as e:
            logger.error(f"Error: {e}")
            raise e

    def _get_region_for_model(self) -> str:
        """Get the appropriate AWS region for the model.

        Some models are only available in specific regions and must be called
        directly in those regions (not via cross-region inference profiles).
        """
        us_only_models = ["deepseek-r1", "claude-v4.1-opus", "claude-v4-opus"]
        if self.model in us_only_models:
            return "us-east-1"
        return BEDROCK_REGION

    def _run_invoke_api(
        self,
        messages: list[SimpleMessageModel],
        message_for_continue_generate: SimpleMessageModel | None = None,
    ) -> OnStopInput:
        """Handle Claude 4 models using the invoke API for file upload support"""
        try:
            # Get the appropriate region for this model
            model_region = self._get_region_for_model()

            # Note: Tools are not passed to invoke API yet because full tool use
            # support (executing tools and returning results) is not implemented.
            # The model may output XML-style tool calls which are sanitized.

            # Create payload for invoke API
            args = compose_args_for_invoke_api(
                messages=messages,
                model=self.model,
                instructions=self.instructions,
                generation_params=self.generation_params,
                stream=True,
                target_region=model_region,
            )
            logger.info(f"args for invoke_model_with_response_stream: {args}")
            logger.info(f"Using region: {model_region} for model: {self.model}")

            # Use region-specific client for US-only models
            client = get_bedrock_runtime_client(region=model_region)
            try:
                response = client.invoke_model_with_response_stream(**args)
            except ClientError as e:
                if e.response["Error"]["Code"] == "ThrottlingException":
                    raise BedrockThrottlingException(
                        "Bedrock API is throttling requests"
                    ) from e
                raise

            # Process the streaming response for Claude 4 invoke API
            current_text = ""
            current_thinking = ""
            input_token_count = 0
            output_token_count = 0
            stop_reason: StopReasonType = "end_turn"
            event_count = 0
            # Track content block types by index
            content_block_types: dict[int, str] = {}

            for event in response["body"]:
                chunk = event.get("chunk")
                if chunk:
                    chunk_data = json.loads(chunk["bytes"].decode())
                    event_count += 1
                    event_type = chunk_data.get("type", "unknown")
                    # Log ALL events at INFO level to diagnose empty responses
                    logger.info(
                        f"Claude 4 event #{event_count}: {event_type} - {json.dumps(chunk_data)[:500]}"
                    )

                    if event_type == "message_start":
                        usage = chunk_data.get("message", {}).get("usage", {})
                        input_token_count = usage.get("input_tokens", 0)

                    elif event_type == "content_block_start":
                        # Track the type of each content block
                        index = chunk_data.get("index", 0)
                        content_block = chunk_data.get("content_block", {})
                        block_type = content_block.get("type", "text")
                        content_block_types[index] = block_type
                        logger.debug(
                            f"Content block {index} started: type={block_type}"
                        )

                    elif event_type == "content_block_delta":
                        index = chunk_data.get("index", 0)
                        delta = chunk_data.get("delta", {})
                        delta_type = delta.get("type", "")

                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            current_text += text
                            if self.on_stream:
                                self.on_stream(text)
                        elif delta_type == "thinking_delta":
                            # Capture thinking content for extended thinking models
                            thinking = delta.get("thinking", "")
                            current_thinking += thinking
                            if self.on_reasoning:
                                self.on_reasoning(thinking)
                        elif delta_type == "input_json_delta":
                            # Tool use input - log but don't process yet
                            # Full tool use for invoke API requires multi-turn handling
                            logger.debug(
                                f"Tool use input delta (not executed): {delta.get('partial_json', '')[:100]}"
                            )

                    elif event_type == "message_delta":
                        delta = chunk_data.get("delta", {})
                        if "stop_reason" in delta:
                            stop_reason = delta["stop_reason"]
                        usage = chunk_data.get("usage", {})
                        output_token_count = usage.get("output_tokens", 0)

                    elif event_type == "error":
                        error_msg = chunk_data.get("error", {}).get(
                            "message", "Unknown error"
                        )
                        logger.error(f"Claude 4 invoke API error: {error_msg}")
                        raise ValueError(f"Bedrock API error: {error_msg}")

            logger.info(
                f"Claude 4 invoke API processed {event_count} events, text_length={len(current_text)}, thinking_length={len(current_thinking)}, stop_reason={stop_reason}"
            )

            # Sanitize text to remove internal reasoning tags before checking for empty
            sanitized_for_check = _sanitize_text(current_text)

            # Validate that we received content - empty messages will corrupt conversations
            if not sanitized_for_check.strip():
                # Log detailed diagnostic info
                logger.error(
                    f"Empty response from Claude 4: events={event_count}, content_blocks={content_block_types}, stop_reason={stop_reason}, thinking_length={len(current_thinking)}"
                )
                # If we have thinking but no text, include that in the error
                if current_thinking:
                    raise ValueError(
                        f"Model returned thinking ({len(current_thinking)} chars) but no text response after {event_count} events. Stop reason: {stop_reason}"
                    )
                raise ValueError(
                    f"Received empty response from Bedrock API after {event_count} events. Stop reason: {stop_reason}. Check CloudWatch logs for event details."
                )

            # Create the final message with sanitized text (reuse the sanitized version)
            message = MessageModel(
                role="assistant",
                content=[
                    TextContentModel(
                        content_type="text",
                        body=sanitized_for_check,
                    )
                ],
                model=self.model,
                children=[],
                parent=None,
                create_time=get_current_time(),
                feedback=None,
                used_chunks=None,
                thinking_log=None,
            )

            price = calculate_price(
                model=self.model,
                input_tokens=input_token_count,
                output_tokens=output_token_count,
                cache_read_input_tokens=0,  # Claude 4 invoke API doesn't support prompt caching yet
                cache_write_input_tokens=0,  # Claude 4 invoke API doesn't support prompt caching yet
            )

            result = OnStopInput(
                message=message,
                stop_reason=stop_reason,
                input_token_count=input_token_count,
                output_token_count=output_token_count,
                cache_read_input_count=0,  # Claude 4 invoke API doesn't support prompt caching yet
                cache_write_input_count=0,  # Claude 4 invoke API doesn't support prompt caching yet
                price=price,
            )
            return result

        except Exception as e:
            logger.error(f"Error in invoke API: {e}")
            raise e
