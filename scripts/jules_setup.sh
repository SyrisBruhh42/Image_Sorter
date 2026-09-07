#!/usr/bin/env bash
# scripts/jules_setup.sh
# Repeatable and idempotent environment setup script for Image Sorter development and testing on Ubuntu 24.04.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== Image Sorter Jules Setup Script ==="
echo "Repository Root: ${REPO_ROOT}"

# 1. Virtual Environment Setup
VENV_DIR="${REPO_ROOT}/.venv"

if [ ! -d "${VENV_DIR}" ]; then
    echo "Creating isolated virtual environment at ${VENV_DIR}..."
    python3 -m venv "${VENV_DIR}"
else
    echo "Virtual environment already exists at ${VENV_DIR}."
fi

# Activate virtual environment
source "${VENV_DIR}/bin/activate"

# 2. Upgrade core tooling
python -m pip install --upgrade pip setuptools wheel --quiet

# 3. Install the package and its declared development dependencies
echo "Installing project dependencies..."
python -m pip install -e "${REPO_ROOT}[dev]" --quiet

# 4. Dependency Health Check
echo "Running dependency check..."
pip check

# 5. Check System Shared Libraries
echo "Checking system Qt / X11 / Wayland runtime library prerequisites..."
REQUIRED_LIBS=("libegl1" "libgl1" "libdbus-1-3")
MISSING_LIBS=()

for lib in "${REQUIRED_LIBS[@]}"; do
    if ! dpkg -s "${lib}" &>/dev/null; then
        MISSING_LIBS+=("${lib}")
    fi
done

if [ ${#MISSING_LIBS[@]} -ne 0 ]; then
    echo "WARNING: The following system libraries are missing and may be required for GUI execution:"
    for lib in "${MISSING_LIBS[@]}"; do
        echo "  - ${lib}"
    done
    echo "Note: libxcb-cursor0 was noted in CI; if running xcb backend natively, install via apt."
else
    echo "All core system library checks passed."
fi

echo "=== Jules Setup Complete ==="
echo "Virtual environment ready at ${VENV_DIR}."
