"""WebSocket route for streaming chat responses (ECS implementation)."""

import json
import logging
from typing import Annotated

from app.auth import verify_token
from app.repositories.conversation import RecordNotFoundError
from app.routes.schemas.conversation import ChatInput
from app.usecases.chat import chat
from app.user import User
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

router = APIRouter()
logger = logging.getLogger(__name__)


class WebSocketNotificationSender:
    """
    Notification sender for FastAPI WebSocket.
    Replaces Lambda's API Gateway Management API with native WebSocket send.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def send(self, data: dict):
        """Send JSON data to WebSocket client."""
        try:
            await self.websocket.send_json(data)
        except Exception as e:
            logger.error(f"Failed to send WebSocket message: {e}")
            raise

    async def on_stream(self, token: str):
        """Send streaming token to client."""
        await self.send(
            {
                "status": "STREAMING",
                "completion": token,
            }
        )

    async def on_stop(self, arg):
        """Send completion status to client."""
        await self.send(
            {
                "status": "STREAMING_END",
                "completion": "",
                "stop_reason": arg["stop_reason"],
                "token_count": {
                    "input": arg["input_token_count"],
                    "output": arg["output_token_count"],
                    "cache_read_input": arg["cache_read_input_count"],
                    "cache_write_input": arg["cache_write_input_count"],
                },
                "price": arg["price"],
            }
        )

    async def on_agent_thinking(self, tool_use):
        """Send agent thinking status to client."""
        await self.send(
            {
                "status": "AGENT_THINKING",
                "log": {
                    tool_use["tool_use_id"]: {
                        "name": tool_use["name"],
                        "input": tool_use["input"],
                    },
                },
            }
        )

    async def on_agent_tool_result(self, run_result):
        """Send agent tool result to client."""
        await self.send(
            {
                "status": "AGENT_TOOL_RESULT",
                "result": {
                    "toolUseId": run_result["tool_use_id"],
                    "status": run_result["status"],
                },
            }
        )

        # Send related documents
        for related_document in run_result["related_documents"]:
            await self.send(
                {
                    "status": "AGENT_RELATED_DOCUMENT",
                    "result": {
                        "toolUseId": run_result["tool_use_id"],
                        "relatedDocument": related_document.to_schema().model_dump(
                            by_alias=True
                        ),
                    },
                }
            )

    async def on_reasoning(self, token: str):
        """Send reasoning token to client."""
        await self.send(
            {
                "status": "REASONING",
                "completion": token,
            }
        )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for streaming chat responses.

    Protocol:
    1. Client connects and sends: {"step": "START", "token": "<jwt>"}
    2. Server validates token and responds: {"status": "CONNECTED"}
    3. Client sends chat request: {"step": "MESSAGE", "data": <ChatInput>}
    4. Server streams responses with various status messages
    5. Client can disconnect anytime
    """
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    user = None

    try:
        # Wait for START message with authentication token
        start_message = await websocket.receive_json()
        logger.info(f"Received WebSocket message: {start_message}")

        if start_message.get("step") != "START":
            await websocket.send_json(
                {
                    "status": "ERROR",
                    "reason": "Expected START message with token",
                }
            )
            await websocket.close()
            return

        token = start_message.get("token")
        if not token:
            await websocket.send_json(
                {
                    "status": "ERROR",
                    "reason": "Token required",
                }
            )
            await websocket.close()
            return

        # Verify JWT token
        try:
            decoded = verify_token(token)
            user = User.from_decoded_token(decoded)
            logger.info(f"User authenticated: {user.id}")
        except Exception as e:
            logger.exception(f"Invalid token: {e}")
            await websocket.send_json(
                {
                    "status": "ERROR",
                    "reason": "Invalid token",
                }
            )
            await websocket.close()
            return

        # Send connection success (plain text, not JSON - matches Lambda protocol)
        await websocket.send_text("Session started.")

        # Store message chunks (for chunked protocol support)
        message_parts = {}

        # Main message loop
        while True:
            # Receive message from client
            message = await websocket.receive_json()
            logger.info(f"Received message: {message}")

            step = message.get("step")

            if step == "BODY":
                # Store message chunk
                index = message.get("index", 0)
                part = message.get("part", "")
                message_parts[index] = part

                # Acknowledge receipt
                await websocket.send_text("Message part received.")
                continue

            elif step == "END":
                # Verify token again (security - ensure same user)
                end_token = message.get("token")
                if end_token:
                    try:
                        decoded = verify_token(end_token)
                        end_user = User.from_decoded_token(decoded)
                        if end_user.id != user.id:
                            await websocket.send_json(
                                {
                                    "status": "ERROR",
                                    "reason": "Token mismatch",
                                }
                            )
                            continue
                    except Exception as e:
                        logger.exception(f"Invalid token on END: {e}")
                        await websocket.send_json(
                            {
                                "status": "ERROR",
                                "reason": "Invalid token",
                            }
                        )
                        continue

                # Reassemble message from chunks
                if not message_parts:
                    await websocket.send_json(
                        {
                            "status": "ERROR",
                            "reason": "No message parts received",
                        }
                    )
                    continue

                # Sort by index and concatenate
                sorted_parts = [message_parts[i] for i in sorted(message_parts.keys())]
                full_message = "".join(sorted_parts)

                # Parse the concatenated message as JSON
                try:
                    full_chat_data = json.loads(full_message)
                except json.JSONDecodeError as e:
                    logger.exception(f"Invalid JSON in concatenated message: {e}")
                    await websocket.send_json(
                        {
                            "status": "ERROR",
                            "reason": "Invalid JSON in message",
                        }
                    )
                    continue

                # Clear chunks for next message
                message_parts = {}

                # Process chat input
                try:
                    # Remove token from chat data (it's just for auth)
                    if "token" in full_chat_data:
                        del full_chat_data["token"]

                    chat_input = ChatInput(**full_chat_data)
                    notificator = WebSocketNotificationSender(websocket)

                    logger.info(f"Processing chat input: {chat_input}")

                    # Process chat with streaming callbacks
                    await process_chat_streaming(user, chat_input, notificator)

                    # Send completion message (matches Lambda protocol)
                    await websocket.send_text("Message sent.")

                except ValidationError as e:
                    logger.exception(f"Invalid chat input: {e}")
                    await websocket.send_json(
                        {
                            "status": "ERROR",
                            "reason": f"Invalid chat input: {e}",
                        }
                    )

                except RecordNotFoundError as e:
                    logger.exception(f"Record not found: {e}")
                    bot_id = full_chat_data.get("botId")
                    reason = (
                        f"bot {bot_id} not found"
                        if bot_id
                        else "Conversation not found"
                    )
                    await websocket.send_json(
                        {
                            "status": "ERROR",
                            "reason": reason,
                        }
                    )

                except Exception as e:
                    logger.exception(f"Error processing chat: {e}")
                    await websocket.send_json(
                        {
                            "status": "ERROR",
                            "reason": f"Failed to process chat: {str(e)}",
                        }
                    )

            elif step == "DISCONNECT":
                logger.info("Client requested disconnect")
                break

            else:
                await websocket.send_json(
                    {
                        "status": "ERROR",
                        "reason": f"Unknown step: {step}",
                    }
                )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        try:
            await websocket.send_json(
                {
                    "status": "ERROR",
                    "reason": f"Server error: {str(e)}",
                }
            )
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass
        logger.info("WebSocket connection closed")


async def process_chat_streaming(
    user: User, chat_input: ChatInput, notificator: WebSocketNotificationSender
):
    """
    Process chat input with streaming responses.

    Note: The chat() function is synchronous but we need async callbacks.
    We use run_coroutine_threadsafe to schedule async callbacks from the
    sync thread back to the main event loop.
    """
    import asyncio

    # Get the current event loop (main async context)
    loop = asyncio.get_running_loop()

    # Create sync wrappers that schedule async callbacks on the main loop
    def sync_on_stream(token: str):
        asyncio.run_coroutine_threadsafe(notificator.on_stream(token), loop)

    def sync_on_stop(arg):
        asyncio.run_coroutine_threadsafe(notificator.on_stop(arg), loop)

    def sync_on_thinking(tool_use):
        asyncio.run_coroutine_threadsafe(notificator.on_agent_thinking(tool_use), loop)

    def sync_on_tool_result(run_result):
        asyncio.run_coroutine_threadsafe(
            notificator.on_agent_tool_result(run_result), loop
        )

    def sync_on_reasoning(token: str):
        asyncio.run_coroutine_threadsafe(notificator.on_reasoning(token), loop)

    # Run chat in thread pool to avoid blocking the event loop
    await loop.run_in_executor(
        None,
        lambda: chat(
            user=user,
            chat_input=chat_input,
            on_stream=sync_on_stream,
            on_stop=sync_on_stop,
            on_thinking=sync_on_thinking,
            on_tool_result=sync_on_tool_result,
            on_reasoning=sync_on_reasoning,
        ),
    )
