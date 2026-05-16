# Backend Image Strategy

## Goal

Avoid re-downloading the heaviest Python dependencies every time the backend
business code or small runtime dependencies change.

## Images

### Base image

Build once and reuse:

```powershell
docker build -f docker/backend/Dockerfile.base -t yolo-backend-base:torch-cu128 .
```

This image contains:

- system libraries
- `torch`
- `torchvision`
- AI runtime dependencies from `backend/requirements-ml.txt`

### App image

Build the regular backend image:

```powershell
docker compose -f docker/docker-compose.yml build backend
```

This image adds:

- runtime/business dependencies from `backend/requirements-runtime.txt`
- backend source code

## BuildKit cache

Both Dockerfiles use:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip ...
```

This keeps pip cache between builds when BuildKit is enabled.

## Recommended environment

On Windows PowerShell:

```powershell
$env:DOCKER_BUILDKIT=1
$env:COMPOSE_DOCKER_CLI_BUILD=1
```

Or use the included helper scripts:

```powershell
.\build-backend-base.bat
.\build-backend-app.bat
```

## Optional training layer

Training and chart dependencies are separated in:

- `backend/requirements-train.txt`

Keep them disabled by default in the app image. Only enable them when online
training or training charts are actually needed.
