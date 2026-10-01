.PHONY: run demo-offline baseline report build test check hooks estimate models ping console shell

IMAGE ?= vipunen-2b

ENV_FILE ?= $(firstword $(wildcard .env) $(wildcard ../../../.env))
# Judges may export LLM_NAME / LLM_BASE_URL / LLM_API_KEY instead of writing a .env:
# `-e NAME` passes a variable through from the shell when it is set there (and wins over .env).
PASS_ENV := LLM_NAME LLM_BASE_URL LLM_API_KEY PUBLICAI_BASE_URL PUBLICAI_API_KEY VIPUNEN_BUDGET_USD
ENV_FLAG := $(if $(ENV_FILE),--env-file $(ENV_FILE),) $(foreach v,$(PASS_ENV),$(if $($(v)),-e $(v),))

# var/ = usage + hash-only ledger; private/ = verbatim evidence (gitignored).
DOCKER_RUN = docker run --rm $(ENV_FLAG) -v "$(CURDIR)/var:/app/var" -v "$(CURDIR)/private:/app/private"

# Judges: end-to-end demo on the neutral example chain + control arm, public seeds,
# live Apertus endpoint (needs LLM_API_KEY in .env).
run: build
	$(DOCKER_RUN) $(IMAGE) vipunen run --demo

# Same pipeline, no network, no key.
demo-offline: build
	$(DOCKER_RUN) $(IMAGE) vipunen run --demo --echo

# Prose control arm: the history/culture seeds, no verse, all six Apertus models
# (v1.0 routed to Public AI), deterministic verdicts. Then the table.
BASELINE_MODELS = swiss-ai/Apertus-v1.5-8B swiss-ai/Apertus-v1.5-8B-thinking 	swiss-ai/Apertus-v1.5-70B swiss-ai/Apertus-v1.5-70B-thinking 	swiss-ai/Apertus-8B-Instruct-2509 swiss-ai/Apertus-70B-Instruct-2509
baseline: build
	$(DOCKER_RUN) $(IMAGE) vipunen run --quiet --endpoint auto --chain config/passthrough.yaml 		--seeds data/seeds/history_culture.yaml $(foreach m,$(BASELINE_MODELS),--model $(m))

# Re-score all stored evidence (offline, no key). BATCH=<id> for one batch.
report: build
	$(DOCKER_RUN) $(IMAGE) vipunen report --pairs $(if $(BATCH),--batch $(BATCH),)

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

# Operator console on http://localhost:8000 only. Mounts the gitignored private/ tree
# (operator chain, swap body private/unmask.py, evidence).
console: build
	$(DOCKER_RUN) -p 127.0.0.1:8000:8000 $(IMAGE) vipunen console --host 0.0.0.0

shell: build
	$(DOCKER_RUN) -it $(IMAGE) bash
