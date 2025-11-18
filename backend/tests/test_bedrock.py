import os
import sys

os.environ["REGION"] = "us-west-2"
os.environ["BEDROCK_REGION"] = "us-west-2"
os.environ["ENABLE_BEDROCK_CROSS_REGION_INFERENCE"] = "true"

sys.path.append(".")

import unittest
from unittest.mock import MagicMock, patch

from app.bedrock import call_converse_api, compose_args_for_converse_api, get_model_id
from app.repositories.models.conversation import SimpleMessageModel, TextContentModel
from app.repositories.models.custom_bot_guardrails import BedrockGuardrailsModel
from app.routes.schemas.conversation import type_model_name

# MODEL: type_model_name = "claude-v3-haiku"
MODEL: type_model_name = "claude-v3.7-sonnet"


class TestGetModelId(unittest.TestCase):
    def test_get_model_id_with_cross_region_supported_model(self):
        model = "claude-v3.7-sonnet"
        # Prefix with "us." to enable cross-region
        expected_model_id = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
        self.assertEqual(
            get_model_id(model, enable_cross_region=True, bedrock_region="us-east-1"),
            expected_model_id,
        )

    def test_get_model_id_without_cross_region(self):
        model = "claude-v3.7-sonnet"
        # No prefix to disable cross-region
        expected_model_id = "anthropic.claude-3-7-sonnet-20250219-v1:0"
        self.assertEqual(
            get_model_id(model, enable_cross_region=False, bedrock_region="us-east-1"),
            expected_model_id,
        )

    def test_get_model_id_with_unsupported_region_for_cross_region(self):
        model = "claude-v3.7-sonnet"
        # Cross region is disabled because the region is not supported
        expected_model_id = "anthropic.claude-3-7-sonnet-20250219-v1:0"
        self.assertEqual(
            get_model_id(
                model, enable_cross_region=True, bedrock_region="ap-northeast-1"
            ),
            expected_model_id,
        )


class TestCallConverseApi(unittest.TestCase):
    @patch("app.bedrock.get_bedrock_runtime_client")
    def test_call_converse_api(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "Test response"}]}}
        }

        message = SimpleMessageModel(
            role="user",
            content=[
                TextContentModel(
                    content_type="text",
                    body="Hello, World!",
                )
            ],
        )
        arg = compose_args_for_converse_api(
            [message],
            MODEL,
            stream=False,
        )

        response = call_converse_api(arg)
        self.assertEqual(response, mock_client.converse.return_value)
        mock_client.converse.assert_called_once_with(**arg)


class TestCallConverseApiWithGuardrails(unittest.TestCase):
    def setUp(self):
        self.get_client_patcher = patch("app.bedrock.get_bedrock_runtime_client")
        self.mock_get_client = self.get_client_patcher.start()
        self.addCleanup(self.get_client_patcher.stop)

        self.bedrock_client = MagicMock()
        self.mock_get_client.return_value = self.bedrock_client
        self.bedrock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "Guardrail response"}]}}
        }

        self.guardrail = BedrockGuardrailsModel(
            is_guardrail_enabled=True,
            hate_threshold=0,
            insults_threshold=0,
            sexual_threshold=1,
            violence_threshold=0,
            misconduct_threshold=0,
            grounding_threshold=0,
            relevance_threshold=0,
            guardrail_arn="test-arn",
            guardrail_version="1",
        )

    def test_call_converse_api_with_guardrails(self):
        message = SimpleMessageModel(
            role="user",
            content=[
                TextContentModel(
                    content_type="text",
                    body="Hello, World!",
                )
            ],
        )
        arg = compose_args_for_converse_api(
            [message],
            MODEL,
            guardrail=self.guardrail,
            stream=False,
        )

        response = call_converse_api(arg)
        self.assertEqual(response, self.bedrock_client.converse.return_value)
        self.bedrock_client.converse.assert_called_once_with(**arg)


if __name__ == "__main__":
    unittest.main()
