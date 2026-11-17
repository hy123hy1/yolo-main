#!/bin/bash
set -e

if [ "${1:-}" = "uvicorn" ]; then
    : "${METADATA_DATABASE_URL:?Set METADATA_DATABASE_URL before starting the backend}"
    : "${ZLM_SECRET:?Set ZLM_SECRET to match the ZLM config before starting the backend}"
fi

if [ -n "${ASCEND_ENV_SCRIPT:-}" ]; then
    if [ ! -f "$ASCEND_ENV_SCRIPT" ]; then
        echo "ASCEND_ENV_SCRIPT does not exist: $ASCEND_ENV_SCRIPT" >&2
        exit 1
    fi
    source "$ASCEND_ENV_SCRIPT"
fi
python -c "import platform; from ais_bench.infer.interface import InferSession; import aclruntime; assert platform.machine() in ('aarch64', 'arm64'), 'aarch64 runtime required'; print('[Ascend] SDK imported; OM/SoC compatibility still requires model verification')"
exec "$@"
