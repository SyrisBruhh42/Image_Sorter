#!/usr/bin/env bash
# scripts/test_headless.sh
# Headless test wrapper script for Image Sorter test execution with isolated temporary XDG directories.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Ensure virtual environment is activated if present
if [ -d "${REPO_ROOT}/.venv" ]; then
    source "${REPO_ROOT}/.venv/bin/activate"
fi

# Create isolated temporary directory for XDG Base Directories
TMP_XDG_DIR="$(mktemp -d)"

cleanup() {
    rm -rf "${TMP_XDG_DIR}"
}
trap cleanup EXIT

export XDG_CONFIG_HOME="${TMP_XDG_DIR}/config"
export XDG_DATA_HOME="${TMP_XDG_DIR}/data"
export XDG_CACHE_HOME="${TMP_XDG_DIR}/cache"
export XDG_STATE_HOME="${TMP_XDG_DIR}/state"

mkdir -p "${XDG_CONFIG_HOME}" "${XDG_DATA_HOME}" "${XDG_CACHE_HOME}" "${XDG_STATE_HOME}"

export PYTHONPATH="${REPO_ROOT}/src"
export QT_QPA_PLATFORM="offscreen"

echo "=== Running Headless Test Suite with Isolated XDG Paths ==="
echo "XDG Root Temp Dir: ${TMP_XDG_DIR}"
echo "PYTHONPATH: ${PYTHONPATH}"
echo "QT_QPA_PLATFORM: ${QT_QPA_PLATFORM}"

# Execute pytest with passed arguments or default non-packaging suite
if [ $# -eq 0 ]; then
    python -m pytest -m "not packaging" -q "${REPO_ROOT}/tests"
else
    python -m pytest "$@"
fi
