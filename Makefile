SHELL := /bin/bash
.DEFAULT_GOAL := validate
PACKAGE ?= build/packages/unifi-network-application-$(shell .tools/bin/helm4 show chart charts/unifi-network-application | sed -n 's/^version: //p').tgz
SMOKE_ARGS ?=

.PHONY: bootstrap lint validate package smoke
bootstrap:
	@./scripts/bootstrap-tools.sh

lint:
	@./scripts/validate.sh --lint-only

validate:
	@./scripts/validate.sh

package:
	@mkdir -p build/packages
	@.tools/bin/helm4 package charts/unifi-network-application --destination build/packages

smoke:
	@source .tools/env.sh && python3 scripts/integration/suseai-smoke.py --package "$(PACKAGE)" $(SMOKE_ARGS)
