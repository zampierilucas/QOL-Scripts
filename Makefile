# QOL-Scripts Makefile
# Requires: make (via Git Bash, Chocolatey, or WSL)

PYTHON = python
SRC_DIR = src
SPEC_FILE = $(SRC_DIR)/main.spec
DIST_DIR = $(SRC_DIR)/dist
BUILD_DIR = $(SRC_DIR)/build

.PHONY: all verify lint dead compile build dist clean install help run debug

# Default target
all: verify build

# Install dependencies
install:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m pip install -e ".[dev]" pyinstaller

# Verify code quality (lint + dead code + compile check)
verify: lint dead compile
	@echo "Verification complete!"

# Lint with ruff (config in pyproject.toml)
lint:
	$(PYTHON) -m ruff check $(SRC_DIR)

# Dead code detection with vulture (config in pyproject.toml)
dead:
	$(PYTHON) -m vulture $(SRC_DIR)

# Compile check for syntax errors
compile:
	$(PYTHON) -m py_compile $(SRC_DIR)/main.py

# Run without compiling
run:
	$(PYTHON) $(SRC_DIR)/main.py

# Run with debug mode
debug:
	$(PYTHON) $(SRC_DIR)/main.py --debug

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
	@echo "  make verify   - Run linting, dead code check, and compile check"
	@echo "  make lint     - Run ruff linter only"
	@echo "  make dead     - Run vulture dead code detection only"
	@echo "  make compile  - Check for Python syntax errors"
	@echo "  make run      - Run directly with Python (no compile)"
	@echo "  make debug    - Run with --debug flag"
	@echo "  make build    - Build executable (uses cache, fast)"
	@echo "  make rebuild  - Clean + build (slow, full rebuild)"
	@echo "  make clean    - Remove build artifacts"
	@echo "  make all      - Run verify + build (default)"
	@echo "  make help     - Show this help"
