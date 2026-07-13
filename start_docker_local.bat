@echo off
setlocal enabledelayedexpansion

echo =====================================================================
echo    QuantumTrade Pro - Automated Local Docker Startup
echo =====================================================================
echo.

:: 1. Check Docker Daemon
echo [1/5] Checking Docker status...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker is not running. Please start Docker Desktop first.
    pause
    exit /b 1
)
echo [OK] Docker is running.
echo.

:: 2. Setup External Networks
echo [2/5] Setting up external networks...
docker network inspect trading-net >nul 2>&1
if %errorlevel% neq 0 (
    echo Creating missing Docker network: trading-net...
    docker network create trading-net
) else (
    echo [OK] Network 'trading-net' exists.
)

docker network inspect n8n_default >nul 2>&1
if %errorlevel% neq 0 (
    echo Creating missing Docker network: n8n_default...
    docker network create n8n_default
) else (
    echo [OK] Network 'n8n_default' exists.
)
echo.

:: 3. Setup Environment File
echo [3/5] Checking environment configuration...
if not exist ".env" (
    if exist ".env.example" (
        echo Creating .env file from .env.example...
        copy ".env.example" ".env" >nul
        echo [WARNING] .env has been created. Please configure your API keys in it!
    ) else (
        echo [ERROR] Neither .env nor .env.example was found.
        pause
        exit /b 1
    )
) else (
    echo [OK] .env file is present.
)
echo.

:: 4. Build Frontend
echo [4/5] Preparing frontend distribution...
if not exist "frontend\dist" (
    echo Frontend distribution folder (frontend/dist) is missing.
    echo Building the frontend app...
    
    where npm >nul 2>&1
    if %errorlevel% eq 0 (
        echo [INFO] Found local npm. Building frontend locally...
        cd frontend
        call npm install
        call npm run build
        cd ..
    ) else (
        echo [INFO] Local npm not found. Building using a Docker Node container...
        docker run --rm -v "%cd%/frontend:/app" -w /app node:18-alpine sh -c "npm install && npm run build"
    )
) else (
    echo [OK] Frontend distribution (frontend/dist) is already built.
    set /p rebuild="Would you like to rebuild the frontend anyway? (y/N): "
    if /i "!rebuild!"=="y" (
        where npm >nul 2>&1
        if !errorlevel! eq 0 (
            echo [INFO] Rebuilding frontend locally...
            cd frontend
            call npm install
            call npm run build
            cd ..
        ) else (
            echo [INFO] Rebuilding frontend using a Docker Node container...
            docker run --rm -v "%cd%/frontend:/app" -w /app node:18-alpine sh -c "npm install && npm run build"
        )
    )
)
echo.

:: 5. Start Container Stack
echo [5/5] Launching Docker Compose stack...
docker compose up -d --build
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to start Docker Compose stack.
    pause
    exit /b 1
)
echo.
echo =====================================================================
echo    🚀 QuantumTrade Pro is running locally in Docker!
echo =====================================================================
echo.
echo Access URLs:
echo   - Web Dashboard:     http://localhost:8081
echo   - Backend API Docs:  http://localhost:8001/docs
echo   - InfluxDB Console:  http://localhost:8086
echo   - MCP Server:        http://localhost:9100/mcp
echo.
echo Useful Commands:
echo   - View backend logs:   docker compose logs -f backend
echo   - View all logs:       docker compose logs -f
echo   - Stop the stack:      docker compose down
echo.
echo =====================================================================
pause
