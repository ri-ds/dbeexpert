.DEFAULT_GOAL := help
.PHONY: help up down logs restart rebuild health smoke ps clean reset-db shell-neo4j

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the whole stack
	docker compose up -d --build
	@echo ""
	@echo "  Web app        http://localhost:$${FRONTEND_PORT:-8080}"
	@echo "  API docs       http://localhost:$${BACKEND_PORT:-8011}/api/docs"
	@echo "  Neo4j Browser  http://localhost:$${NEO4J_HTTP_PORT:-7474}"

down: ## Stop the stack, keep the database volume
	docker compose down

logs: ## Follow logs for every service
	docker compose logs -f

restart: ## Restart backend and frontend only
	docker compose restart backend frontend

rebuild: ## Rebuild backend and frontend images from scratch
	docker compose build --no-cache backend frontend
	docker compose up -d backend frontend

ps: ## Show container status
	docker compose ps

health: ## Print the backend health payload
	@curl -s http://localhost:$${BACKEND_PORT:-8011}/api/health \
		| python3 -m json.tool || echo "Backend is not responding yet."

smoke: ## Run one real question through the API
	@curl -s -X POST http://localhost:$${BACKEND_PORT:-8011}/api/query \
		-H 'Content-Type: application/json' \
		-d '{"question":"Which faculty have expertise in cystic fibrosis?","mode":"hybrid","sessionId":"make-smoke"}' \
		| python3 -m json.tool

shell-neo4j: ## Open cypher-shell against the running database
	docker compose exec neo4j cypher-shell -u neo4j -p $${NEO4J_PASSWORD:-dbepassword123}

reset-db: ## Delete the database volume so the next start restores the dump again
	docker compose down
	docker volume rm dbeexpert_neo4j_data dbeexpert_neo4j_logs 2>/dev/null || true
	@echo "Database volumes removed. The next 'make up' will restore the dump."

clean: ## Remove containers, volumes, and built images
	docker compose down -v --remove-orphans
	docker image rm dbeexpert-backend:latest dbeexpert-frontend:latest 2>/dev/null || true
