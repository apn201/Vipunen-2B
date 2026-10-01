.PHONY: run build test check hooks estimate models console shell

IMAGE ?= vipunen-2b

ENV_FILE ?= $(firstword $(wildcard .env) $(wildcard ../../../.env))
ENV_FLAG := $(if $(ENV_FILE),--env-file $(ENV_FILE),)

DOCKER_RUN = docker run --rm $(ENV_FLAG) -v "$(CURDIR)/var:/app/var"

# Judges: end-to-end demo on the neutral example chain + public seeds.
# S1: offline echo pipeline. S2 switches this to the live endpoint.
run: build
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
	$(DOCKER_RUN) $(IMAGE) vipunen run --estimate

models: build
	$(DOCKER_RUN) $(IMAGE) vipunen models

# Operator console on localhost only. Mounts the gitignored private/ tree.
console: build
	$(DOCKER_RUN) -v "$(CURDIR)/private:/app/private" -p 127.0.0.1:8000:8000 $(IMAGE) vipunen console

shell: build
	$(DOCKER_RUN) -it $(IMAGE) bash
