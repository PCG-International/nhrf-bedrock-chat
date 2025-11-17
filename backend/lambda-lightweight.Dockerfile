# syntax=docker/dockerfile:1.4
# Lightweight Dockerfile for Lambda functions that don't need ML dependencies
# These Lambda functions are for Step Functions tasks and DynamoDB Stream handlers
# They only need boto3 and basic AWS SDK, not Docling/PyTorch/CUDA

FROM public.ecr.aws/lambda/python:3.13 AS lambda-base

# Set Python path early
ENV PYTHONPATH=/var/task

# Stage for dependencies (changes less frequently)
FROM lambda-base AS lambda-deps

# Copy and install minimal requirements with cache mount
COPY ./embedding_statemachine/requirements.txt /var/task/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r /var/task/requirements.txt

# Final stage
FROM lambda-deps AS runtime

# Copy only the necessary Python modules
# 1. The embedding_statemachine directory (Lambda handler code)
COPY ./embedding_statemachine /var/task/embedding_statemachine

# 2. The app modules that are imported by the Lambda functions
COPY ./app/__init__.py /var/task/app/
COPY ./app/repositories /var/task/app/repositories
COPY ./app/routes/schemas /var/task/app/routes/schemas
COPY ./app/utils.py /var/task/app/utils.py
COPY ./app/bot_remove.py /var/task/app/bot_remove.py

# The handler will be set by CDK for each Lambda function