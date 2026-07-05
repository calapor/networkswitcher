#!/usr/bin/env bash
# deploy.sh – subcommands: sync | install
# Env vars inherited from Jenkinsfile: REMOTE_USER, REMOTE_HOST, REMOTE_DIR
set -euo pipefail

: "${REMOTE_USER:?REMOTE_USER must be set}"
: "${REMOTE_HOST:?REMOTE_HOST must be set}"
: "${REMOTE_DIR:?REMOTE_DIR must be set}"

REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RSYNC_EXCLUDES=(
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='venv/'
    --exclude='.venv/'
    --exclude='stats.json'
    --exclude='settings.json'
    --exclude='.git/'
    --exclude='.context/'
)

cmd_sync() {
    echo "==> Sync: ${WORKSPACE_ROOT}/ -> ${REMOTE}:${REMOTE_DIR}/"
    ssh "${REMOTE}" "mkdir -p '${REMOTE_DIR}'"

    if command -v rsync >/dev/null 2>&1; then
        echo "   using rsync"
        rsync -az --delete "${RSYNC_EXCLUDES[@]}" -e "ssh" \
            "${WORKSPACE_ROOT}/" "${REMOTE}:${REMOTE_DIR}/"
    else
        echo "   rsync absent — falling back to tar-over-ssh (no --delete semantics)"
        local tar_excludes=()
        for ex in __pycache__ '*.pyc' venv .venv stats.json settings.json .git .context; do
            tar_excludes+=(--exclude="./${ex}")
        done
        tar -C "${WORKSPACE_ROOT}" "${tar_excludes[@]}" -czf - . \
            | ssh "${REMOTE}" "tar -C '${REMOTE_DIR}' -xzf -"
    fi
    echo "   Sync complete."
}

cmd_install() {
    echo "==> Install: running install.sh on ${REMOTE}"
    ssh "${REMOTE}" "cd '${REMOTE_DIR}' && sudo '${REMOTE_DIR}/install.sh'"
    echo "   Install complete."
}

case "${1:-}" in
    sync)    cmd_sync    ;;
    install) cmd_install ;;
    *)
        echo "Usage: deploy.sh <sync|install>" >&2
        exit 1
        ;;
esac
