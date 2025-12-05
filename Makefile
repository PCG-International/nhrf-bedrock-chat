# Makefile for NHRF Bedrock Chat
# Provides commands for formatting, linting, testing, and deployment

.PHONY: help install format lint test build deploy clean docker-build docker-run

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

# Default target
.DEFAULT_GOAL := help

##@ Help

help: ## Display this help message
	@echo "$(BLUE)NHRF Bedrock Chat - Development Commands$(NC)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make $(GREEN)<target>$(NC)\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2 } /^##@/ { printf "\n$(BLUE)%s$(NC)\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Setup

install: install-backend install-frontend install-cdk ## Install all dependencies
	@echo "$(GREEN)✓ All dependencies installed$(NC)"

install-backend: ## Install backend (Python/Poetry) dependencies
	@echo "$(BLUE)Installing backend dependencies...$(NC)"
	@cd backend && \
		python3 -m venv .venv 2>/dev/null || true && \
		. .venv/bin/activate && \
		pip install poetry && \
		poetry install
	@echo "$(GREEN)✓ Backend dependencies installed$(NC)"

install-frontend: ## Install frontend (React/TypeScript) dependencies
	@echo "$(BLUE)Installing frontend dependencies...$(NC)"
	@cd frontend && npm ci
	@echo "$(GREEN)✓ Frontend dependencies installed$(NC)"

install-cdk: ## Install CDK dependencies
	@echo "$(BLUE)Installing CDK dependencies...$(NC)"
	@cd cdk && npm ci
	@echo "$(GREEN)✓ CDK dependencies installed$(NC)"

##@ Formatting

format: format-backend format-frontend format-cdk ## Format all code
	@echo "$(GREEN)✓ All code formatted$(NC)"

format-backend: ## Format backend Python code with Black
	@echo "$(BLUE)Formatting backend code...$(NC)"
	@cd backend && \
		. .venv/bin/activate && \
		poetry run black app/ tests/ embedding_statemachine/ auth/ s3_exporter/ user_usage/
	@echo "$(GREEN)✓ Backend code formatted$(NC)"

format-frontend: ## Format frontend TypeScript code with Prettier
	@echo "$(BLUE)Formatting frontend code...$(NC)"
	@cd frontend && npx prettier --write "src/**/*.{ts,tsx,js,jsx,json,css,md}"
	@echo "$(GREEN)✓ Frontend code formatted$(NC)"

format-cdk: ## Format CDK TypeScript code with Prettier
	@echo "$(BLUE)Formatting CDK code...$(NC)"
	@cd cdk && npx prettier --write "lib/**/*.ts" "bin/**/*.ts" "test/**/*.ts"
	@echo "$(GREEN)✓ CDK code formatted$(NC)"

format-check: format-check-backend format-check-frontend format-check-cdk ## Check formatting without making changes

format-check-backend: ## Check backend formatting
	@echo "$(BLUE)Checking backend formatting...$(NC)"
	@cd backend && \
		. .venv/bin/activate && \
		poetry run black --check app/ tests/ embedding_statemachine/ auth/ s3_exporter/ user_usage/

format-check-frontend: ## Check frontend formatting
	@echo "$(BLUE)Checking frontend formatting...$(NC)"
	@cd frontend && npx prettier --check "src/**/*.{ts,tsx,js,jsx,json,css,md}"

format-check-cdk: ## Check CDK formatting
	@echo "$(BLUE)Checking CDK formatting...$(NC)"
	@cd cdk && npx prettier --check "lib/**/*.ts" "bin/**/*.ts" "test/**/*.ts"

##@ Linting

lint: lint-backend lint-frontend lint-cdk ## Lint all code
	@echo "$(GREEN)✓ All code linted$(NC)"

lint-backend: ## Lint backend code with mypy
	@echo "$(BLUE)Linting backend code...$(NC)"
	@cd backend && \
		. .venv/bin/activate && \
		poetry run mypy --config-file mypy.ini .
	@echo "$(GREEN)✓ Backend linting passed$(NC)"

lint-frontend: ## Lint frontend code with ESLint
	@echo "$(BLUE)Linting frontend code...$(NC)"
	@cd frontend && npm run lint
	@echo "$(GREEN)✓ Frontend linting passed$(NC)"

lint-cdk: ## Lint CDK code with TypeScript compiler
	@echo "$(BLUE)Linting CDK code...$(NC)"
	@cd cdk && npm run build
	@echo "$(GREEN)✓ CDK linting passed$(NC)"

lint-fix: lint-fix-frontend ## Auto-fix linting issues where possible

lint-fix-frontend: ## Auto-fix frontend ESLint issues
	@echo "$(BLUE)Auto-fixing frontend linting issues...$(NC)"
	@cd frontend && npx eslint . --ext ts,tsx --fix
	@echo "$(GREEN)✓ Frontend linting issues fixed$(NC)"

##@ Testing

test: test-backend test-frontend test-cdk ## Run all tests
	@echo "$(GREEN)✓ All tests passed$(NC)"

test-backend: ## Run backend tests
	@echo "$(BLUE)Running backend tests...$(NC)"
	@cd backend && \
		. .venv/bin/activate && \
		python tests/test_bedrock.py && \
		python tests/test_repositories/test_conversation.py
	@echo "$(GREEN)✓ Backend tests passed$(NC)"

test-frontend: ## Run frontend tests
	@echo "$(BLUE)Running frontend tests...$(NC)"
	@cd frontend && npm test
	@echo "$(GREEN)✓ Frontend tests passed$(NC)"

test-cdk: ## Run CDK tests
	@echo "$(BLUE)Running CDK tests...$(NC)"
	@cd cdk && npm test
	@echo "$(GREEN)✓ CDK tests passed$(NC)"

test-coverage-backend: ## Run backend tests with coverage
	@echo "$(BLUE)Running backend tests with coverage...$(NC)"
	@cd backend && \
		. .venv/bin/activate && \
		poetry run pytest tests/ --cov=app --cov-report=html --cov-report=term
	@echo "$(GREEN)✓ Coverage report generated at backend/htmlcov/index.html$(NC)"

##@ Building

build: build-frontend build-cdk ## Build all components
	@echo "$(GREEN)✓ All components built$(NC)"

build-frontend: ## Build frontend for production
	@echo "$(BLUE)Building frontend...$(NC)"
	@cd frontend && npm run build
	@echo "$(GREEN)✓ Frontend built$(NC)"

build-cdk: ## Build CDK TypeScript
	@echo "$(BLUE)Building CDK...$(NC)"
	@cd cdk && npm run build
	@echo "$(GREEN)✓ CDK built$(NC)"

##@ Docker

docker-build: ## Build backend Docker image
	@echo "$(BLUE)Building Docker image...$(NC)"
	@cd backend && docker build -t nhrf-bedrock-chat-backend:latest .
	@echo "$(GREEN)✓ Docker image built: nhrf-bedrock-chat-backend:latest$(NC)"

docker-run: ## Run backend in Docker container
	@echo "$(BLUE)Running Docker container...$(NC)"
	@docker run -p 8000:8000 --env-file backend/.env nhrf-bedrock-chat-backend:latest

docker-test: ## Test Docker build
	@echo "$(BLUE)Testing Docker build...$(NC)"
	@cd backend && docker build -t nhrf-bedrock-chat-backend:test .
	@docker run --rm nhrf-bedrock-chat-backend:test python -c "import app.main; print('✓ Docker build OK')"
	@echo "$(GREEN)✓ Docker build test passed$(NC)"

##@ Development

dev-backend: ## Run backend development server
	@echo "$(BLUE)Starting backend development server...$(NC)"
	@cd backend && \
		. .venv/bin/activate && \
		env $$(cat .env | xargs) poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Run frontend development server
	@echo "$(BLUE)Starting frontend development server...$(NC)"
	@cd frontend && npm run dev

dev: ## Run both backend and frontend dev servers (requires tmux)
	@echo "$(BLUE)Starting development environment...$(NC)"
	@tmux new-session -d -s bedrock-chat "cd backend && . .venv/bin/activate && env \$$(cat .env | xargs) poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
	@tmux split-window -h -t bedrock-chat "cd frontend && npm run dev"
	@tmux attach -t bedrock-chat

##@ Deployment (V3 - Lambda)

deploy-v3: ## Deploy v3 (Lambda-based architecture)
	@echo "$(BLUE)Deploying v3 stack (Lambda)...$(NC)"
	@cd cdk && npx cdk deploy --all --require-approval never
	@echo "$(GREEN)✓ v3 stack deployed$(NC)"

deploy-v3-with-approval: ## Deploy v3 with manual approval
	@echo "$(BLUE)Deploying v3 stack (Lambda) with approval...$(NC)"
	@cd cdk && npx cdk deploy --all
	@echo "$(GREEN)✓ v3 stack deployed$(NC)"

diff-v3: ## Show what would change in v3 deployment
	@echo "$(BLUE)Showing v3 deployment diff...$(NC)"
	@cd cdk && npx cdk diff --all

synth-v3: ## Synthesize v3 CloudFormation template
	@echo "$(BLUE)Synthesizing v3 CloudFormation template...$(NC)"
	@cd cdk && npx cdk synth --all

destroy-v3: ## Destroy v3 stack (WARNING: Deletes resources!)
	@echo "$(RED)WARNING: This will destroy all v3 resources!$(NC)"
	@echo "Press Ctrl+C to cancel, or Enter to continue..."
	@read
	@cd cdk && npx cdk destroy --all --force
	@echo "$(YELLOW)⚠ v3 stack destroyed$(NC)"

##@ Deployment (V4 - ECS Fargate)

deploy-v4: ## Deploy v4 (ECS Fargate architecture) - uses git SHA as image tag
	@echo "$(BLUE)Deploying v4 stack (ECS Fargate)...$(NC)"
	@echo "$(YELLOW)Enabling Docker BuildKit for better caching...$(NC)"
	@IMAGE_TAG=$$(git rev-parse HEAD) && \
		echo "$(YELLOW)Using image tag: $$IMAGE_TAG$(NC)" && \
		export DOCKER_BUILDKIT=1 && \
		export BUILDKIT_PROGRESS=plain && \
		cd cdk && npx cdk deploy --all -c envName=v4 -c imageTag=$$IMAGE_TAG --require-approval never
	@echo "$(GREEN)✓ v4 stack deployed$(NC)"

deploy-v4-with-approval: ## Deploy v4 with manual approval
	@echo "$(BLUE)Deploying v4 stack (ECS Fargate) with approval...$(NC)"
	@echo "$(YELLOW)Enabling Docker BuildKit for better caching...$(NC)"
	@IMAGE_TAG=$$(git rev-parse HEAD) && \
		echo "$(YELLOW)Using image tag: $$IMAGE_TAG$(NC)" && \
		export DOCKER_BUILDKIT=1 && \
		export BUILDKIT_PROGRESS=plain && \
		cd cdk && npx cdk deploy --all -c envName=v4 -c imageTag=$$IMAGE_TAG
	@echo "$(GREEN)✓ v4 stack deployed$(NC)"

diff-v4: ## Show what would change in v4 deployment
	@echo "$(BLUE)Showing v4 deployment diff...$(NC)"
	@IMAGE_TAG=$$(git rev-parse HEAD) && \
		cd cdk && npx cdk diff --all -c envName=v4 -c imageTag=$$IMAGE_TAG

synth-v4: ## Synthesize v4 CloudFormation template
	@echo "$(BLUE)Synthesizing v4 CloudFormation template...$(NC)"
	@IMAGE_TAG=$$(git rev-parse HEAD) && \
		cd cdk && npx cdk synth --all -c envName=v4 -c imageTag=$$IMAGE_TAG

destroy-v4: ## Destroy v4 stack (WARNING: Deletes resources!)
	@echo "$(RED)WARNING: This will destroy all v4 resources!$(NC)"
	@echo "Press Ctrl+C to cancel, or Enter to continue..."
	@read
	@cd cdk && npx cdk destroy --all -c envName=v4 --force
	@echo "$(YELLOW)⚠ v4 stack destroyed$(NC)"

##@ CDK Operations

cdk-bootstrap: ## Bootstrap CDK (first time only)
	@echo "$(BLUE)Bootstrapping CDK...$(NC)"
	@cd cdk && npx cdk bootstrap
	@echo "$(GREEN)✓ CDK bootstrapped$(NC)"

cdk-list: ## List all CDK stacks
	@echo "$(BLUE)Listing CDK stacks...$(NC)"
	@cd cdk && npx cdk list --all

cdk-list-v4: ## List v4 CDK stacks
	@echo "$(BLUE)Listing v4 CDK stacks...$(NC)"
	@cd cdk && npx cdk list --all -c envName=v4

cdk-doctor: ## Run CDK doctor to check for issues
	@echo "$(BLUE)Running CDK doctor...$(NC)"
	@cd cdk && npx cdk doctor

##@ Docker Cache Optimization

docker-build-backend: ## Pre-build backend Docker image with caching
	@echo "$(BLUE)Pre-building backend Docker image with BuildKit caching...$(NC)"
	@cd backend && \
		DOCKER_BUILDKIT=1 docker build \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		-t backend:cached \
		-f Dockerfile .
	@echo "$(GREEN)✓ Backend image cached locally$(NC)"

docker-build-lambda: ## Pre-build Lambda Docker image with caching
	@echo "$(BLUE)Pre-building Lambda Docker image with BuildKit caching...$(NC)"
	@cd backend && \
		DOCKER_BUILDKIT=1 docker build \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		-t lambda:cached \
		-f lambda-lightweight.Dockerfile .
	@echo "$(GREEN)✓ Lambda image cached locally$(NC)"

cache-warm: docker-build-backend ## Warm up Docker build cache for ECS backend
	@echo "$(GREEN)✓ Docker cache warmed up$(NC)"

cache-warm-all: docker-build-backend docker-build-lambda ## Warm up all Docker caches (including Lambda)

##@ ECR Image Management (V4)

ECR_REGION := eu-central-1
AWS_ACCOUNT_ID := $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "000000000000")
ECR_PREFIX := v4-bedrock-chat

ecr-login: ## Login to ECR registry
	@echo "$(BLUE)Logging into ECR...$(NC)"
	@aws ecr get-login-password --region $(ECR_REGION) | \
		docker login --username AWS --password-stdin $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com
	@echo "$(GREEN)✓ Logged into ECR$(NC)"

ecr-build-backend: ## Build backend Docker image for ECR
	@echo "$(BLUE)Building ECS backend image for ECR...$(NC)"
	@cd backend && \
		DOCKER_BUILDKIT=1 docker build \
		--platform linux/amd64 \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-backend:latest \
		--tag $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-backend:latest \
		--tag $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-backend:$$(git rev-parse --short HEAD) \
		-f Dockerfile .
	@echo "$(GREEN)✓ Backend image built$(NC)"

ecr-build-lambda-lightweight: ## Build Lambda lightweight Docker image for ECR
	@echo "$(BLUE)Building Lambda lightweight image for ECR...$(NC)"
	@cd backend && \
		DOCKER_BUILDKIT=1 docker build \
		--platform linux/amd64 \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-lightweight:latest \
		--tag $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-lightweight:latest \
		--tag $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-lightweight:$$(git rev-parse --short HEAD) \
		-f lambda-lightweight.Dockerfile .
	@echo "$(GREEN)✓ Lambda lightweight image built$(NC)"

ecr-build-lambda-full: ## Build Lambda full Docker image for ECR
	@echo "$(BLUE)Building Lambda full image for ECR...$(NC)"
	@cd backend && \
		DOCKER_BUILDKIT=1 docker build \
		--platform linux/amd64 \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-full:latest \
		--tag $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-full:latest \
		--tag $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-full:$$(git rev-parse --short HEAD) \
		-f lambda.Dockerfile .
	@echo "$(GREEN)✓ Lambda full image built$(NC)"

ecr-build-all: ecr-build-backend ecr-build-lambda-lightweight ecr-build-lambda-full ## Build all Docker images for ECR
	@echo "$(GREEN)✓ All images built$(NC)"

ecr-push-backend: ecr-login ## Push backend image to ECR
	@echo "$(BLUE)Pushing backend image to ECR...$(NC)"
	@docker push $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-backend:latest
	@docker push $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-backend:$$(git rev-parse --short HEAD)
	@echo "$(GREEN)✓ Backend image pushed$(NC)"

ecr-push-lambda-lightweight: ecr-login ## Push Lambda lightweight image to ECR
	@echo "$(BLUE)Pushing Lambda lightweight image to ECR...$(NC)"
	@docker push $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-lightweight:latest
	@docker push $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-lightweight:$$(git rev-parse --short HEAD)
	@echo "$(GREEN)✓ Lambda lightweight image pushed$(NC)"

ecr-push-lambda-full: ecr-login ## Push Lambda full image to ECR
	@echo "$(BLUE)Pushing Lambda full image to ECR...$(NC)"
	@docker push $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-full:latest
	@docker push $(AWS_ACCOUNT_ID).dkr.ecr.$(ECR_REGION).amazonaws.com/$(ECR_PREFIX)-lambda-full:$$(git rev-parse --short HEAD)
	@echo "$(GREEN)✓ Lambda full image pushed$(NC)"

ecr-push-all: ecr-push-backend ecr-push-lambda-lightweight ecr-push-lambda-full ## Push all images to ECR
	@echo "$(GREEN)✓ All images pushed to ECR$(NC)"

ecr-build-and-push-all: ecr-build-all ecr-push-all ## Build and push all images to ECR
	@echo "$(GREEN)✓ All images built and pushed to ECR$(NC)"

ecr-list-images: ## List images in ECR repositories
	@echo "$(BLUE)Backend images:$(NC)"
	@aws ecr describe-images \
		--repository-name $(ECR_PREFIX)-backend \
		--region $(ECR_REGION) \
		--query 'sort_by(imageDetails,& imagePushedAt)[-5:].[imageTags[0], imagePushedAt, imageSizeInBytes]' \
		--output table 2>/dev/null || echo "Repository not found or no images"
	@echo ""
	@echo "$(BLUE)Lambda lightweight images:$(NC)"
	@aws ecr describe-images \
		--repository-name $(ECR_PREFIX)-lambda-lightweight \
		--region $(ECR_REGION) \
		--query 'sort_by(imageDetails,& imagePushedAt)[-5:].[imageTags[0], imagePushedAt, imageSizeInBytes]' \
		--output table 2>/dev/null || echo "Repository not found or no images"
	@echo ""
	@echo "$(BLUE)Lambda full images:$(NC)"
	@aws ecr describe-images \
		--repository-name $(ECR_PREFIX)-lambda-full \
		--region $(ECR_REGION) \
		--query 'sort_by(imageDetails,& imagePushedAt)[-5:].[imageTags[0], imagePushedAt, imageSizeInBytes]' \
		--output table 2>/dev/null || echo "Repository not found or no images"

# Fast deployment targets using ECR
deploy-v4-ecr: ecr-build-and-push-all ## Build, push to ECR, then deploy v4
	@echo "$(BLUE)Deploying v4 with ECR images...$(NC)"
	@cd cdk && npx cdk deploy --all -c envName=v4 --require-approval never
	@echo "$(GREEN)✓ V4 deployed with ECR images$(NC)"

fast-v4: ecr-push-backend deploy-v4 ## Fast v4 deployment (push backend only, then deploy)
	@echo "$(GREEN)✓ Fast V4 deployment complete$(NC)"

##@ AWS Operations

aws-check-ecs: ## Check ECS service status (v4)
	@echo "$(BLUE)Checking ECS service status...$(NC)"
	@aws ecs describe-services \
		--cluster v4-backend-cluster \
		--services v4-backend-service \
		--region eu-central-1 \
		--query 'services[0].[serviceName,status,runningCount,desiredCount,healthCheckGracePeriodSeconds]' \
		--output table

aws-logs-ecs: ## Tail ECS logs (v4)
	@echo "$(BLUE)Tailing ECS logs...$(NC)"
	@aws logs tail /aws/ecs/backend --follow --region eu-central-1

aws-check-alb: ## Check ALB target health (v4)
	@echo "$(BLUE)Checking ALB target health...$(NC)"
	@aws elbv2 describe-load-balancers \
		--names v4-backend-alb \
		--region eu-central-1 \
		--query 'LoadBalancers[0].[LoadBalancerName,State.Code,DNSName]' \
		--output table

##@ Quality Checks

check: format-check lint test ## Run all quality checks (format, lint, test)
	@echo "$(GREEN)✓ All quality checks passed$(NC)"

pre-commit: format lint ## Run pre-commit checks (format + lint)
	@echo "$(GREEN)✓ Pre-commit checks passed$(NC)"

ci: check build ## Run CI checks (format, lint, test, build)
	@echo "$(GREEN)✓ CI checks passed$(NC)"

##@ Utilities

clean: ## Clean build artifacts and caches
	@echo "$(BLUE)Cleaning build artifacts...$(NC)"
	@rm -rf backend/.venv
	@rm -rf backend/__pycache__
	@rm -rf backend/.mypy_cache
	@rm -rf backend/.pytest_cache
	@rm -rf backend/htmlcov
	@rm -rf backend/.coverage
	@rm -rf frontend/node_modules
	@rm -rf frontend/dist
	@rm -rf frontend/dev-dist
	@rm -rf cdk/node_modules
	@rm -rf cdk/cdk.out
	@rm -rf cdk/dist
	@echo "$(GREEN)✓ Build artifacts cleaned$(NC)"

clean-docker: ## Remove Docker images
	@echo "$(BLUE)Removing Docker images...$(NC)"
	@docker rmi nhrf-bedrock-chat-backend:latest 2>/dev/null || true
	@docker rmi nhrf-bedrock-chat-backend:test 2>/dev/null || true
	@echo "$(GREEN)✓ Docker images removed$(NC)"

update-deps: ## Update all dependencies
	@echo "$(BLUE)Updating backend dependencies...$(NC)"
	@cd backend && . .venv/bin/activate && poetry update
	@echo "$(BLUE)Updating frontend dependencies...$(NC)"
	@cd frontend && npm update
	@echo "$(BLUE)Updating CDK dependencies...$(NC)"
	@cd cdk && npm update
	@echo "$(GREEN)✓ All dependencies updated$(NC)"

status: ## Show git status and branch info
	@echo "$(BLUE)Git Status:$(NC)"
	@git status -s
	@echo ""
	@echo "$(BLUE)Current Branch:$(NC) $(GREEN)$$(git branch --show-current)$(NC)"
	@echo "$(BLUE)Last Commit:$(NC) $$(git log -1 --pretty=format:'%h - %s (%cr)')"

##@ Documentation

docs: ## Generate project documentation
	@echo "$(BLUE)Generating documentation...$(NC)"
	@echo "Documentation files:"
	@echo "  - README.md"
	@echo "  - CLAUDE.md"
	@echo "  - MIGRATION_ANALYSIS.md"
	@echo "  - V4_DEPLOYMENT.md"
	@echo "  - docs/LOCAL_DEVELOPMENT.md"

docs-api: ## Open API documentation (backend must be running)
	@echo "$(BLUE)Opening API documentation...$(NC)"
	@open http://localhost:8000/docs

##@ Shortcuts

all: install check build ## Install, check, and build everything

quick-deploy-v4: build-cdk deploy-v4 ## Quick v4 deployment (build + deploy)

fast-deploy-v4: cache-warm deploy-v4 ## Fast v4 deployment with cache warming

watch-cdk: ## Watch CDK for changes and auto-rebuild
	@echo "$(BLUE)Watching CDK for changes...$(NC)"
	@cd cdk && npm run watch

v4: deploy-v4 ## Shortcut for deploy-v4
v3: deploy-v3 ## Shortcut for deploy-v3
