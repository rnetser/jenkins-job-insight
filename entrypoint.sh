#!/bin/bash
# Entrypoint for OpenShift compatibility.
# OpenShift runs containers as a random UID in GID 0. K8s subPath
# volume mounts create intermediate directories as root, making
# ~/.config non-writable. XDG_CONFIG_HOME redirects config writes
# to a writable location.

# Copy cursor credentials from PVC staging mount
if [ -d /cursor-credentials ]; then
    mkdir -p "${XDG_CONFIG_HOME:-/home/appuser/.config}/cursor"
    cp -a /cursor-credentials/. "${XDG_CONFIG_HOME:-/home/appuser/.config}/cursor/"
fi

# Resolve PORT with a default so the exec-form CMD (which cannot expand
# shell variables) gets the correct bind port at runtime.
export PORT="${PORT:-8000}"

# exec replaces the shell with the CMD process, making uvicorn PID 1
# for proper signal handling and graceful shutdown.
exec "$@" --port "$PORT"
