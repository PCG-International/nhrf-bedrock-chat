import unittest
from unittest.mock import Mock, patch
import sys
import os

# Add the parent directory to the path to import the app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.stream import ConverseApiStreamHandler
from app.repositories.models.conversation import TextContentModel, MessageModel
from app.routes.schemas.conversation import type_model_name


class TestClaude4InvokeApiStreamHandler(unittest.TestCase):
    """Test Claude 4 invoke API stream handler specifically for cache parameter handling"""

    def setUp(self):
        """Set up test fixtures"""
        self.claude4_model: type_model_name = "claude-v4-sonnet"
        self.message = MessageModel(
            role="user",
            content=[
                TextContentModel(
                    content_type="text",
                    body="Hello, World!",
                )
            ],
            model=self.claude4_model,
            children=[],
            parent=None,
            create_time=0,
            feedback=None,
            used_chunks=None,
            thinking_log=None,
        )

    @patch("app.stream.get_bedrock_runtime_client")
    @patch("app.stream.calculate_price")
    def test_claude4_invoke_api_calls_calculate_price_with_cache_params(
        self, mock_calculate_price, mock_get_client
    ):
        """Test that Claude 4 invoke API calls calculate_price with cache parameters"""
        # Mock the bedrock client and response
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock the streaming response
        mock_response = {
            "body": [
                {
                    "chunk": {
                        "bytes": b'{"type": "message_start", "message": {"usage": {"input_tokens": 100}}}'
                    }
                },
                {
                    "chunk": {
                        "bytes": b'{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}'
                    }
                },
                {
                    "chunk": {
                        "bytes": b'{"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 50}}'
                    }
                },
            ]
        }
        mock_client.invoke_model_with_response_stream.return_value = mock_response

        # Mock calculate_price to return a test value
        mock_calculate_price.return_value = 0.001

        # Create stream handler
        stream_handler = ConverseApiStreamHandler(
            model=self.claude4_model,
            instructions=[],
            generation_params=None,
            guardrail=None,
            on_stream=None,
        )

        # Run the stream handler
        result = stream_handler.run(messages=[self.message])

        # Verify that calculate_price was called with the correct parameters
        mock_calculate_price.assert_called_once_with(
            model=self.claude4_model,
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=0,  # Should be 0 for Claude 4 invoke API
            cache_write_input_tokens=0,  # Should be 0 for Claude 4 invoke API
        )

        # Verify that the result contains the correct cache counts
        self.assertEqual(result["cache_read_input_count"], 0)
        self.assertEqual(result["cache_write_input_count"], 0)
        self.assertEqual(result["input_token_count"], 100)
        self.assertEqual(result["output_token_count"], 50)
        self.assertEqual(result["price"], 0.001)

    @patch("app.stream.is_claude_4_model")
    def test_claude4_model_detection_triggers_invoke_api(self, mock_is_claude_4_model):
        """Test that Claude 4 models trigger the invoke API path"""
        mock_is_claude_4_model.return_value = True

        stream_handler = ConverseApiStreamHandler(
            model=self.claude4_model,
            instructions=[],
            generation_params=None,
            guardrail=None,
            on_stream=None,
        )

        with patch.object(stream_handler, "_run_invoke_api") as mock_invoke_api:
            mock_invoke_api.return_value = {
                "message": Mock(),
                "stop_reason": "end_turn",
                "input_token_count": 100,
                "output_token_count": 50,
                "cache_read_input_count": 0,
                "cache_write_input_count": 0,
                "price": 0.001,
            }

            result = stream_handler.run(messages=[self.message])

            # Verify that _run_invoke_api was called
            mock_invoke_api.assert_called_once()

            # Verify that the result has cache counts
            self.assertEqual(result["cache_read_input_count"], 0)
            self.assertEqual(result["cache_write_input_count"], 0)


if __name__ == "__main__":
    unittest.main()
