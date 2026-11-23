.PHONY: help install test lint check-docs run-control run-agent test-agent build-console smoke-console cli

help:
	@echo "install         install the control plane (editable, with dev extras)"
	@echo "test            control plane tests (pytest)"
	@echo "lint            ruff"
	@echo "check-docs      verify relative links in Markdown"
	@echo "run-control     run the control plane locally (zero-config token mode, token: dev-token)"
	@echo "run-agent       run the node agent (needs its environment variables, see deploy/pod/)"
	@echo "test-agent      node agent tests"
	@echo "build-console   typecheck and build the console"
	@echo "smoke-console   console smoke test (control plane running, console built)"
	@echo "cli             management CLI, e.g. make cli ARGS='user list'"

install:
	cd services/control && python -m pip install -e '.[dev]'

test:
	cd services/control && python -m pytest -q

lint:
	cd services/control && ruff check src tests

check-docs:
	python3 deploy/scripts/check-links.py

# Local development only: data under data/, the token is the public "dev-token". Never
# expose this to a network.
run-control:
	cd services/control && PYTHONPATH=src SKEIN_DB=../data/skein.db SKEIN_BLOB_ROOT=../data/blobs SKEIN_TOKEN=dev-token python -m skein

run-agent:
	cd services/node-agent && node node-agent.mjs

test-agent:
	cd services/node-agent && npm test

build-console:
	cd services/console && npm ci && npm run build

smoke-console:
	node deploy/scripts/console-smoke.mjs

cli:
	cd services/control && PYTHONPATH=src SKEIN_TOKEN=$${SKEIN_TOKEN:-dev-token} \
		SKEIN_CONTROL_URL=$${SKEIN_CONTROL_URL:-http://127.0.0.1:7800} python -m skein $(ARGS)
