@echo off
chcp 65001 >nul
setlocal

REM Build reusable backend base image with heavy ML dependencies.

cd /d "%~dp0"

set DOCKER_BUILDKIT=1
set COMPOSE_DOCKER_CLI_BUILD=1

echo.
echo [1/1] Building base image: yolo-backend-base:torch-cu128
docker build -f docker/backend/Dockerfile.base -t yolo-backend-base:torch-cu128 .
if errorlevel 1 (
    echo.
    echo Base image build failed.
    exit /b 1
)

echo.
echo ==========================================
echo Base image build complete.
echo Image: yolo-backend-base:torch-cu128
echo ==========================================

endlocal
pause
