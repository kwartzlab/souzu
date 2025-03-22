#!/bin/bash
set -euo pipefail

uv sync
uv run pytest

echo "HTML coverage report generated in htmlcov/ directory"
echo "Open htmlcov/index.html in your browser to view the report"

uv run coverage report