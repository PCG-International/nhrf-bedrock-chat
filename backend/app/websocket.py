import json
import logging
import os
import traceback
from datetime import datetime
from decimal import Decimal as decimal
from queue import SimpleQueue
from threading import Thread
from typing import BinaryIO, Literal, TypedDict

import boto3
from app.agents.tools.agent_tool import ToolRunResult
from app.auth import verify_token
from app.repositories.conversation import RecordNotFoundError
from app.routes.schemas.conversation import ChatInput
from app.stream import OnStopInput, OnThinking
from app.usecases.chat import chat
from app.user import User
from boto3.dynamodb.conditions import Attr, Key

WEBSOCKET_SESSION_TABLE_NAME = os.environ["WEBSOCKET_SESSION_TABLE_NAME"]
LARGE_MESSAGE_BUCKET = os.environ.get("LARGE_MESSAGE_BUCKET")
REGION = os.environ.get("REGION", "eu-central-1")

# Threshold for hybrid storage approach
LARGE_PAYLOAD_THRESHOLD = 100 * 1024  # 100KB

dynamodb_client = boto3.resource("dynamodb")
table = dynamodb_client.Table(WEBSOCKET_SESSION_TABLE_NAME)

# S3 client for large message storage
s3_client = boto3.client("s3", region_name=REGION)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class _NotifyCommand(TypedDict):
    type: Literal["notify"]
    payload: bytes | BinaryIO


class _FinishCommand(TypedDict):
    type: Literal["finish"]


_Command = _NotifyCommand | _FinishCommand


class NotificationSender:
    def __init__(self, endpoint_url: str, connection_id: str) -> None:
        self.commands = SimpleQueue[_Command]()
        self.endpoint_url = endpoint_url
        self.connection_id = connection_id

    def run(self):
        import boto3

        gatewayapi = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=self.endpoint_url,
        )

        while True:
            command = self.commands.get()
            if command["type"] == "notify":
                try:
                    gatewayapi.post_to_connection(
                        ConnectionId=self.connection_id,
                        Data=command["payload"],
                    )

                except (
                    gatewayapi.exceptions.GoneException,
                    gatewayapi.exceptions.ForbiddenException,
                ) as e:
                    logger.exception(
                        f"Shutdown the notification sender due to an exception: {e}"
                    )
                    break

                except Exception as e:
                    logger.exception(f"Failed to send notification: {e}")

            elif command["type"] == "finish":
                break

    def finish(self):
        self.commands.put(
            {
                "type": "finish",
            }
        )

    def notify(self, payload: bytes | BinaryIO):
        self.commands.put(
            {
                "type": "notify",
                "payload": payload,
            }
        )

    def on_stream(self, token: str):
        # Send completion
        payload = json.dumps(
            dict(
                status="STREAMING",
                completion=token,
            )
        ).encode("utf-8")

        self.notify(payload=payload)

    def on_stop(self, arg: OnStopInput):
        payload = json.dumps(
            dict(
                status="STREAMING_END",
                completion="",
                stop_reason=arg["stop_reason"],
                token_count=dict(
                    input=arg["input_token_count"],
                    output=arg["output_token_count"],
                    cache_read_input=arg["cache_read_input_count"],
                    cache_write_input=arg["cache_write_input_count"],
                ),
                price=arg["price"],
            )
        ).encode("utf-8")

        self.notify(payload=payload)

    def on_agent_thinking(self, tool_use: OnThinking):
        payload = json.dumps(
            dict(
                status="AGENT_THINKING",
                log={
                    tool_use["tool_use_id"]: {
                        "name": tool_use["name"],
                        "input": tool_use["input"],
                    },
                },
            )
        ).encode("utf-8")

        self.notify(payload=payload)

    def on_agent_tool_result(self, run_result: ToolRunResult):
        self.notify(
            payload=json.dumps(
                dict(
                    status="AGENT_TOOL_RESULT",
                    result={
                        "toolUseId": run_result["tool_use_id"],
                        "status": run_result["status"],
                    },
                )
            ).encode("utf-8")
        )

        for related_document in run_result["related_documents"]:
            self.notify(
                payload=json.dumps(
                    dict(
                        status="AGENT_RELATED_DOCUMENT",
                        result={
                            "toolUseId": run_result["tool_use_id"],
                            "relatedDocument": related_document.to_schema().model_dump(
                                by_alias=True
                            ),
                        },
                    )
                ).encode("utf-8")
            )

    def on_reasoning(self, token: str):
        payload = json.dumps(
            dict(
                status="REASONING",
                completion=token,
            )
        ).encode("utf-8")
        self.notify(payload=payload)


def process_chat_input(
    user: User,
    chat_input: ChatInput,
    notificator: NotificationSender,
) -> dict:
    """Process chat input and send the message to the client."""
    logger.info(f"Received chat input: {chat_input}")

    try:
        chat(
            user=user,
            chat_input=chat_input,
            on_stream=lambda token: notificator.on_stream(
                token=token,
            ),
            on_stop=lambda arg: notificator.on_stop(
                arg=arg,
            ),
            on_thinking=lambda tool_use: notificator.on_agent_thinking(
                tool_use=tool_use,
            ),
            on_tool_result=lambda run_result: notificator.on_agent_tool_result(
                run_result=run_result
            ),
            on_reasoning=lambda token: notificator.on_reasoning(
                token=token,
            ),
        )

        return {"statusCode": 200, "body": "Message sent."}

    except RecordNotFoundError:
        if chat_input.bot_id:
            return {
                "statusCode": 404,
                "body": json.dumps(
                    dict(
                        status="ERROR",
                        reason=f"bot {chat_input.bot_id} not found.",
                    )
                ),
            }
        else:
            return {
                "statusCode": 400,
                "body": json.dumps(
                    dict(
                        status="ERROR",
                        reason="Invalid request.",
                    )
                ),
            }

    except Exception as e:
        logger.exception(f"Failed to run stream handler: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                dict(
                    status="ERROR",
                    reason=f"Failed to run stream handler: {e}",
                )
            ),
        }


def handler(event, context):
    logger.info(f"Received event: {event}")
    route_key = event["requestContext"]["routeKey"]

    if route_key == "$connect":
        return {"statusCode": 200, "body": "Connected."}
    elif route_key == "$disconnect":
        return {"statusCode": 200, "body": "Disconnected."}

    connection_id = event["requestContext"]["connectionId"]
    domain_name = event["requestContext"]["domainName"]
    stage = event["requestContext"]["stage"]
    endpoint_url = f"https://{domain_name}/{stage}"
    notificator = NotificationSender(
        endpoint_url=endpoint_url,
        connection_id=connection_id,
    )

    now = datetime.now()
    expire = int(now.timestamp()) + 60 * 2  # 2 minute from now
    body = json.loads(event["body"])
    step = body.get("step")
    token = body.get("token")

    notification_thread = Thread(
        target=lambda: notificator.run(),
        daemon=True,
    )
    notification_thread.start()
    try:
        # API Gateway (websocket) has hard limit of 32KB per message, so if the message is larger than that,
        # need to concatenate chunks and send as a single full message.
        # To do that, we store the chunks in DynamoDB and when the message is complete, send it to SNS.
        # The life cycle of the message is as follows:
        # 1. Client sends `START` message to the WebSocket API.
        # 2. This handler receives the `Session started` message.
        # 3. Client sends message parts to the WebSocket API.
        # 4. This handler receives the message parts and appends them to the item in DynamoDB with index.
        # 5. Client sends `END` message to the WebSocket API.
        # 6. This handler receives the `END` message, concatenates the parts and sends the message to Bedrock.
        if step == "START":
            try:
                # Verify JWT token
                decoded = verify_token(token)
            except Exception as e:
                logger.exception(f"Invalid token: {e}")
                return {
                    "statusCode": 403,
                    "body": json.dumps(
                        dict(
                            status="ERROR",
                            reason="Invalid token.",
                        )
                    ),
                }

            user_id = decoded["sub"]

            # Store user id
            response = table.put_item(
                Item={
                    "ConnectionId": connection_id,
                    # Store as zero
                    "MessagePartId": decimal(0),
                    "UserId": user_id,
                    "expire": expire,
                }
            )
            return {"statusCode": 200, "body": "Session started."}
        elif step == "END":
            decoded = verify_token(token)
            user = User.from_decoded_token(decoded)

            # Retrieve user id
            response = table.query(
                KeyConditionExpression=Key("ConnectionId").eq(connection_id),
                FilterExpression=Attr("UserId").exists(),
            )
            user_id = response["Items"][0]["UserId"]

            # Concatenate the message parts
            message_parts = []
            last_evaluated_key = None

            while True:
                if last_evaluated_key:
                    response = table.query(
                        KeyConditionExpression=Key("ConnectionId").eq(connection_id)
                        # Zero is reserved for user id, so start from 1
                        & Key("MessagePartId").gte(1),
                        ExclusiveStartKey=last_evaluated_key,
                    )
                else:
                    response = table.query(
                        KeyConditionExpression=Key("ConnectionId").eq(connection_id)
                        & Key("MessagePartId").gte(1),
                    )

                message_parts.extend(response["Items"])

                if "LastEvaluatedKey" in response:
                    last_evaluated_key = response["LastEvaluatedKey"]
                else:
                    break

            logger.info(f"Number of message chunks: {len(message_parts)}")
            message_parts.sort(key=lambda x: x["MessagePartId"])

            # Assemble message from both DynamoDB and S3 sources
            message_text_parts = []
            for item in message_parts:
                if item.get("IsLargeMessage", False) and "S3Key" in item:
                    # Retrieve large message part from S3
                    try:
                        s3_key = item["S3Key"]
                        logger.info(f"Retrieving large message part from S3: {s3_key}")
                        s3_response = s3_client.get_object(
                            Bucket=LARGE_MESSAGE_BUCKET, Key=s3_key
                        )
                        part_content = s3_response["Body"].read().decode("utf-8")
                        message_text_parts.append(part_content)
                    except Exception as e:
                        logger.error(
                            f"Failed to retrieve large message part from S3: {e}"
                        )
                        raise e
                else:
                    # Get regular message part from DynamoDB
                    message_text_parts.append(item["MessagePart"])

            full_message = "".join(message_text_parts)

            # Clean up temporary S3 files for large messages
            s3_keys_to_delete = []
            for item in message_parts:
                if item.get("IsLargeMessage", False) and "S3Key" in item:
                    s3_keys_to_delete.append(item["S3Key"])

            # Process the concatenated full message
            chat_input = ChatInput(**json.loads(full_message))
            result = process_chat_input(
                user=user,
                chat_input=chat_input,
                notificator=notificator,
            )

            # Delete temporary S3 files after successful processing
            if s3_keys_to_delete and LARGE_MESSAGE_BUCKET:
                try:
                    for s3_key in s3_keys_to_delete:
                        s3_client.delete_object(Bucket=LARGE_MESSAGE_BUCKET, Key=s3_key)
                        logger.info(f"Cleaned up temporary S3 file: {s3_key}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup temporary S3 files: {e}")

            return result

        else:
            # Store the message part of full message
            # Zero is reserved for user id, so start from 1
            part_index = body["index"] + 1
            message_part = body["part"]

            # Check if this is a large payload that should be stored in S3
            is_large_payload = body.get("isLargePayload", False)
            total_size = body.get("totalSize", 0)

            if (
                is_large_payload
                and total_size > LARGE_PAYLOAD_THRESHOLD
                and LARGE_MESSAGE_BUCKET
            ):
                # Store large message parts in S3
                s3_key = f"websocket-large/{connection_id}/part-{part_index:05d}.txt"
                try:
                    s3_client.put_object(
                        Bucket=LARGE_MESSAGE_BUCKET,
                        Key=s3_key,
                        Body=message_part.encode("utf-8"),
                        ContentType="text/plain",
                    )
                    # Store S3 reference in DynamoDB instead of content
                    table.put_item(
                        Item={
                            "ConnectionId": connection_id,
                            "MessagePartId": decimal(part_index),
                            "S3Key": s3_key,
                            "IsLargeMessage": True,
                            "TotalSize": decimal(total_size),
                            "expire": expire,
                        }
                    )
                    logger.info(
                        f"Stored large message part {part_index} in S3: {s3_key}"
                    )
                except Exception as e:
                    logger.error(f"Failed to store large message part in S3: {e}")
                    # Fallback to DynamoDB (might still cause throttling, but better than total failure)
                    table.put_item(
                        Item={
                            "ConnectionId": connection_id,
                            "MessagePartId": decimal(part_index),
                            "MessagePart": message_part,
                            "expire": expire,
                        }
                    )
            else:
                # Store small message parts directly in DynamoDB (existing behavior)
                table.put_item(
                    Item={
                        "ConnectionId": connection_id,
                        "MessagePartId": decimal(part_index),
                        "MessagePart": message_part,
                        "expire": expire,
                    }
                )
            return {"statusCode": 200, "body": "Message part received."}

    except Exception as e:
        logger.exception(f"Operation failed: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "status": "ERROR",
                    "reason": str(e),
                }
            ),
        }

    finally:
        notificator.finish()
        notification_thread.join(timeout=60)
