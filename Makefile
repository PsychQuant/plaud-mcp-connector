# plaud-mcp-connector
#
# The site targets exist to make one thing impossible: publishing site/ without
# the rules in site/README.md having been checked. Running `vercel deploy` by
# hand works fine and skips all of it — that is exactly the hole this closes.
#
#   make                       show this help
#   make test                  run the suite
#   make site-check            check site/ against the positioning rules
#   make site-preview          deploy a preview URL (gated)
#   make site-prod CONFIRM=1   deploy to production (gated + confirmed)
#
# DOMAIN=example.com checks the name the page will be served under. Leave it
# empty for Vercel's generated hostname.

PYTHON ?= python3
SITE   ?= site
README ?= README.md
DOMAIN ?=

.DEFAULT_GOAL := help
.PHONY: help test site-check check site-preview site-prod _confirm-prod _require-vercel

help:  ## Show this help
	@grep -hE '^[a-z][a-zA-Z0-9_-]*:.*## ' $(MAKEFILE_LIST) \
	  | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables: DOMAIN=<host>  SITE=$(SITE)  PYTHON=$(PYTHON)"

test:  ## Run the whole test suite
	$(PYTHON) -m unittest discover -s tests -q

site-check:  ## Check site/ against the positioning rules in site/README.md
	$(PYTHON) scripts/site_check.py --site $(SITE) --readme $(README) --domain "$(DOMAIN)"

check: test site-check  ## Everything that must pass before a deploy

# Prerequisite order is deliberate. The confirmation comes first so a refusal is
# instant rather than arriving after a full test run, and the deploy command is
# the last thing in the recipe so no gate can be skipped by reordering.
site-preview: _require-vercel check  ## Deploy a preview URL (not the public site)
	cd $(SITE) && vercel deploy

site-prod: _confirm-prod _require-vercel check  ## Deploy to production — needs CONFIRM=1
	cd $(SITE) && vercel deploy --prod

_confirm-prod:
	@if [ -z "$(CONFIRM)" ]; then \
	  echo "site-prod publishes site/ to the public production URL."; \
	  echo "That is outward-facing and awkward to take back."; \
	  echo ""; \
	  echo "Re-run as:  make site-prod CONFIRM=1"; \
	  exit 1; \
	fi

_require-vercel:
	@command -v vercel >/dev/null 2>&1 || { \
	  echo "vercel CLI not found. Install it with:  npm i -g vercel"; \
	  exit 1; \
	}
