@echo off
echo Building silicon-pool with Nuitka...

:: Ensure UV is available
where uv >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Error: uv package manager not found.
    echo Please install uv
    exit /b 1
)

:: Create build directory if it doesn't exist
if not exist "build" mkdir build

:: Run Nuitka through UV to compile main.py
uv run python -m nuitka ^
    --include-module=fastapi ^
    --include-module=uvicorn ^
    --include-module=aiohttp ^
    --include-data-dir=.\static=static ^
    --output-dir=build ^
    --standalone ^
    --windows-icon-from-ico=./static/favicon.ico ^
    main.py

echo Build completed. Check the build directory for the output.
