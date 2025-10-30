#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"

if [[ ! -d "${VENV_DIR}" ]]; then
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --upgrade pip
fi

"${VENV_DIR}/bin/pip" install -r "${PROJECT_ROOT}/requirements.txt"
export MARKSCHECKER_BASE_DIR="${PROJECT_ROOT}/instance"
mkdir -p "${MARKSCHECKER_BASE_DIR}"

exec "${VENV_DIR}/bin/flask" --app app:create_app --debug run
