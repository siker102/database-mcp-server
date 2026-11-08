# Developer and CI entry points. CI calls exactly these targets, so local and CI runs match.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV ?= uv
COMPOSE ?= docker compose
CHART := deploy/helm/titanic-mcp
KUBE_VERSION ?= 1.33.0

.PHONY: help install fmt lint typecheck test check helm-check up down smoke db-init run docker-build

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install: ## Install all dependencies from uv.lock (fails if the lock is outdated)
	$(UV) sync --locked

fmt: ## Format code and apply safe lint fixes
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

lint: ## Lint and check formatting
	$(UV) run ruff check .
	$(UV) run ruff format --check .

typecheck: ## Static type check (mypy --strict)
	$(UV) run mypy

test: ## Unit, integration and security tests with coverage (needs Docker)
	$(UV) run pytest

helm-check: ## Lint the Helm chart and validate the rendered manifests against the K8s schema
	helm lint $(CHART) --strict
	helm template titanic-mcp $(CHART) | kubeconform -strict -summary -kubernetes-version $(KUBE_VERSION)

check: lint typecheck test helm-check ## Everything that must be green before a commit

up: ## Start Postgres, db-init and the server with Docker Compose
	$(UV) run python scripts/init_local_env.py
	$(COMPOSE) up -d --build --wait

down: ## Stop the Compose stack and delete its volumes
	$(COMPOSE) down -v

smoke: ## End-to-end test against the running Compose stack
	$(UV) run python scripts/smoke.py --url http://localhost:8000/mcp

db-init: ## Run db-init once more against the Compose database
	$(COMPOSE) run --rm db-init

run: ## Run the server locally (needs a running database and a filled .env)
	set -a && source .env && set +a && $(UV) run titanic-mcp serve

docker-build: ## Build the server image
	docker build -t titanic-mcp:local .
