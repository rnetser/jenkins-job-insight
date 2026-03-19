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

# Check if any argument contains "uvicorn" to detect all uvicorn invocations
has_uvicorn=false
has_port=false
for arg in "$@"; do
    case "$arg" in
        *uvicorn*) has_uvicorn=true ;;
        --port|--port=*) has_port=true ;;
    esac
done

if [ "$has_uvicorn" = true ] && [ "$has_port" = false ]; then
    exec "$@" --port "$PORT"
else
    exec "$@"
fi
