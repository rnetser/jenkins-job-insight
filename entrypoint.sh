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

# If the caller already passed --port, don't append a duplicate.
if [[ " $* " == *" --port "* ]]; then
    exec "$@"
fi

# Only inject --port for the default uvicorn command; pass other
# commands (e.g. "docker run … bash") through unchanged.
if [[ "$1" == "uv" && "$2" == "run" && "$4" == "uvicorn" ]]; then
    exec "$@" --port "$PORT"
fi

exec "$@"
