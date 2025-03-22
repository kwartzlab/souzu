#!/bin/bash
set -euo pipefail

cd "$(dirname "$(realpath "$0")")"

GIT_DIR="$(git rev-parse --git-dir)"

for hook in pre-commit post-checkout pre-push; do
    cp .git-hook-template "$GIT_DIR/hooks/$hook"
    chmod +x "$GIT_DIR/hooks/$hook"
done
