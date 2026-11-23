#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
export PYTHONPATH=src
export SKEIN_DB="${SKEIN_DB:-data/preview.db}"
export SKEIN_BLOB_ROOT="${SKEIN_BLOB_ROOT:-data/blobs}"
export SKEIN_TOKEN="${SKEIN_TOKEN:-dev}"
exec python -m skein
