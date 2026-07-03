# LabDog packaging Makefile
# Build orchestration for venv, frontend, tarball, deb, rpm

SHELL := /bin/bash

# --- Variables ---
VERSION      ?= $(shell grep '^version' ../backend/pyproject.toml 2>/dev/null | head -1 | sed 's/.*= *"\(.*\)"/\1/' || echo "0.1.0")
ARCH         ?= amd64
PYTHON       ?= python3.12
DESTDIR      ?= $(CURDIR)/staging
PREFIX       ?= /usr/lib/labdog
DIST         ?= $(CURDIR)/dist

# Build metadata baked into backend/app/_build_info.py at package time
# so the runtime /api/version endpoint can report it for non-Docker
# installs (.deb / .rpm / .tar.gz). Defaults come from the local git
# checkout and the wall clock; override from CI to inject the canonical
# release SHA + build date.
COMMIT_SHA   ?= $(shell git rev-parse HEAD 2>/dev/null || echo "")
BUILD_DATE   ?= $(shell date -u +'%Y-%m-%dT%H:%M:%SZ')

# Bundled action pack: fetched at build time from labdog-playbooks at
# the ref pinned in the repo-root LABDOG_PLAYBOOKS_REF file. Same
# model as the Docker build (see Stage 2b in Dockerfile). CI / dev
# override LABDOG_PLAYBOOKS_REPO + LABDOG_PLAYBOOKS_REF via env.
LABDOG_PLAYBOOKS_REPO ?= https://github.com/open-labdog/labdog-playbooks.git
LABDOG_PLAYBOOKS_REF  ?= $(shell cat ../LABDOG_PLAYBOOKS_REF 2>/dev/null | tr -d '[:space:]')
BUNDLED_FETCH_DIR     ?= $(CURDIR)/staging-bundled

# --- Phony targets ---
.PHONY: help build build-venv _build-venv build-frontend _build-frontend bake-build-info clean deb rpm tarball all fetch-bundled-pack

# --- Default ---
help:
	@echo "LabDog packaging targets:"
	@echo ""
	@echo "  build           Build all components (venv + frontend)"
	@echo "  build-venv      Create Python venv with production dependencies"
	@echo "  build-frontend  Build Next.js static export"
	@echo "  deb             Build .deb package (requires nfpm)"
	@echo "  rpm             Build .rpm package (requires nfpm)"
	@echo "  tarball         Build installable tarball"
	@echo "  all             Build tarball + deb + rpm"
	@echo "  clean           Remove staging directory and dist/"
	@echo ""
	@echo "Variables:"
	@echo "  VERSION=$(VERSION)  ARCH=$(ARCH)  PYTHON=$(PYTHON)"
	@echo "  DESTDIR=$(DESTDIR)  PREFIX=$(PREFIX)"

# --- Aggregate ---
build: build-venv build-frontend bake-build-info

# --- fetch-bundled-pack ---
# Clone labdog-playbooks at the pinned ref into a scratch dir; the
# `_build-venv` step then cp's it into the staging layout. Idempotent
# -- skipped if BUNDLED_FETCH_DIR is already populated, so successive
# invocations don't refetch.
#
# Delegates to scripts/fetch-bundled-pack.sh -- single source of truth
# for the clone shared with Dockerfile, dev/dev.sh, and CI.
fetch-bundled-pack:
	@if [ -d "$(BUNDLED_FETCH_DIR)" ] && [ -d "$(BUNDLED_FETCH_DIR)/actions" ]; then \
		echo "[fetch-bundled-pack] $(BUNDLED_FETCH_DIR) already populated -- skipping"; \
	else \
		LABDOG_PLAYBOOKS_REPO="$(LABDOG_PLAYBOOKS_REPO)" \
		LABDOG_PLAYBOOKS_REF="$(LABDOG_PLAYBOOKS_REF)" \
			../scripts/fetch-bundled-pack.sh "$(BUNDLED_FETCH_DIR)"; \
	fi

# --- bake-build-info ---
# Always re-runs (no skip-if-cached check) so a rebuild with a fresh
# COMMIT_SHA / BUILD_DATE updates the staged file. Skips silently when
# COMMIT_SHA is empty (e.g. building outside a git checkout) — the
# runtime then reports a "dev build".
bake-build-info:
	@if [ -z "$(COMMIT_SHA)" ]; then \
		echo "[bake-build-info] COMMIT_SHA empty — skipping (runtime will report dev build)"; \
	else \
		echo "[bake-build-info] Writing _build_info.py (sha=$(COMMIT_SHA), date=$(BUILD_DATE))"; \
		mkdir -p $(DESTDIR)$(PREFIX)/backend/app; \
		printf 'COMMIT_SHA = "%s"\nBUILD_DATE = "%s"\n' "$(COMMIT_SHA)" "$(BUILD_DATE)" \
			> $(DESTDIR)$(PREFIX)/backend/app/_build_info.py; \
	fi

# --- build-venv ---
build-venv: fetch-bundled-pack
	@if [ -x $(DESTDIR)$(PREFIX)/venv/bin/ansible-playbook ]; then \
		echo "[build-venv] venv already built — skipping"; \
	else \
		$(MAKE) _build-venv; \
	fi

_build-venv: fetch-bundled-pack
	@echo "[build-venv] Creating Python venv..."
	@mkdir -p $(DESTDIR)$(PREFIX)
	$(PYTHON) -m venv $(DESTDIR)$(PREFIX)/venv
	$(DESTDIR)$(PREFIX)/venv/bin/pip install --upgrade pip --quiet
	@echo "[build-venv] Installing backend dependencies..."
	@# setuptools' dynamic version reads a VERSION file relative to
	@# backend/pyproject.toml, but the source of truth lives at the repo
	@# root. Stage it in so the installed labdog-backend dist-metadata
	@# carries the real version (otherwise it builds as 0.0.0 and
	@# /api/version reports 0.0.0). backend/VERSION is gitignored.
	cp ../VERSION ../backend/VERSION
	$(DESTDIR)$(PREFIX)/venv/bin/pip install --no-cache-dir --quiet ../backend/
	@echo "[build-venv] Fixing shebangs..."
	@find $(DESTDIR)$(PREFIX)/venv/bin -maxdepth 1 -type f | while read f; do \
		head -c2 "$$f" | grep -q '^#!' && sed -i "1s|$(DESTDIR)||g" "$$f" || true; \
	done
	@echo "[build-venv] Relocating venv to the target system python..."
	@# The venv is created with whatever python3.12 built it. In CI that is
	@# a tool-cache python (e.g. /opt/hostedtoolcache/...) whose path does
	@# NOT exist on the target host, leaving venv/bin/python a dangling
	@# symlink and the install broken. Repoint bin/python* and pyvenv.cfg
	@# at the distro python3.12 the package depends on — /usr/bin/python3.12
	@# on both Ubuntu 24.04 and Rocky 9 — so the installed venv actually
	@# runs. (Safe: the venv's 3.12.x site-packages are ABI-compatible with
	@# the target's 3.12.x interpreter.)
	@ln -sfn /usr/bin/python3.12 $(DESTDIR)$(PREFIX)/venv/bin/python3.12
	@ln -sfn python3.12 $(DESTDIR)$(PREFIX)/venv/bin/python3
	@ln -sfn python3.12 $(DESTDIR)$(PREFIX)/venv/bin/python
	@sed -i "s|^home = .*|home = /usr/bin|" $(DESTDIR)$(PREFIX)/venv/pyvenv.cfg
	@sed -i "s|^executable = .*|executable = /usr/bin/python3.12|" $(DESTDIR)$(PREFIX)/venv/pyvenv.cfg
	@echo "[build-venv] Stripping bytecode..."
	@find $(DESTDIR)$(PREFIX)/venv -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	@find $(DESTDIR)$(PREFIX)/venv -name "*.pyc" -delete 2>/dev/null; true
	@echo "[build-venv] Copying backend source..."
	@mkdir -p $(DESTDIR)$(PREFIX)/backend
	@cp -r ../backend/app ../backend/alembic ../backend/alembic.ini $(DESTDIR)$(PREFIX)/backend/
	@echo "[build-venv] Copying bundled action pack from $(BUNDLED_FETCH_DIR)..."
	@# Wipe any leftover ansible/ from the source cp -- the in-repo dir
	@# is gitignored but a dev clone via ./dev/dev.sh bundle may leave
	@# content there. The pinned BUNDLED_FETCH_DIR is the source of truth.
	@rm -rf $(DESTDIR)$(PREFIX)/backend/app/ansible
	@mkdir -p $(DESTDIR)$(PREFIX)/backend/app/ansible
	@cp -r $(BUNDLED_FETCH_DIR)/. $(DESTDIR)$(PREFIX)/backend/app/ansible/
	@test -x $(DESTDIR)$(PREFIX)/venv/bin/ansible-playbook \
		|| (echo "ERROR: ansible-playbook not found in venv — check backend dependencies" && exit 1)
	@echo "✓ venv built at $(DESTDIR)$(PREFIX)/venv"

# --- build-frontend ---
build-frontend:
	@if [ -d $(DESTDIR)$(PREFIX)/frontend/out ]; then \
		echo "[build-frontend] frontend already built — skipping"; \
	else \
		$(MAKE) _build-frontend; \
	fi

_build-frontend:
	@echo "[build-frontend] Building Next.js static export..."
	cd ../frontend && npm ci --silent
	cd ../frontend && npm run build
	@mkdir -p $(DESTDIR)$(PREFIX)/frontend
	cp -r ../frontend/out $(DESTDIR)$(PREFIX)/frontend/out
	@echo "✓ frontend built at $(DESTDIR)$(PREFIX)/frontend/out"

# --- clean ---
clean:
	rm -rf $(DESTDIR) $(DIST) $(BUNDLED_FETCH_DIR)
	@echo "✓ cleaned staging, dist, and bundled-fetch directories"

# --- Packaging targets ---

TARBALL_NAME := labdog-$(VERSION)-linux-$(ARCH).tar.gz
TARBALL_DIR  := labdog-$(VERSION)-linux-$(ARCH)

deb: build
	@mkdir -p $(DIST)
	VERSION=$(VERSION) nfpm pkg --packager deb --config nfpm.yaml --target $(DIST)/
	@echo "✓ .deb built: $$(ls $(DIST)/labdog_*.deb | tail -1)"

rpm: build
	@mkdir -p $(DIST)
	VERSION=$(VERSION) nfpm pkg --packager rpm --config nfpm.yaml --target $(DIST)/
	@echo "✓ .rpm built: $$(ls $(DIST)/labdog-*.rpm | tail -1)"

tarball: build
	@mkdir -p $(DIST)/$(TARBALL_DIR)
	@echo "[tarball] Assembling tarball directory..."
	cp -r $(DESTDIR)/. $(DIST)/$(TARBALL_DIR)/
	cp install.sh uninstall.sh $(DIST)/$(TARBALL_DIR)/
	chmod +x $(DIST)/$(TARBALL_DIR)/install.sh $(DIST)/$(TARBALL_DIR)/uninstall.sh
	@# install.sh expects systemd/, tmpfiles.d/, and etc/ at the
	@# tarball root (parallel to install.sh). nfpm pulls these into
	@# the .deb / .rpm directly via nfpm.yaml; the tarball has to
	@# bundle them here.
	@mkdir -p $(DIST)/$(TARBALL_DIR)/systemd $(DIST)/$(TARBALL_DIR)/tmpfiles.d $(DIST)/$(TARBALL_DIR)/etc
	cp systemd/labdog.service $(DIST)/$(TARBALL_DIR)/systemd/
	cp tmpfiles.d/labdog.conf $(DIST)/$(TARBALL_DIR)/tmpfiles.d/
	cp etc/labdog.toml $(DIST)/$(TARBALL_DIR)/etc/
	@echo "[tarball] Creating archive..."
	tar -czf $(DIST)/$(TARBALL_NAME) -C $(DIST) $(TARBALL_DIR)
	rm -rf $(DIST)/$(TARBALL_DIR)
	@echo "✓ tarball: $(DIST)/$(TARBALL_NAME)"
	@sha256sum $(DIST)/$(TARBALL_NAME)

all: tarball deb rpm
	@echo ""
	@echo "=== Build complete ==="
	@ls -lh $(DIST)/
