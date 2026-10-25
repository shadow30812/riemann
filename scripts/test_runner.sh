#!/usr/bin/env bash

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$PROJECT_ROOT/logs/test_results.log"
FAIL=0

# Python tests run in the uv environment created by `uv venv riemann` (see
# docs/README.md). Set RIEMANN_VENV to point at a different uv environment.
VENV_DIR="${RIEMANN_VENV:-$PROJECT_ROOT/riemann}"
VENV_PYTHON="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
    echo -e "\033[0;31mNo uv environment at $VENV_DIR. Create it with: uv venv riemann --python 3.11 && uv pip install -e \".[dev]\"\033[0m"
    exit 1
fi
# A system LD_LIBRARY_PATH can shadow PySide6's bundled Qt libraries.
PYTEST="env -u LD_LIBRARY_PATH \"$VENV_PYTHON\" -m pytest"

cd "$PROJECT_ROOT"
> "$LOG_FILE"

run_and_log() {
    CMD="$1"
    bash -c "$CMD" 2>&1 | tee >(sed 's/\x1b\[[0-9;]*m//g' >> "$LOG_FILE")
    return ${PIPESTATUS[0]}
}

echo -e "\033[0;34mRunning rust-ocr-worker tests...\033[0m"
if cd rust-ocr-worker && run_and_log "cargo test"; then
    echo -e "\033[0;32mrust-ocr-worker tests passed!\033[0m\n"
    cd ..
else
    echo -e "\033[0;31mrust-ocr-worker tests failed!\033[0m"
    FAIL=1
    cd ..
fi

echo -e "\033[0;34mRunning rust-core tests...\033[0m"
if cd rust-core && run_and_log "PYO3_PYTHON=\"$VENV_PYTHON\" LD_LIBRARY_PATH=\$(\"$VENV_PYTHON\" -c \"import sysconfig; print(sysconfig.get_config_var('LIBDIR'))\"):\$LD_LIBRARY_PATH cargo test --no-default-features"; then
    echo -e "\033[0;32mrust-core tests passed!\033[0m\n"
    cd ..
else
    echo -e "\033[0;31mrust-core tests failed!\033[0m"
    FAIL=1
    cd ..
fi

echo -e "\033[0;34mRunning JavaScript tests...\033[0m"
if run_and_log "npm test"; then
    echo -e "\033[0;32mJavaScript tests passed!\033[0m\n"
else
    echo -e "\033[0;31mJavaScript tests failed!\033[0m"
    FAIL=1
fi

echo -e "\033[0;34mRunning riemann-ai backend tests...\033[0m"
if cd riemann-ai && run_and_log "$PYTEST --forked"; then
    echo -e "\033[0;32mriemann-ai tests passed!\033[0m\n"
    cd ..
else
    echo -e "\033[0;31mriemann-ai tests failed!\033[0m"
    FAIL=1
    cd ..
fi

echo -e "\033[0;34mRunning python-app tests...\033[0m"
if cd python-app && run_and_log "$PYTEST"; then
    echo -e "\033[0;32mpython-app tests passed!\033[0m\n"
    cd ..
else
    echo -e "\033[0;31mpython-app tests failed!\033[0m"
    FAIL=1
    cd ..
fi

if [ "$FAIL" -eq 0 ]; then
    echo -e "\033[0;32mAll test suites passed!\033[0m"
else
    echo -e "\033[0;31mSome tests failed. Check $LOG_FILE\033[0m"
    exit 1
fi