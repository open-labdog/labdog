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

# --- Phony targets ---
.PHONY: help build build-venv _build-venv build-frontend _build-frontend bake-build-info clean deb rpm tarball all

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
build-venv:
	@if [ -x $(DESTDIR)$(PREFIX)/venv/bin/ansible-playbook ]; then \
		echo "[build-venv] venv already built — skipping"; \
	else \
		$(MAKE) _build-venv; \
	fi

_build-venv:
	@echo "[build-venv] Creating Python venv..."
	@mkdir -p $(DESTDIR)$(PREFIX)
	$(PYTHON) -m venv $(DESTDIR)$(PREFIX)/venv
	$(DESTDIR)$(PREFIX)/venv/bin/pip install --upgrade pip --quiet
	@echo "[build-venv] Installing backend dependencies..."
	$(DESTDIR)$(PREFIX)/venv/bin/pip install --no-cache-dir --quiet ../backend/
	@echo "[build-venv] Fixing shebangs..."
	@find $(DESTDIR)$(PREFIX)/venv/bin -maxdepth 1 -type f | while read f; do \
		head -c2 "$$f" | grep -q '^#!' && sed -i "1s|$(DESTDIR)||g" "$$f" || true; \
	done
	@echo "[build-venv] Fixing pyvenv.cfg..."
	@PYDIR=$$(dirname $$($(PYTHON) -c "import sys; print(sys.executable)")); \
	sed -i "s|home = .*|home = $$PYDIR|g" $(DESTDIR)$(PREFIX)/venv/pyvenv.cfg
	@echo "[build-venv] Stripping bytecode..."
	@find $(DESTDIR)$(PREFIX)/venv -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	@find $(DESTDIR)$(PREFIX)/venv -name "*.pyc" -delete 2>/dev/null; true
	@echo "[build-venv] Copying backend source..."
	@mkdir -p $(DESTDIR)$(PREFIX)/backend
	@cp -r ../backend/app ../backend/alembic ../backend/alembic.ini $(DESTDIR)$(PREFIX)/backend/
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
	rm -rf $(DESTDIR) $(DIST)
	@echo "✓ cleaned staging and dist directories"

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
