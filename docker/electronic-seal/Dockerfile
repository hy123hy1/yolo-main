# syntax=docker/dockerfile:1

FROM yolo-backend-base:torch-cu128

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DEFAULT_TIMEOUT=1200

WORKDIR /app

COPY backend/requirements-runtime.txt /app/requirements-runtime.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=1200 --retries=10 \
    -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple \
    -r /app/requirements-runtime.txt

COPY backend/src /app/src

RUN mkdir -p /app/data/electronic_seal /app/configs

EXPOSE 9001

CMD ["uvicorn", "src.electronic_seal.main:app", "--host", "0.0.0.0", "--port", "9001"]
