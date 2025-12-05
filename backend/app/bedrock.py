from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Literal, Optional, Tuple, TypeGuard

from app.config import (
    BEDROCK_PRICING,
    DEFAULT_DEEP_SEEK_GENERATION_CONFIG,
    DEFAULT_GENERATION_CONFIG,
)
from app.repositories.models.custom_bot import GenerationParamsModel
from app.repositories.models.custom_bot_guardrails import BedrockGuardrailsModel
from app.routes.schemas.conversation import type_model_name
from app.utils import get_bedrock_runtime_client
from botocore.exceptions import ClientError
from reretry import retry

if TYPE_CHECKING:
    from app.agents.tools.agent_tool import AgentTool
    from app.repositories.models.conversation import ContentModel, SimpleMessageModel
    from mypy_boto3_bedrock_runtime.literals import ConversationRoleType
    from mypy_boto3_bedrock_runtime.type_defs import (
        ContentBlockTypeDef,
        ConverseResponseTypeDef,
        ConverseStreamRequestTypeDef,
        GuardrailConverseContentBlockTypeDef,
        InferenceConfigurationTypeDef,
        InvokeModelRequestTypeDef,
        InvokeModelResponseTypeDef,
        InvokeModelWithResponseStreamRequestTypeDef,
        MessageTypeDef,
        SystemContentBlockTypeDef,
        ToolTypeDef,
    )


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
ENABLE_BEDROCK_CROSS_REGION_INFERENCE = (
    os.environ.get("ENABLE_BEDROCK_CROSS_REGION_INFERENCE", "false") == "true"
)

client = get_bedrock_runtime_client()


class BedrockThrottlingException(Exception): ...


def _is_conversation_role(role: str) -> TypeGuard[ConversationRoleType]:
    return role in ["user", "assistant"]


def is_nova_model(model: type_model_name) -> bool:
    """Check if the model is an Amazon Nova model"""
    return "amazon-nova" in model


def is_deepseek_model(model: type_model_name) -> bool:
    """Check if the model is a DeepSeek model"""
    return "deepseek" in model


def is_claude_4_model(model: type_model_name) -> bool:
    """Check if the model is a Claude 4 model"""
    return model in ["claude-v4-opus", "claude-v4-sonnet"]


def is_tooluse_supported(model: type_model_name) -> bool:
    """Check if the model is supported for tool use"""
    return model not in [
        "deepseek-r1",
        "",
    ]


def is_prompt_caching_supported(
    model: type_model_name, target: Literal["system", "message", "tool"]
) -> bool:
    if target == "tool":
        return model in [
            "claude-v4-opus",
            "claude-v4.1-opus",
            "claude-v4-sonnet",
            "claude-v3.7-sonnet",
            "claude-v3.5-sonnet-v2",
            "claude-v3.5-haiku",
        ]

    else:
        return model in [
            "claude-v4-opus",
            "claude-v4.1-opus",
            "claude-v4-sonnet",
            "claude-v3.7-sonnet",
            "claude-v3.5-sonnet-v2",
            "claude-v3.5-haiku",
            "amazon-nova-pro",
            "amazon-nova-lite",
            "amazon-nova-micro",
        ]


def _prepare_deepseek_model_params(
    model: type_model_name, generation_params: Optional[GenerationParamsModel] = None
) -> Tuple[InferenceConfigurationTypeDef, None]:
    """
    Prepare inference configuration and additional model request fields for DeepSeek models
    > Note that DeepSeek models expect inference parameters as a JSON object under an inferenceConfig attribute,
    > similar to Amazon Nova models.
    """
    # Base inference configuration
    inference_config: InferenceConfigurationTypeDef = {
        "maxTokens": (
            generation_params.max_tokens
            if generation_params
            else DEFAULT_DEEP_SEEK_GENERATION_CONFIG["max_tokens"]
        ),
        "temperature": (
            generation_params.temperature
            if generation_params
            else DEFAULT_DEEP_SEEK_GENERATION_CONFIG["temperature"]
        ),
        "topP": (
            generation_params.top_p
            if generation_params
            else DEFAULT_DEEP_SEEK_GENERATION_CONFIG["top_p"]
        ),
    }

    inference_config["stopSequences"] = (
        generation_params.stop_sequences
        if (
            generation_params
            and generation_params.stop_sequences
            and any(generation_params.stop_sequences)
        )
        else DEFAULT_DEEP_SEEK_GENERATION_CONFIG.get("stop_sequences", [])
    )

    return inference_config, None


def _prepare_nova_model_params(
    model: type_model_name, generation_params: Optional[GenerationParamsModel] = None
) -> Tuple[InferenceConfigurationTypeDef, Dict[str, Any]]:
    """
    Prepare inference configuration and additional model request fields for Nova models
    > Note that Amazon Nova expects inference parameters as a JSON object under a inferenceConfig attribute. Amazon Nova also has an additional parameter "topK" that can be passed as an additional inference parameters. This parameter follows the same structure and is passed through the additionalModelRequestFields, as shown below.
    https://docs.aws.amazon.com/nova/latest/userguide/getting-started-converse.html
    """
    # Base inference configuration
    inference_config: InferenceConfigurationTypeDef = {
        "maxTokens": (
            generation_params.max_tokens
            if generation_params
            else DEFAULT_GENERATION_CONFIG["max_tokens"]
        ),
        "temperature": (
            generation_params.temperature
            if generation_params
            else DEFAULT_GENERATION_CONFIG["temperature"]
        ),
        "topP": (
            generation_params.top_p
            if generation_params
            else DEFAULT_GENERATION_CONFIG["top_p"]
        ),
    }

    # Additional model request fields specific to Nova models
    additional_fields: Dict[str, Any] = {"inferenceConfig": {}}

    # Add top_k if specified in generation params
    if generation_params and generation_params.top_k is not None:
        top_k = generation_params.top_k
        if top_k > 128:
            logger.warning(
                "In Amazon Nova, an 'unexpected error' occurs if topK exceeds 128. To avoid errors, the upper limit of A is set to 128."
            )
            top_k = 128

        additional_fields["inferenceConfig"]["topK"] = top_k

    return inference_config, additional_fields


def compose_args_for_converse_api(
    messages: list[SimpleMessageModel],
    model: type_model_name,
    instructions: list[str] = [],
    generation_params: GenerationParamsModel | None = None,
    guardrail: BedrockGuardrailsModel | None = None,
    grounding_source: GuardrailConverseContentBlockTypeDef | None = None,
    tools: dict[str, AgentTool] | None = None,
    stream: bool = True,
    enable_reasoning: bool = False,
    prompt_caching_enabled: bool = False,
) -> ConverseStreamRequestTypeDef:
    def process_content(c: ContentModel, role: str) -> list[ContentBlockTypeDef]:
        # Drop unsigned reasoning blocks only for DeepSeek R1
        if (
            is_deepseek_model(model)
            and c.content_type == "reasoning"
            and not getattr(c, "signature", None)
        ):
            return []

        # Skip empty text content - Bedrock rejects blank text fields
        if c.content_type == "text" and (not c.body or not c.body.strip()):
            return []

        if c.content_type == "text":
            if (
                role == "user"
                and guardrail
                and guardrail.grounding_threshold > 0
                and grounding_source
            ):
                return [
                    {"guardContent": grounding_source},
                    {
                        "guardContent": {
                            "text": {"text": c.body, "qualifiers": ["query"]}
                        }
                    },
                ]

        return c.to_contents_for_converse()

    arg_messages: list[MessageTypeDef] = [
        {
            "role": message.role,
            "content": [
                block
                for c in message.content
                for block in process_content(c, message.role)
            ],
        }
        for message in messages
        if _is_conversation_role(message.role)
    ]
    tool_specs: list[ToolTypeDef] | None = (
        [
            {
                "toolSpec": tool.to_converse_spec(),
            }
            for tool in tools.values()
        ]
        if tools
        else None
    )

    # Prepare model-specific parameters
    inference_config: InferenceConfigurationTypeDef
    additional_model_request_fields: dict[str, Any] | None
    system_prompts: list[SystemContentBlockTypeDef]

    if is_nova_model(model):
        # Special handling for Nova models
        inference_config, additional_model_request_fields = _prepare_nova_model_params(
            model, generation_params
        )
        system_prompts = (
            [
                {
                    "text": "\n\n".join(instructions),
                }
            ]
            if instructions and any(instructions)
            else []
        )

    elif is_deepseek_model(model):
        # Special handling for DeepSeek models
        inference_config, additional_model_request_fields = (
            _prepare_deepseek_model_params(model, generation_params)
        )
        system_prompts = (
            [
                {
                    "text": "\n\n".join(instructions),
                }
            ]
            if instructions and any(instructions)
            else []
        )

    else:
        # Standard handling for non-Nova models
        if enable_reasoning:
            budget_tokens = (
                generation_params.reasoning_params.budget_tokens
                if generation_params and generation_params.reasoning_params
                else DEFAULT_GENERATION_CONFIG["reasoning_params"]["budget_tokens"]  # type: ignore
            )
            max_tokens = (
                generation_params.max_tokens
                if generation_params
                else DEFAULT_GENERATION_CONFIG["max_tokens"]
            )

            if max_tokens <= budget_tokens:
                logger.warning(
                    f"max_tokens ({max_tokens}) must be greater than budget_tokens ({budget_tokens}). "
                    f"Setting max_tokens to {budget_tokens + 1024}"
                )
                max_tokens = budget_tokens + 1024

            inference_config = {
                "maxTokens": max_tokens,
                "temperature": 1.0,  # Force temperature to 1.0 when reasoning is enabled
                "topP": (
                    generation_params.top_p
                    if generation_params
                    else DEFAULT_GENERATION_CONFIG["top_p"]
                ),
                "stopSequences": (
                    generation_params.stop_sequences
                    if (
                        generation_params
                        and generation_params.stop_sequences
                        and any(generation_params.stop_sequences)
                    )
                    else DEFAULT_GENERATION_CONFIG.get("stop_sequences", [])
                ),
            }
            additional_model_request_fields = {
                # top_k cannot be used with reasoning
                "thinking": {
                    "type": "enabled",
                    "budget_tokens": budget_tokens,
                },
            }
        else:
            inference_config = {
                "maxTokens": (
                    generation_params.max_tokens
                    if generation_params
                    else DEFAULT_GENERATION_CONFIG["max_tokens"]
                ),
                "temperature": (
                    generation_params.temperature
                    if generation_params
                    else DEFAULT_GENERATION_CONFIG["temperature"]
                ),
                "topP": (
                    generation_params.top_p
                    if generation_params
                    else DEFAULT_GENERATION_CONFIG["top_p"]
                ),
                "stopSequences": (
                    generation_params.stop_sequences
                    if (
                        generation_params
                        and generation_params.stop_sequences
                        and any(generation_params.stop_sequences)
                    )
                    else DEFAULT_GENERATION_CONFIG.get("stop_sequences", [])
                ),
            }
            additional_model_request_fields = {
                "top_k": (
                    generation_params.top_k
                    if generation_params
                    else DEFAULT_GENERATION_CONFIG["top_k"]
                ),
            }
        system_prompts = [
            {
                "text": instruction,
            }
            for instruction in instructions
            if len(instruction) > 0
        ]

    if prompt_caching_enabled and not (
        tool_specs and not is_prompt_caching_supported(model, target="tool")
    ):
        if is_prompt_caching_supported(model, "system") and len(system_prompts) > 0:
            system_prompts.append(
                {
                    "cachePoint": {
                        "type": "default",
                    },
                }
            )

        if is_prompt_caching_supported(model, target="message"):
            for order, message in enumerate(
                filter(lambda m: m["role"] == "user", reversed(arg_messages))
            ):
                if order >= 2:
                    break

                message["content"] = [
                    *(message["content"]),
                    {
                        "cachePoint": {"type": "default"},
                    },
                ]

        if is_prompt_caching_supported(model, target="tool") and tool_specs:
            tool_specs.append(
                {
                    "cachePoint": {
                        "type": "default",
                    },
                }
            )

    # Construct the base arguments
    args: ConverseStreamRequestTypeDef = {
        "inferenceConfig": inference_config,
        "modelId": get_model_id(model),
        "messages": arg_messages,
        "system": system_prompts,
    }

    if additional_model_request_fields is not None:
        args["additionalModelRequestFields"] = additional_model_request_fields

    if guardrail and guardrail.guardrail_arn and guardrail.guardrail_version:
        args["guardrailConfig"] = {
            "guardrailIdentifier": guardrail.guardrail_arn,
            "guardrailVersion": guardrail.guardrail_version,
            "trace": "enabled",
        }

        if stream:
            # https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html
            args["guardrailConfig"]["streamProcessingMode"] = "async"

    # NOTE: Some models doesn't support tool use. https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference-supported-models-features.html
    if tool_specs:
        args["toolConfig"] = {
            "tools": tool_specs,
        }

    return args


def compose_args_for_invoke_api(
    messages: list[SimpleMessageModel],
    model: type_model_name,
    instructions: list[str] = [],
    generation_params: GenerationParamsModel | None = None,
    stream: bool = True,
    target_region: str | None = None,
) -> InvokeModelWithResponseStreamRequestTypeDef | InvokeModelRequestTypeDef:
    """
    Compose arguments for Claude 4 models using the invoke API instead of converse API.
    This allows for file uploads that are not supported by the converse API.

    Args:
        target_region: The AWS region where the model will be called. If provided and different from BEDROCK_REGION,
                      cross-region inference will be disabled to use direct model ID.
    """
    # Convert messages to Claude 4 format
    claude_messages = []
    for message in messages:
        if _is_conversation_role(message.role):
            content = []
            for c in message.content:
                content.extend(c.to_contents_for_invoke())
            claude_messages.append({"role": message.role, "content": content})

    # Prepare system prompt
    system_prompt = (
        "\n\n".join(instructions) if instructions and any(instructions) else ""
    )

    # Prepare inference parameters
    max_tokens = (
        generation_params.max_tokens
        if generation_params
        else DEFAULT_GENERATION_CONFIG["max_tokens"]
    )
    temperature = (
        generation_params.temperature
        if generation_params
        else DEFAULT_GENERATION_CONFIG["temperature"]
    )
    top_p = (
        generation_params.top_p
        if generation_params
        else DEFAULT_GENERATION_CONFIG["top_p"]
    )
    top_k = (
        generation_params.top_k
        if generation_params
        else DEFAULT_GENERATION_CONFIG["top_k"]
    )
    stop_sequences = (
        generation_params.stop_sequences
        if (
            generation_params
            and generation_params.stop_sequences
            and any(generation_params.stop_sequences)
        )
        else DEFAULT_GENERATION_CONFIG.get("stop_sequences", [])
    )

    # Compose the body for Claude 4 invoke API
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": claude_messages,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
    }

    if system_prompt:
        body["system"] = system_prompt

    if stop_sequences:
        body["stop_sequences"] = stop_sequences

    # Return appropriate request type based on streaming
    # Always use cross-region inference profiles for target region
    # For us-east-1 models, get_model_id will return us.model-id format

    if stream:
        return {
            "body": json.dumps(body),
            "modelId": get_model_id(
                model,
                enable_cross_region=True,
                bedrock_region=target_region or BEDROCK_REGION,
            ),
            "contentType": "application/json",
            "accept": "application/json",
        }
    else:
        return {
            "body": json.dumps(body),
            "modelId": get_model_id(
                model,
                enable_cross_region=True,
                bedrock_region=target_region or BEDROCK_REGION,
            ),
            "contentType": "application/json",
            "accept": "application/json",
        }


@retry(
    exceptions=(BedrockThrottlingException,),
    tries=3,
    delay=60,
    backoff=2,
    jitter=(0, 2),
    logger=logger,
)
def call_converse_api(
    args: ConverseStreamRequestTypeDef,
) -> ConverseResponseTypeDef:
    client = get_bedrock_runtime_client()
    try:
        return client.converse(**args)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ThrottlingException":
            raise BedrockThrottlingException(
                "Bedrock API is throttling requests"
            ) from e
        raise


@retry(
    exceptions=(BedrockThrottlingException,),
    tries=3,
    delay=60,
    backoff=2,
    jitter=(0, 2),
    logger=logger,
)
def call_invoke_api(
    args: InvokeModelRequestTypeDef,
) -> InvokeModelResponseTypeDef:
    """Call the invoke API for Claude 4 models"""
    client = get_bedrock_runtime_client()
    try:
        return client.invoke_model(**args)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ThrottlingException":
            raise BedrockThrottlingException(
                "Bedrock API is throttling requests"
            ) from e
        raise


def calculate_price(
    model: type_model_name,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int,
    cache_write_input_tokens: int,
    region: str = BEDROCK_REGION,
) -> float:
    input_price = (
        BEDROCK_PRICING.get(region, {})
        .get(model, {})
        .get("input", BEDROCK_PRICING["default"][model]["input"])
    )
    output_price = (
        BEDROCK_PRICING.get(region, {})
        .get(model, {})
        .get("output", BEDROCK_PRICING["default"][model]["output"])
    )
    cache_read_input_price = (
        BEDROCK_PRICING.get(region, {})
        .get(model, {})
        .get(
            "cache_read_input",
            BEDROCK_PRICING["default"][model].get("cache_read_input", input_price),
        )
    )
    cache_write_input_price = (
        BEDROCK_PRICING.get(region, {})
        .get(model, {})
        .get(
            "cache_write_input",
            BEDROCK_PRICING["default"][model].get("cache_write_input", input_price),
        )
    )

    return (
        input_price * input_tokens / 1000.0
        + output_price * output_tokens / 1000.0
        + cache_read_input_price * cache_read_input_tokens / 1000.0
        + cache_write_input_price * cache_write_input_tokens / 1000.0
    )


def get_model_id(
    model: type_model_name,
    enable_cross_region: bool = ENABLE_BEDROCK_CROSS_REGION_INFERENCE,
    bedrock_region: str = BEDROCK_REGION,
) -> str:
    # Ref: https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids-arns.html
    base_model_ids = {
        "claude-v4-opus": "anthropic.claude-opus-4-20250514-v1:0",
        "claude-v4.1-opus": "anthropic.claude-opus-4-1-20250805-v1:0",
        "claude-v4-sonnet": "anthropic.claude-sonnet-4-20250514-v1:0",
        "claude-v3-opus": "anthropic.claude-3-opus-20240229-v1:0",
        "claude-v3.7-sonnet": "anthropic.claude-3-7-sonnet-20250219-v1:0",
        # New Amazon Nova models
        "amazon-nova-pro": "amazon.nova-pro-v1:0",
        "amazon-nova-lite": "amazon.nova-lite-v1:0",
        "amazon-nova-micro": "amazon.nova-micro-v1:0",
        # DeepSeek models
        "deepseek-r1": "deepseek.r1-v1:0",
    }

    # Made this list by scripts/cross_region_inference/get_supported_cross_region_inferences.py
    # Ref: https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
    supported_regions = {
        "us-east-1": {
            "area": "us",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-opus",
                "claude-v4.1-opus",
                "claude-v4-sonnet",
                "claude-v3-opus",
                "claude-v3.7-sonnet",
                "deepseek-r1",
            ],
        },
        "us-east-2": {
            "area": "us",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-opus",
                "claude-v4.1-opus",
                "claude-v4-sonnet",
                "claude-v3.7-sonnet",
                "deepseek-r1",
            ],
        },
        "us-west-2": {
            "area": "us",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-opus",
                "claude-v4.1-opus",
                "claude-v4-sonnet",
                "claude-v3-opus",
                "claude-v3.7-sonnet",
                "deepseek-r1",
            ],
        },
        "eu-central-1": {
            "area": "eu",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-sonnet",
                "claude-v3.7-sonnet",
                # Note: claude-v4.1-opus and deepseek-r1 not available for channel program accounts
            ],
        },
        "eu-west-1": {
            "area": "eu",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-sonnet",
                "claude-v3.7-sonnet",
            ],
        },
        "eu-west-2": {"area": "eu", "models": []},
        "eu-west-3": {
            "area": "eu",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-sonnet",
                "claude-v3.7-sonnet",
            ],
        },
        "eu-north-1": {
            "area": "eu",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
            ],
        },
        "ap-south-1": {
            "area": "apac",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-sonnet",
            ],
        },
        "ap-northeast-1": {
            "area": "apac",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-sonnet",
            ],
        },
        "ap-northeast-2": {
            "area": "apac",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-sonnet",
            ],
        },
        "ap-northeast-3": {"area": "apac", "models": []},
        "ap-southeast-1": {
            "area": "apac",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-sonnet",
            ],
        },
        "ap-southeast-2": {
            "area": "apac",
            "models": [
                "amazon-nova-lite",
                "amazon-nova-micro",
                "amazon-nova-pro",
                "claude-v4-sonnet",
            ],
        },
    }

    base_model_id = base_model_ids.get(model)
    if not base_model_id:
        raise ValueError(f"Unsupported model: {model}")

    model_id = base_model_id

    if enable_cross_region:
        if (
            bedrock_region in supported_regions
            and model in supported_regions[bedrock_region]["models"]
        ):
            region_prefix = supported_regions[bedrock_region]["area"]

            # For models only available in US regions (DeepSeek, Claude 4.1 Opus),
            # use US prefix even when calling from EU
            us_only_models = ["deepseek-r1", "claude-v4.1-opus", "claude-v4-opus"]
            if model in us_only_models:
                region_prefix = "us"
                logger.info(
                    f"Model '{model}' only available in US regions, using US inference profile from '{bedrock_region}'"
                )

            model_id = f"{region_prefix}.{base_model_id}"
            logger.info(
                f"Using cross-region model ID: {model_id} for model '{model}' in region '{BEDROCK_REGION}'"
            )
        else:
            logger.warning(
                f"Region '{bedrock_region}' does not support cross-region inference for model '{model}'."
            )
    else:
        logger.info(f"Using local model ID: {model_id} for model '{model}'")

    return model_id
