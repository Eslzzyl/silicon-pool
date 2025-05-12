#!/bin/bash
echo "Building silicon-pool with Nuitka..."

# Ensure UV is available
if ! command -v uv &> /dev/null; then
    echo "Error: uv package manager not found."
    echo "Please install uv"
    exit 1
fi

# Create build directory if it doesn't exist
if [ ! -d "build" ]; then
    mkdir build
fi

# Run Nuitka through UV to compile main.py
uv run python -m nuitka \
    --include-module=fastapi \
    --include-module=uvicorn \
    --include-module=aiohttp \
    --include-data-dir=./static=static \
    --output-dir=build \
    --standalone \
    main.py

echo "Build completed. Check the build directory for the output."
