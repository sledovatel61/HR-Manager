.PHONY: help up down logs ps backend-test backend-lint backend-typecheck frontend-test frontend-lint frontend-typecheck frontend-build check prod-preflight

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the full stack (PostgreSQL + backend + frontend)
	docker compose -f infra/docker-compose.yml up --build -d

down: ## Stop and remove the stack (data volume is preserved)
	docker compose -f infra/docker-compose.yml down

logs: ## Follow logs of all services
	docker compose -f infra/docker-compose.yml logs -f

ps: ## Show service status
	docker compose -f infra/docker-compose.yml ps

backend-test: ## Run backend tests (unit; integration needs TEST_DATABASE_URL)
	cd backend && pytest -m "not integration" -v

backend-lint: ## Run ruff on the backend
	cd backend && ruff check .

backend-typecheck: ## Run mypy on the backend
	cd backend && mypy app

frontend-test: ## Run frontend unit tests
	cd frontend && npm run test

frontend-lint: ## Run ESLint in the frontend
	cd frontend && npm run lint

frontend-typecheck: ## Run TypeScript typecheck in the frontend
	cd frontend && npm run typecheck

frontend-build: ## Produce a production frontend build
	cd frontend && npm run build

check: backend-lint backend-typecheck backend-test frontend-lint frontend-typecheck frontend-test frontend-build ## Run every check

prod-preflight: ## Verify that production secrets are configured
	infra/scripts/check_env.sh
