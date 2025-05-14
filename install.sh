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

echo "Checking for existing ResStock repository..."
RESSTOCK_PATH="../resstock"
INSTALL_POSTPROCESSING=false

# Function to check if a path has a valid postprocessing folder
check_postprocessing() {
  local path=$1
  if [ -d "$path" ] && [ -d "$path/postprocessing" ]; then
    RESSTOCK_PATH="$path"
    INSTALL_POSTPROCESSING=true
    return 0
  fi
  return 1
}

# Check if default resstock folder exists and has postprocessing
if check_postprocessing "$RESSTOCK_PATH"; then
  echo "Found existing ResStock folder with postprocessing"
else
  # Either folder doesn't exist or doesn't have postprocessing
  if [ -d "$RESSTOCK_PATH" ]; then
    echo "WARNING: Found an older version of ResStock without postprocessing folder."
  else
    echo "ResStock folder not found."
  fi

  # Ask for alternative path
  read -p "Enter path to ResStock with postprocessing (or press Enter to skip): " ALT_PATH

  if [ -z "$ALT_PATH" ]; then
    echo "No path provided. Skipping installation of postprocessing related library to buildstockbatch."
  elif [ ! -d "$ALT_PATH" ]; then
    echo "WARNING: The provided path '$ALT_PATH' does not exist."
    echo "Skipping installation of postprocessing related library to buildstockbatch."
  elif [ ! -d "$ALT_PATH/postprocessing" ]; then
    echo "WARNING: The provided path '$ALT_PATH' does not contain a postprocessing folder."
    echo "Skipping installation of postprocessing related library to buildstockbatch."
  else
    check_postprocessing "$ALT_PATH"
    echo "Found postprocessing folder in provided path"
  fi
fi

# Install postprocessing if a valid path was found
if [ "$INSTALL_POSTPROCESSING" = true ]; then
  echo "Installing ResStock postprocessing module from $RESSTOCK_PATH/postprocessing..."
  pip install --no-cache-dir "$RESSTOCK_PATH/postprocessing"
fi

echo "Installation complete!"
