.PHONY: run demo-offline build test check hooks estimate models ping console shell

IMAGE ?= vipunen-2b

ENV_FILE ?= $(firstword $(wildcard .env) $(wildcard ../../../.env))
ENV_FLAG := $(if $(ENV_FILE),--env-file $(ENV_FILE),)

# var/ = usage + hash-only ledger; private/ = verbatim evidence (gitignored).
DOCKER_RUN = docker run --rm $(ENV_FLAG) -v "$(CURDIR)/var:/app/var" -v "$(CURDIR)/private:/app/private"

# Judges: end-to-end demo on the neutral example chain + control arm, public seeds,
# live Apertus endpoint (needs LLM_API_KEY in .env).
run: build
	$(DOCKER_RUN) $(IMAGE) vipunen run --demo

# Same pipeline, no network, no key.
demo-offline: build
	$(DOCKER_RUN) $(IMAGE) vipunen run --demo --echo

build:
	docker build -t $(IMAGE) .

test: build
	docker run --rm $(IMAGE) python -m pytest -q

# Tests + publish gate (blocks push on exit 1).
check: test
	python tools/publish_gate.py

# Run the publish gate automatically before every git push.
hooks:
	python tools/publish_gate.py --install

estimate: build
	$(DOCKER_RUN) $(IMAGE) vipunen run --demo --estimate

models: build
	$(DOCKER_RUN) $(IMAGE) vipunen models

# One short live call per Apertus model (counts against the budget).
ping: build
	$(DOCKER_RUN) $(IMAGE) vipunen models --ping

# Operator console on localhost only. Mounts the gitignored private/ tree.
console: build
	$(DOCKER_RUN) -p 127.0.0.1:8000:8000 $(IMAGE) vipunen console

shell: build
	$(DOCKER_RUN) -it $(IMAGE) bash
