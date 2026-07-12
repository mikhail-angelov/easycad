.PHONY: run test test-unit test-smoke test-capabilities test-e2e-real test-e2e-generation-real test-e2e-features-real

PYTHON ?= .venv/bin/python
LOCAL_ENV = CADQUERY_WORKER_TIMEOUT_SECONDS=180 XDG_CACHE_HOME=$(CURDIR)/.cache PYTHONDONTWRITEBYTECODE=1

run:
	$(LOCAL_ENV) $(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8852

test: test-unit test-smoke

test-unit:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests

test-smoke:
	$(LOCAL_ENV) $(PYTHON) -m tests.smoke_fixture_generation

test-capabilities:
	uv --version
	$(LOCAL_ENV) $(PYTHON) -m tests.capability_regression

test-e2e-real: test-e2e-features-real test-e2e-generation-real

test-e2e-generation-real:
	$(LOCAL_ENV) $(PYTHON) -m tests.e2e_real_fixture_generation

test-e2e-features-real:
	$(LOCAL_ENV) $(PYTHON) -m tests.e2e_real_feature_analysis
