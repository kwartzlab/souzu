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
- When testing coroutines, be careful with AsyncMock as it can lead to "coroutine never awaited" warnings

### Testing Asynchronous Workflows
- Use explicit try/except blocks for testing exception handling in async code
- Break complex async workflows into smaller testable components
- Test both success and error paths through async workflows
- For complex loops, create tests that simulate each step independently
- Use AsyncMock for mocking async functions, but be aware of "coroutine never awaited" warnings
- Prefer regular Mock for event.set() and similar calls to avoid coroutine warnings
- Test async signal handlers and event loops thoroughly

#### Example: Testing Signal Handlers
```python
@pytest.mark.asyncio
async def test_signal_handlers():
    """Test that signal handlers correctly trigger the exit event."""
    # Create a mock asyncio loop that captures signal handlers
    mock_loop = Mock()
    signal_handlers = {}
    
    def mock_add_handler(sig, handler, *args):
        signal_handlers[sig] = (handler, args)
    
    mock_loop.add_signal_handler.side_effect = mock_add_handler
    
    with patch("module.get_running_loop", return_value=mock_loop):
        # Run the function that sets up signal handlers
        await my_async_function()
        
        # Verify signal handlers were registered for the right signals
        assert signal.SIGINT in signal_handlers
        assert signal.SIGTERM in signal_handlers
        
        # Call the handler manually to test it works
        handler, args = signal_handlers[signal.SIGINT]
        handler(*args)
        
        # Verify the handler did the right thing
        assert expected_action_happened
```

#### Example: Testing Asynchronous Workflows
```python
@pytest.mark.asyncio
async def test_async_task_processing():
    """Test an async workflow by simulating its steps."""
    # Create test data
    test_item = create_test_item()
    
    # Mock or simulate the async components
    mock_queue = AsyncMock()
    mock_queue.get.return_value = test_item
    
    # Mock the async resources
    mock_resource = AsyncMock()
    
    # Test the actual processing logic
    try:
        result = await process_item(test_item, mock_resource)
        
        # Verify the result
        assert result.status == "success"
        assert mock_resource.process.called_once_with(test_item)
    except Exception:
        # Verify exception handling if applicable
        assert mock_resource.cleanup.called
```

### Best Practices for Serialization Tests
- Test serialization with actual round-trip tests (object → serialized → object)
- Avoid mocking serializers; use the real serialization functions
- Test with both valid and invalid/edge case inputs
- When testing file persistence, use temporary files with AsyncPath
- Verify all key fields survive serialization round-trips

#### Example: Testing Object Serialization Round-Trip
```python
def test_serialization_round_trip() -> None:
    """Test complete serialization cycle for an object."""
    import json
    
    # Create an object with various data types
    original_obj = MyClass(
        int_field=42,
        str_field="test",
        enum_field=MyEnum.VALUE,
        date_field=datetime(2023, 1, 1, 12, 0, 0),
    )
    
    # Step 1: Unstructure to dictionary using the serializer
    dict_data = SERIALIZER.unstructure(original_obj)
    
    # Step 2: Convert to JSON
    json_str = json.dumps(dict_data)
    
    # Step 3: Convert back from JSON
    json_loaded = json.loads(json_str)
    
    # Step 4: Structure back to object using the serializer
    restored_obj = SERIALIZER.structure(json_loaded, MyClass)
    
    # Verify the round trip worked correctly
    assert restored_obj.int_field == original_obj.int_field
    assert restored_obj.str_field == original_obj.str_field
    assert restored_obj.enum_field == original_obj.enum_field
    assert restored_obj.date_field == original_obj.date_field
```

#### Example: Testing File Persistence with AsyncPath
```python
@pytest.mark.asyncio
async def test_file_persistence() -> None:
    """Test saving and loading objects from files."""
    from tempfile import TemporaryDirectory
    from anyio import Path as AsyncPath
    
    # Create temporary directory
    with TemporaryDirectory() as temp_dir:
        # Create AsyncPath for async file operations
        temp_file = AsyncPath(temp_dir) / "test_data.json"
        
        # Create test data
        original_data = MyClass(field1="value1", field2=42)
        
        # Serialize and save
        serialized = json.dumps(SERIALIZER.unstructure(original_data))
        async with await temp_file.open('w') as f:
            await f.write(serialized)
            
        # Load and deserialize
        async with await temp_file.open('r') as f:
            content = await f.read()
            loaded_dict = json.loads(content)
            restored_data = SERIALIZER.structure(loaded_dict, MyClass)
            
        # Verify data matches
        assert restored_data.field1 == original_data.field1
        assert restored_data.field2 == original_data.field2
```

### Testing Network Services and Discovery
- Use clean, instance-specific queues for discovery tests
- Create factory functions for frozen class objects to simplify testing
- Test response and request handling directly through handler methods
- Test both successful discoveries and failure scenarios
- Simulate device/service discovery through direct handler invocation
- Test duplicate detection and filtering mechanisms
- Mock socket communication but verify actual message handling
- Test timeout behaviors and network error cases
- Prefer testing the actual business logic over mocking handler functions

#### Example: Network Discovery Testing
```python
@pytest.mark.asyncio
async def test_discovery_flow() -> None:
    """Test integrated discovery flow with simulated device discovery."""
    # Create clean objects for this test
    discovery_queue = Queue[Device]()
    discovery = Discovery(discovery_queue)
    
    # Simulate device discovery by directly calling message handlers
    discovery.handle_message([
        ("Type", "device"),
        ("ID", "DEVICE101"),
        ("Name", "Device One"),
        ("Address", "192.168.1.101"),
    ])
    
    # Verify device was added to the queue
    assert discovery_queue.qsize() == 1
    device = discovery_queue.get_nowait()
    
    # Verify device properties
    assert device.id == "DEVICE101"
    assert device.name == "Device One"
    assert device.address == "192.168.1.101"
    
    # Test duplicate detection
    discovery.handle_message([
        ("Type", "device"),
        ("ID", "DEVICE101"),  # Same ID should be detected as duplicate
        ("Name", "Device One"),
        ("Address", "192.168.1.101"),
    ])
    
    # Verify no duplicate was added
    assert discovery_queue.empty()
```

### Avoiding Over-Mocking
- Only mock external dependencies or slow operations, not core functionality
- Test the actual behavior, not just that functions were called
- Assert on parameter values passed to mocked functions
- Use partial mocks instead of complete mocks when possible
- When testing multiple components, do both integration tests and unit tests
- For file operations, use temporary directories instead of mocking file access
- Combine real serialization with mocked file operations for hybrid tests

#### Example: Problematic Over-Mocking
```python
# BAD: Over-mocking core functionality
def test_process_data_over_mocked():
    serializer = MagicMock()
    serializer.unstructure.return_value = {"mocked": "data"}
    file_handler = MagicMock()
    
    process_data(input_data, serializer, file_handler)
    
    # Only verifies calls happened, not that they work correctly
    assert serializer.unstructure.called
    assert file_handler.save.called
```

#### Example: Better Balanced Mocking
```python
# GOOD: Mock external dependencies but use real core functionality
def test_process_data_balanced():
    # Use real serializer
    test_data = MyClass(field1="test", field2=42)
    
    # Mock only the file operations
    mock_file = MagicMock()
    
    with patch("mymodule.open", mock_file):
        process_data(test_data)
    
    # Verify the actual content that would be written
    written_data = mock_file().__enter__().write.call_args[0][0]
    loaded_data = json.loads(written_data)
    
    # Check that serialization worked correctly
    assert loaded_data["field1"] == "test"
    assert loaded_data["field2"] == 42
```

#### Example: Best Integration Test
```python
# BEST: Use temporary files for a full integration test
def test_process_data_integration():
    with TemporaryDirectory() as temp_dir:
        temp_file = Path(temp_dir) / "output.json"
        
        # Use real objects and real file operations
        test_data = MyClass(field1="test", field2=42)
        process_data(test_data, output_file=str(temp_file))
        
        # Verify the file was created with correct content
        assert temp_file.exists()
        with open(temp_file) as f:
            loaded_data = json.load(f)
            
        assert loaded_data["field1"] == "test"
        assert loaded_data["field2"] == 42
```
