#!/bin/bash
set -euo pipefail

cd "$(dirname "$(realpath "$0")")"

uv run prek install -f
