# Barricade packaging Makefile
# Build orchestration for venv, frontend, tarball, deb, rpm

SHELL := /bin/bash

# --- Variables ---
VERSION      ?= $(shell grep '^version' ../backend/pyproject.toml 2>/dev/null | head -1 | sed 's/.*= *"\(.*\)"/\1/' || echo "0.1.0")
ARCH         ?= amd64
PYTHON       ?= python3.12
DESTDIR      ?= $(CURDIR)/staging
PREFIX       ?= /usr/lib/barricade
DIST         ?= $(CURDIR)/dist

# --- Phony targets ---
.PHONY: help build build-venv _build-venv build-frontend _build-frontend clean deb rpm tarball all

# --- Default ---
help:
	@echo "Barricade packaging targets:"
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
build: build-venv build-frontend

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

TARBALL_NAME := barricade-$(VERSION)-linux-$(ARCH).tar.gz
TARBALL_DIR  := barricade-$(VERSION)-linux-$(ARCH)

deb: build
	@mkdir -p $(DIST)
	VERSION=$(VERSION) nfpm pkg --packager deb --config nfpm.yaml --target $(DIST)/
	@echo "✓ .deb built: $$(ls $(DIST)/barricade_*.deb | tail -1)"

rpm: build
	@mkdir -p $(DIST)
	VERSION=$(VERSION) nfpm pkg --packager rpm --config nfpm.yaml --target $(DIST)/
	@echo "✓ .rpm built: $$(ls $(DIST)/barricade-*.rpm | tail -1)"

tarball: build
	@mkdir -p $(DIST)/$(TARBALL_DIR)
	@echo "[tarball] Assembling tarball directory..."
	cp -r $(DESTDIR)/. $(DIST)/$(TARBALL_DIR)/
	cp install.sh uninstall.sh $(DIST)/$(TARBALL_DIR)/
	chmod +x $(DIST)/$(TARBALL_DIR)/install.sh $(DIST)/$(TARBALL_DIR)/uninstall.sh
	@echo "[tarball] Creating archive..."
	tar -czf $(DIST)/$(TARBALL_NAME) -C $(DIST) $(TARBALL_DIR)
	rm -rf $(DIST)/$(TARBALL_DIR)
	@echo "✓ tarball: $(DIST)/$(TARBALL_NAME)"
	@sha256sum $(DIST)/$(TARBALL_NAME)

all: tarball deb rpm
	@echo ""
	@echo "=== Build complete ==="
	@ls -lh $(DIST)/
