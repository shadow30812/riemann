#!/usr/bin/env bash

# ==============================================================================
# Riemann AI Sidecar Packaging Script
# 
# Description:
#   This script automates the creation of a standalone Conda environment for the 
#   Riemann AI sidecar. It utilizes `conda-pack` to bundle the environment and 
#   places the executable runtime inside the main application's assets directory.
#
# Usage:
#   ./build_ai.sh
# ==============================================================================

set -e
cd "$(dirname "$0")"

echo "========================================"
echo " Riemann AI Sidecar Packaging Script"
echo "========================================"

CONDA_ENV_NAME="rmai"
DESTINATION_ASSETS_DIR="../python-app/riemann/assets/riemann_ai_engine"

echo "--> Initializing Conda and activating '$CONDA_ENV_NAME'..."
eval "$(conda shell.bash hook)"
conda activate "$CONDA_ENV_NAME"

echo "--> Installing conda-pack..."
conda install -y -c conda-forge conda-pack

echo "--> Cleaning up previous builds..."
rm -rf "$DESTINATION_ASSETS_DIR"
mkdir -p "$DESTINATION_ASSETS_DIR"

echo "--> Packing Conda Environment (Uncompressed)..."
conda pack -n "$CONDA_ENV_NAME" -o rmai_env.tar --ignore-missing-files

echo "--> Extracting environment to assets..."
mkdir -p "$DESTINATION_ASSETS_DIR/env"
tar -xf rmai_env.tar -C "$DESTINATION_ASSETS_DIR/env"
rm rmai_env.tar

echo "--> Copying source files..."
cp main.py "$DESTINATION_ASSETS_DIR/"

echo "========================================"
echo " Success! The AI engine is bundled at:"
echo " $DESTINATION_ASSETS_DIR"
echo "========================================"