# Souzu Commands and Guidelines

## Memory Policy
After each session working with this repository, update this file with any important learnings about commands, code patterns, or organizational structures that would be helpful for future sessions.

## Build & Development Commands
- `uv run souzu` - Run from source tree
- `./build.sh -i` - Build and install locally
- `./build.sh -p host` - Build and push to remote host
- `./install-hooks.sh` - Install git pre-commit hooks

## UV Package Management
- `uv sync` - Update the project's environment based on pyproject.toml/uv.lock
- `uv add <package>` - Add dependency to the project and install it
- `uv remove <package>` - Remove dependency from the project
- `uv lock` - Update the project's lockfile without modifying the environment
- `uv lock --upgrade-package <package>` - Upgrade a specific package while keeping others locked

## Linting & Type Checking
- `uv run ruff check` - Run linter
- `uv run ruff format` - Format code
- `uv run mypy` - Type check with mypy
- `uv run pyright` - Type check with pyright

## Testing
- `uv run pytest` - Run tests
- `uv run pytest tests/souzu/test_logs.py -v` - Run specific test file with verbose output
- `uv run pre-commit run --all-files` - Run all pre-commit checks

## Code Coverage
- `uv run pytest` - Run tests with coverage (reports configured in pyproject.toml)
- `./run-tests-with-coverage.sh` - Run tests with coverage and generate reports
- `uv run coverage report` - Show coverage summary in terminal
- `uv run coverage html` - Generate HTML coverage report (saved to htmlcov/)
- `uv run coverage xml` - Generate XML coverage report (for CI tools)

## Code Style Guidelines
- Type hints required for all functions (`disallow_untyped_defs = true`)
- Line length: 88 characters
- Use attrs library for data classes with `@frozen` decorator
- Exception handling: Use specific error types, log errors with appropriate context
- Async code: Properly handle async functions (avoid "truthy-bool" errors)
- Package imports organized alphabetically (enforced by ruff's "I" rule)
- Prefer dataclasses/attrs for structured data
- Follow proper error handling patterns in async code
- Document public functions with docstrings
- Use trailing commas in multi-line sequences
- Comments should explain WHY or non-obvious HOW, not WHAT the code does
- After writing code, look back over the code you added to review and remove unnecessary comments

## Testing Patterns
- Use `pytest-asyncio` for testing async functions
- Set `asyncio_mode = "strict"` and `asyncio_default_fixture_loop_scope = "function"` in pytest config
- Use `AsyncPath` from `anyio` for async file operations in tests
- Properly type annotate async functions and generators in tests
- Mock `__aenter__` and `__anext__` methods for async context managers and iterators
- Use `pytest.mark.asyncio` decorator for async test functions
