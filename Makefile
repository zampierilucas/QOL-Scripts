# QOL-Scripts Makefile
# Requires: make (via Git Bash, Chocolatey, or WSL)

PYTHON = python
SRC_DIR = src
SPEC_FILE = $(SRC_DIR)/main.spec
DIST_DIR = $(SRC_DIR)/dist
BUILD_DIR = $(SRC_DIR)/build

.PHONY: all verify lint compile build dist clean install help run

# Default target
all: verify build

# Install dependencies
install:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m pip install ruff pyinstaller

# Verify code quality (lint + compile check)
verify: lint compile
	@echo "Verification complete!"

# Lint with ruff (fast Python linter)
lint:
	$(PYTHON) -m ruff check $(SRC_DIR) --ignore E501,E402

# Compile check for syntax errors
compile:
	$(PYTHON) -m py_compile $(SRC_DIR)/main.py

# Run without compiling
run:
	$(PYTHON) $(SRC_DIR)/main.py

# Build executable with PyInstaller (uses cache for speed)
build: dist

dist:
	cd $(SRC_DIR) && $(PYTHON) -m PyInstaller main.spec
	@echo "Build complete! Executable at $(DIST_DIR)/QOL-scripts.exe"

# Full rebuild (clean + build)
rebuild: clean dist

# Clean build artifacts
clean:
	rm -rf $(BUILD_DIR) $(DIST_DIR)
	rm -rf $(SRC_DIR)/__pycache__
	@echo "Cleaned build artifacts"

# Help
help:
	@echo "Available targets:"
	@echo "  make install  - Install dependencies (including dev tools)"
	@echo "  make verify   - Run linting and compile checks"
	@echo "  make lint     - Run ruff linter only"
	@echo "  make compile  - Check for Python syntax errors"
	@echo "  make run      - Run directly with Python (no compile)"
	@echo "  make build    - Build executable (uses cache, fast)"
	@echo "  make rebuild  - Clean + build (slow, full rebuild)"
	@echo "  make clean    - Remove build artifacts"
	@echo "  make all      - Run verify + build (default)"
	@echo "  make help     - Show this help"
