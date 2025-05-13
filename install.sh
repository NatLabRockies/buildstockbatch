#!/bin/bash
set -e

# Default to no extras
EXTRAS=""
DEV_MODE=false

# Check if an argument was provided
if [ $# -gt 0 ]; then
  # Validate the argument
  if [ "$1" = "dev" ] || [ "$1" = "aws" ] || [ "$1" = "gcp" ]; then
    EXTRAS="[$1]"
    echo "Installing buildstockbatch with $1 extras..."

    # Set dev mode flag if dev option is selected
    if [ "$1" = "dev" ]; then
      DEV_MODE=true
    fi
  else
    echo "Error: Invalid argument. Use 'dev', 'aws', or 'gcp'."
    exit 1
  fi
else
  echo "Installing buildstockbatch without extras..."
fi

# Install buildstockbatch from the current directory with optional extras
pip install -e .$EXTRAS

# Install pre-commit hooks if in dev mode
if [ "$DEV_MODE" = true ]; then
  echo "Setting up pre-commit hooks for development..."
  pre-commit install
fi

echo "Cloning ResStock repository for postprocessing module..."
# Clone ResStock repository with sparse checkout for postprocessing
mkdir -p ../resstock-src
git clone \
  --depth 1 \
  --filter=blob:none \
  --sparse \
  --branch resstockpostproc \
  https://github.com/NREL/resstock.git ../resstock-src

# Set sparse-checkout to only get the postprocessing directory
git -C ../resstock-src sparse-checkout set postprocessing

echo "Installing ResStock postprocessing module..."
# Install the postprocessing module
pip install --no-cache-dir ../resstock-src/postprocessing

echo "Installation complete!"
