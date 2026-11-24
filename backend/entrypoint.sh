#!/usr/bin/env sh
set -eu

if [ "${AUTO_MIGRATE:-true}" = "true" ]; then
  python -m cj.db.migration
fi

exec uvicorn main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8794}"
