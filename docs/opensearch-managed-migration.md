# OpenSearch Managed Migration Tasks

## 1. Discovery & Requirements
- Confirm current OpenSearch Serverless collections, indexes, and OSIS pipelines in `BotStore`.
- Capture existing index templates/analyzers for `bot` and `conversation` indexes (English + non-English variants).
- Document ingestion throughput + query latency expectations to size managed clusters.

## 2. CDK Infrastructure Updates
- Replace `aws-opensearchserverless` constructs with an `aws-opensearchservice` domain supporting at least two data nodes (or document single-AZ tradeoff).
- Recreate encryption/network policies via VPC, security groups, and domain access policies (`es:ESHttp*`).
- Update OSIS IAM policies to allow `es:*` actions and point to the new domain endpoint.
- Remove `serverless: true` flags from OSIS sink configs; add VPC connection settings if the domain is VPC-only.
- Expose the managed domain endpoint via stack outputs and pipeline environment variables.

## 3. Application & IAM Changes
- Update `get_opensearch_client` to sign requests with `service="es"` and adjust host handling.
- Revise ECS/Lambda task roles and developer policies to use `es:ESHttp{Get,Post,Put,Delete}` permissions.
- Audit environment variables (`OPENSEARCH_DOMAIN_ENDPOINT`) and documentation (`backend/README.md`, local `.env` samples).
- Adapt unit/integration tests and fixtures that mock `aoss`-specific behavior.

## 4. Data Migration & Cutover
- Provision staging managed domain and replay DynamoDB → OSIS pipelines to build fresh indexes.
- Validate schema parity (templates, analyzers, mappings) and run sample searches for bots/conversations.
- Plan production cutover: pause writes, snapshot/export Serverless data (if required), re-run pipelines, verify counts.
- Establish rollback strategy (retain Serverless collection until post-cutover validation completes).

## 5. Observability & Cost Controls
- Configure CloudWatch alarms for cluster health, storage utilization, and CPU/memory pressure.
- Set up automated index lifecycle policies (ISM) to manage retention if required.
- Document monthly cost projections (instance hours + EBS) and compare to Serverless baseline.

## 6. Post-Migration Validation
- Run end-to-end regression (bot search, conversation search) in staging then production.
- Monitor OSIS pipeline metrics and ingestion lag for 48 hours post-cutover.
- Decommission Serverless collection only after confirming stability and cost targets.

