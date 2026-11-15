#!/usr/bin/env bash
# Regenerates requirements/xreqs.txt, a Linux lock of the dependencies in
# pyproject.toml (the one list) plus the "bundled" group, with uv. The
# Windows release compiles its own lock from pyproject.toml in CI.
set -e
cd "$(dirname "$0")/.."
uv pip compile pyproject.toml \
  --extra bundled \
  --python-version 3.11 \
  --python-platform x86_64-manylinux_2_34 \
  --output-file requirements/xreqs.txt
echo "Wrote requirements/xreqs.txt"
