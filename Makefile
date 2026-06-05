# Makefile — ClickHouse Ingestor add-on
#
# Targets
#   build        Build the ingestor container image with podman.
#   test-deps    Install Python test dependencies.
#   test         Run the unit tests (requires test-deps first, or a venv).
#   e2e          Run the full end-to-end harness (scripts/e2e.sh).
#   clean        Remove the built ingestor image and any leftover e2e artefacts.
#
# Variables
#   INGESTOR_IMAGE   Image tag for the ingestor (default: localhost/ch-ingestor:local)
#   BUILD_FROM       Base image used at build time (default: ghcr.io/hassio-addons/base/amd64:17.2.1)

INGESTOR_IMAGE ?= localhost/ch-ingestor:local
BUILD_FROM     ?= ghcr.io/hassio-addons/base/amd64:17.2.1

.PHONY: build test-deps test e2e clean

build:
	podman build \
		--build-arg BUILD_FROM=$(BUILD_FROM) \
		-t $(INGESTOR_IMAGE) \
		ingestor/

test-deps:
	pip3 install -r test/requirements-test.txt

test:
	python3 -m pytest test/ -v

e2e:
	INGESTOR_IMAGE=$(INGESTOR_IMAGE) bash scripts/e2e.sh

clean:
	podman rmi $(INGESTOR_IMAGE) 2>/dev/null || true
	podman rm -f ch_e2e_ingestor ch_e2e_mock ch_e2e_ch 2>/dev/null || true
	podman network rm ch_e2e_net 2>/dev/null || true
