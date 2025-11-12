from typing_extensions import NotRequired, TypedDict


class GenerationParams(TypedDict):
    max_tokens: int
    top_k: NotRequired[int]
    top_p: float
    temperature: float
    stop_sequences: list[str]
    reasoning_params: NotRequired[dict[str, int]]


class EmbeddingConfig(TypedDict):
    model_id: str
    chunk_size: int
    chunk_overlap: int
    enable_partition_pdf: bool


# Configure generation parameter for Claude chat response.
# Adjust the values according to your application.
# See: https://docs.anthropic.com/claude/reference/complete_post
DEFAULT_GENERATION_CONFIG: GenerationParams = {
    # Minimum (Haiku) is 4096
    # Ref: https://docs.anthropic.com/en/docs/about-claude/models/all-models#model-comparison
    "max_tokens": 4096,
    "top_k": 250,
    "top_p": 0.999,
    "temperature": 1.0,
    "stop_sequences": ["Human: ", "Assistant: "],
    # Budget tokens must NOT exceeds max_tokens
    "reasoning_params": {"budget_tokens": 1024},
}

# Ref: https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-deepseek.html
DEFAULT_DEEP_SEEK_GENERATION_CONFIG: GenerationParams = {
    "max_tokens": 4096,
    "top_p": 0.9,
    "temperature": 1.0,
    "stop_sequences": [],
}

# Used for price estimation.
# NOTE: The following is based on 2024-03-07
# See: https://aws.amazon.com/bedrock/pricing/
BEDROCK_PRICING = {
    "us-east-1": {
        "claude-v4-opus": {
            "input": 0.015,
            "output": 0.075,
            "cache_write_input": 0.01875,
            "cache_read_input": 0.0015,
        },
        "claude-v4.1-opus": {
            "input": 0.015,
            "output": 0.075,
            "cache_write_input": 0.01875,
            "cache_read_input": 0.0015,
        },
        "claude-v4-sonnet": {
            "input": 0.003,
            "output": 0.015,
            "cache_write_input": 0.00375,
            "cache_read_input": 0.0003,
        },
        "claude-v3.7-sonnet": {
            "input": 0.00300,
            "output": 0.01500,
            "cache_write_input": 0.00375,
            "cache_read_input": 0.0003,
        },
        "amazon-nova-pro": {
            "input": 0.0008,
            "output": 0.0032,
            "cache_write_input": 0.0008,
            "cache_read_input": 0.0002,
        },
        "amazon-nova-lite": {
            "input": 0.00006,
            "output": 0.00024,
            "cache_write_input": 0.00006,
            "cache_read_input": 0.000015,
        },
        "amazon-nova-micro": {
            "input": 0.000035,
            "output": 0.00014,
            "cache_write_input": 0.000035,
            "cache_read_input": 0.00000875,
        },
        "deepseek-r1": {"input": 0.00135, "output": 0.0054},
    },
    "us-west-2": {
        "claude-v4-opus": {
            "input": 0.015,
            "output": 0.075,
            "cache_write_input": 0.01875,
            "cache_read_input": 0.0015,
        },
        "claude-v4.1-opus": {
            "input": 0.015,
            "output": 0.075,
            "cache_write_input": 0.01875,
            "cache_read_input": 0.0015,
        },
        "claude-v4-sonnet": {
            "input": 0.003,
            "output": 0.015,
            "cache_write_input": 0.00375,
            "cache_read_input": 0.0003,
        },
        "claude-v3.7-sonnet": {
            "input": 0.00300,
            "output": 0.01500,
            "cache_write_input": 0.00375,
            "cache_read_input": 0.0003,
        },
        "claude-v3-opus": {"input": 0.01500, "output": 0.07500},
        "amazon-nova-pro": {
            "input": 0.0008,
            "output": 0.0032,
            "cache_write_input": 0.0008,
            "cache_read_input": 0.0002,
        },
        "amazon-nova-lite": {
            "input": 0.00006,
            "output": 0.00024,
            "cache_write_input": 0.00006,
            "cache_read_input": 0.000015,
        },
        "amazon-nova-micro": {
            "input": 0.000035,
            "output": 0.00014,
            "cache_write_input": 0.000035,
            "cache_read_input": 0.00000875,
        },
        "deepseek-r1": {"input": 0.00135, "output": 0.0054},
    },
    "ap-northeast-1": {},
    "default": {
        "claude-v4-opus": {
            "input": 0.015,
            "output": 0.075,
            "cache_write_input": 0.01875,
            "cache_read_input": 0.0015,
        },
        "claude-v4.1-opus": {
            "input": 0.015,
            "output": 0.075,
            "cache_write_input": 0.01875,
            "cache_read_input": 0.0015,
        },
        "claude-v4-sonnet": {
            "input": 0.003,
            "output": 0.015,
            "cache_write_input": 0.00375,
            "cache_read_input": 0.0003,
        },
        "claude-v3.7-sonnet": {
            "input": 0.00300,
            "output": 0.01500,
            "cache_write_input": 0.00375,
            "cache_read_input": 0.0003,
        },
        "claude-v3-opus": {"input": 0.01500, "output": 0.07500},
        "amazon-nova-pro": {
            "input": 0.0008,
            "output": 0.0032,
            "cache_write_input": 0.0008,
            "cache_read_input": 0.0002,
        },
        "amazon-nova-lite": {
            "input": 0.00006,
            "output": 0.00024,
            "cache_write_input": 0.00006,
            "cache_read_input": 0.000015,
        },
        "amazon-nova-micro": {
            "input": 0.000035,
            "output": 0.00014,
            "cache_write_input": 0.000035,
            "cache_read_input": 0.00000875,
        },
        "deepseek-r1": {"input": 0.00135, "output": 0.0054},
    },
}
