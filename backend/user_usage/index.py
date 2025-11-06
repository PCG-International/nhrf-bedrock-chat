import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import boto3

from app.repositories.usage_analysis import find_users_sorted_by_price

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cloudwatch = boto3.client("cloudwatch")


async def async_handler(event, context):
    logger.info("Event: %s", json.dumps(event))

    limit = 10
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start_time = now - timedelta(days=30)

    start = start_time.strftime("%Y%m%d%H")
    end = now.strftime("%Y%m%d%H")

    users = await find_users_sorted_by_price(limit=limit, from_=start, to_=end)
    logger.info("Got %d users", len(users))

    total_users = len(users)
    total_price = sum(user.total_price for user in users)
    max_price = max((user.total_price for user in users), default=0)
    avg_price = total_price / total_users if total_users else 0

    aggregate_metrics = [
        {"MetricName": "TotalUsers", "Value": total_users, "Unit": "Count"},
        {"MetricName": "TotalPrice", "Value": total_price, "Unit": "None"},
        {"MetricName": "MaxUserPrice", "Value": max_price, "Unit": "None"},
        {"MetricName": "AvgUserPrice", "Value": avg_price, "Unit": "None"},
    ]

    try:
        cloudwatch.put_metric_data(
            Namespace="AIChatbot/Usage", MetricData=aggregate_metrics
        )
        logger.info("Pushed metrics to CloudWatch")
    except Exception as e:
        logger.error("Failed to put metrics: %s", str(e))

    for user in users:
        metric = {
            "MetricName": "UserPrice",
            "Dimensions": [
                {"Name": "UserId", "Value": user.id},
                {"Name": "Email", "Value": user.email},
            ],
            "Timestamp": datetime.now(timezone.utc),
            "Value": user.total_price,
            "Unit": "None",
        }
        try:
            cloudwatch.put_metric_data(
                Namespace="AIChatbot/UserUsage", MetricData=[metric]
            )
        except Exception as e:
            logger.error("Failed to put user metric for %s: %s", user.id, str(e))

    return {"statusCode": 200}


def handler(event, context):
    return asyncio.run(async_handler(event, context))
