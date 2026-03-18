# Barricade packaging Makefile
# Build orchestration for venv, frontend, node, tarball, deb, rpm

SHELL := /bin/bash

# --- Variables ---
VERSION      ?= $(shell grep '^version' ../backend/pyproject.toml 2>/dev/null | head -1 | sed 's/.*= *"\(.*\)"/\1/' || echo "0.1.0")
ARCH         ?= amd64
PYTHON       ?= python3.12
NODE_VERSION ?= 20.18.1
DESTDIR      ?= $(CURDIR)/staging
PREFIX       ?= /usr/lib/barricade
DIST         ?= $(CURDIR)/dist

NODE_DIST_URL := https://nodejs.org/dist/v$(NODE_VERSION)/node-v$(NODE_VERSION)-linux-x64.tar.xz
NODE_CACHE    := $(CURDIR)/.cache/node-v$(NODE_VERSION)-linux-x64.tar.xz

# --- Phony targets ---
.PHONY: help build build-venv build-frontend download-node clean

# --- Default ---
help:
	@echo "Barricade packaging targets:"
	@echo ""
	@echo "  build           Build all components (venv + frontend + node)"
	@echo "  build-venv      Create Python venv with production dependencies"
	@echo "  build-frontend  Build Next.js standalone output"
	@echo "  download-node   Download and strip Node.js binary"
	@echo "  deb             Build .deb package (requires nfpm)"
	@echo "  rpm             Build .rpm package (requires nfpm)"
	@echo "  tarball         Build installable tarball"
	@echo "  all             Build tarball + deb + rpm"
	@echo "  clean           Remove staging directory and dist/"
	@echo ""
	@echo "Variables:"
	@echo "  VERSION=$(VERSION)  ARCH=$(ARCH)  PYTHON=$(PYTHON)"
	@echo "  NODE_VERSION=$(NODE_VERSION)  DESTDIR=$(DESTDIR)  PREFIX=$(PREFIX)"

# --- Aggregate ---
build: build-venv build-frontend download-node

# --- build-venv ---
build-venv:
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
	@echo "[build-frontend] Building Next.js standalone..."
	cd ../frontend && npm ci --silent
	cd ../frontend && npm run build
	@mkdir -p $(DESTDIR)$(PREFIX)/frontend
	cp -r ../frontend/.next/standalone/. $(DESTDIR)$(PREFIX)/frontend/
	cp -r ../frontend/public $(DESTDIR)$(PREFIX)/frontend/public
	mkdir -p $(DESTDIR)$(PREFIX)/frontend/.next/static
	cp -r ../frontend/.next/static/. $(DESTDIR)$(PREFIX)/frontend/.next/static/
	@echo "✓ frontend built at $(DESTDIR)$(PREFIX)/frontend"

# --- download-node ---
download-node:
	@echo "[download-node] Downloading Node.js v$(NODE_VERSION)..."
	@mkdir -p $(CURDIR)/.cache $(DESTDIR)$(PREFIX)/node/bin
	@if [ ! -f $(NODE_CACHE) ]; then \
		curl -fsSL --progress-bar -o $(NODE_CACHE) $(NODE_DIST_URL); \
	else \
		echo "[download-node] Using cached $(NODE_CACHE)"; \
	fi
	@echo "[download-node] Extracting node binary..."
	tar -xJf $(NODE_CACHE) --strip-components=2 -C $(DESTDIR)$(PREFIX)/node/bin \
		node-v$(NODE_VERSION)-linux-x64/bin/node
	@if command -v strip >/dev/null 2>&1; then \
		strip $(DESTDIR)$(PREFIX)/node/bin/node; \
		echo "[download-node] Stripped debug symbols"; \
	fi
	@$(DESTDIR)$(PREFIX)/node/bin/node --version
	@echo "✓ node binary at $(DESTDIR)$(PREFIX)/node/bin/node"

# --- clean ---
clean:
	rm -rf $(DESTDIR) $(DIST)
	@echo "✓ cleaned staging and dist directories"
