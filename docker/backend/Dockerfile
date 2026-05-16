# syntax=docker/dockerfile:1
# YOLO Training Platform - Backend App Image

FROM yolo-backend-base:torch-cu128

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DEFAULT_TIMEOUT=1200

WORKDIR /app

COPY backend/requirements-runtime.txt /app/requirements-runtime.txt
COPY backend/requirements-train.txt /app/requirements-train.txt
COPY backend/requirements.txt /app/requirements.txt

# Frequently-changing business dependencies stay in their own cached layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=1200 --retries=10 \
    -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    -r /app/requirements-runtime.txt

# Optional training/plotting layer. Uncomment only when training features are needed.
# RUN --mount=type=cache,target=/root/.cache/pip \
#     pip install --default-timeout=1200 --retries=10 \
#     -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
#     -r /app/requirements-train.txt

COPY backend/src /app/src
COPY backend/run.py /app/run.py

RUN mkdir -p /app/data /app/models

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
