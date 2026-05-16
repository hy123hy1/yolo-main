@echo off
chcp 65001 >nul
setlocal

REM Build backend app image on top of the reusable base image.

cd /d "%~dp0"

set DOCKER_BUILDKIT=1
set COMPOSE_DOCKER_CLI_BUILD=1

echo.
echo [1/2] Checking base image: yolo-backend-base:torch-cu128
docker image inspect yolo-backend-base:torch-cu128 >nul 2>nul
if errorlevel 1 (
    echo Base image not found.
    echo Please run build-backend-base.bat first.
    exit /b 1
)

echo.
echo [2/2] Building backend app image via docker compose
docker compose -f docker/docker-compose.yml build backend
if errorlevel 1 (
    echo.
    echo Backend app image build failed.
    exit /b 1
)

echo.
echo ==========================================
echo Backend app image build complete.
echo ==========================================

endlocal
pause
