# syntax=docker/dockerfile:1
# Supply a verified linux/arm64 image with Python >=3.11, compatible CANN,
# ais_bench and aclruntime. Missing FFmpeg is installed using openEuler DNF.
ARG ASCEND_BASE_IMAGE
FROM ${ASCEND_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ENABLE_TRAINING=false
WORKDIR /app
RUN if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then \
        dnf install -y ffmpeg && dnf clean all; \
    fi \
    && ffprobe -version >/dev/null \
    && ffmpeg -hide_banner -loglevel error -f lavfi \
        -i color=c=black:s=64x64:r=1 -frames:v 1 -c:v libx264 -f null -
COPY backend/requirements-runtime.txt backend/requirements-ascend.txt /app/
RUN python -c "import sys; assert sys.version_info >= (3, 11), 'Python >=3.11 required'" \
    && python -m pip install --no-cache-dir -r /app/requirements-ascend.txt \
    && python -c "import cv2, numpy; print('OpenCV:', cv2.__version__, 'NumPy:', numpy.__version__)"
COPY backend/src /app/src
COPY backend/tools /app/tools
COPY docker/ascend/entrypoint.sh /app/ascend-entrypoint.sh
RUN mkdir -p /app/data /app/models /app/configs
ENTRYPOINT ["/bin/bash", "/app/ascend-entrypoint.sh"]
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
