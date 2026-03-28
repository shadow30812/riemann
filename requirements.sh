#!/usr/bin/env bash

set -e

OUTPUT_IN="requirements.in"
CONDA_ENV_NAME="riemann"

echo "--> Initializing Conda and activating '$CONDA_ENV_NAME'..."
eval "$(conda shell.bash hook)"
conda activate "$CONDA_ENV_NAME"

echo "🔍 Generating minimal requirements.in using pip-chill..."
pip-chill | grep -vE "^riemann(==.*)?$|^riemann_app(==.*)?$" > "$OUTPUT_IN"
echo "🧹 Cleaning requirements.in (removing editable installs, comments)..."
sed -i '/^-e/d;/^$/d' requirements.in

echo "📦 Compiling requirements.txt using pip-compile..."
pip-compile --generate-hashes "$OUTPUT_IN"

echo "✅ Done!"
echo "Generated:"
echo "  - requirements.in (minimal deps)"
echo "  - requirements.txt (fully pinned deps)"