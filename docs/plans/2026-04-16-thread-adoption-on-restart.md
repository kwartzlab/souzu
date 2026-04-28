# Thread Adoption on Cancel/Restart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a print is started shortly after a previous print on the same printer was cancelled or had tracking lost (and was unclaimed), adopt the previous Slack thread instead of creating a new one — reducing channel noise from the common slicer-tweak retry pattern.

**Architecture:** Add a `previous_job` slot on `PrinterState` capturing minimal adoption metadata (channel/thread_ts, actions_ts, estimated duration, end time) when an unclaimed print ends in cancel or tracking-lost. On the next `_job_started`, check a heuristic (≤10 min since end, new duration estimate within ±10% of previous estimate). If matched, edit the top-level message and actions reply in place rather than posting a new thread; post a restart notification as a reply. The previous attempt's status replies remain as a loose audit trail.

**Tech Stack:** Python (existing), `attrs` for the new dataclass, `cattrs` for serialization, `pytest`/`pytest-asyncio` for tests.

**Decisions baked in:**
- Adoption is eligible only after **cancel** or **tracking-lost** terminal states with **no owner**. Successful completion and non-cancel failures clear `previous_job`.
- After adoption, the top-level message is rewritten to reflect the **new** attempt (`:progress_bar: <new start_message>` + claim button). The strikethrough-of-original pattern continues to apply going forward to the **new** attempt's `start_message` (matching existing behavior).
- The actions reply is edited to a non-actionable "awaiting claim" placeholder using the existing `build_terminal_actions_blocks` helper (text: `"No actions available — awaiting claim."`). On claim, the existing flow replaces it with action buttons.
- A `:repeat:` reply is posted in-thread to surface the new attempt's start details.
- `previous_job` is consumed (cleared) on every `_job_started`, regardless of whether adoption fired. Stale entries (across bot restarts) are also rejected naturally by the time-window check.

---

### Task 1: Add `PreviousJobInfo` dataclass and `previous_job` field on `PrinterState`

**Files:**
- Modify: `src/souzu/job_tracking.py` (around line 70-95, near `PrintJob` and `PrinterState`)
- Modify: `tests/souzu/test_job_tracking.py` (extend serialization round-trip test)

- [ ] **Step 1: Write failing serialization round-trip test for `PreviousJobInfo`**

Append to `tests/souzu/test_job_tracking.py`:

```python
def test_previous_job_info_serialization_round_trip() -> None:
    """Test round-trip serialization of PrinterState with previous_job populated."""
    import json

    from souzu.job_tracking import _STATE_SERIALIZER, PreviousJobInfo

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2, minutes=15),
        ended_at=datetime(2026, 4, 16, 12, 30, 0),
    )
    state = PrinterState(current_job=None, previous_job=previous)

    unstructured = _STATE_SERIALIZER.unstructure(state)
    json_str = json.dumps(unstructured)
    json_loaded = json.loads(json_str)
    restructured = _STATE_SERIALIZER.structure(json_loaded, PrinterState)

    assert restructured.previous_job is not None
    assert restructured.previous_job.slack_channel == previous.slack_channel
    assert restructured.previous_job.slack_thread_ts == previous.slack_thread_ts
    assert restructured.previous_job.actions_ts == previous.actions_ts
    assert restructured.previous_job.duration == previous.duration
    assert restructured.previous_job.ended_at == previous.ended_at


def test_previous_job_info_default_none() -> None:
    """PrinterState.previous_job defaults to None."""
    state = PrinterState()
    assert state.previous_job is None


def test_previous_job_info_excluded_from_serialization_when_none() -> None:
    """When previous_job is None, round-trip preserves it as None."""
    import json

    from souzu.job_tracking import _STATE_SERIALIZER

    state = PrinterState()
    unstructured = _STATE_SERIALIZER.unstructure(state)
    json_str = json.dumps(unstructured)
    restored = _STATE_SERIALIZER.structure(json.loads(json_str), PrinterState)
    assert restored.previous_job is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_previous_job_info_serialization_round_trip tests/souzu/test_job_tracking.py::test_previous_job_info_default_none tests/souzu/test_job_tracking.py::test_previous_job_info_excluded_from_serialization_when_none -v`
Expected: FAIL with `ImportError: cannot import name 'PreviousJobInfo'` (and `PrinterState` rejecting the kwarg).

- [ ] **Step 3: Add `PreviousJobInfo` and extend `PrinterState`**

In `src/souzu/job_tracking.py`, immediately before the `PrinterState` definition (around line 93), add:

```python
@define
class PreviousJobInfo:
    """Adoption metadata captured when an unclaimed print ends in cancel/tracking-lost.

    Used by ``_job_started`` to decide whether to re-use the previous attempt's
    Slack thread instead of starting a new one.
    """

    slack_channel: str
    slack_thread_ts: str
    actions_ts: str | None
    duration: timedelta
    ended_at: datetime
```

Then modify `PrinterState` (at `src/souzu/job_tracking.py:93-96`):

```python
@define
class PrinterState:
    current_job: PrintJob | None = None
    previous_job: PreviousJobInfo | None = None
    connection: BambuMqttConnection | None = None
```

No additional `_STATE_SERIALIZER` hooks are needed — the existing converter handles attrs classes automatically and the existing `make_dict_unstructure_fn(PrinterState, ..., connection=cattrs_override(omit=True))` will pick up the new field.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_previous_job_info_serialization_round_trip tests/souzu/test_job_tracking.py::test_previous_job_info_default_none tests/souzu/test_job_tracking.py::test_previous_job_info_excluded_from_serialization_when_none -v`
Expected: PASS.

- [ ] **Step 5: Format and run full test file to confirm no regressions**

Run: `uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py && uv run pytest tests/souzu/test_job_tracking.py -v`
Expected: PASS for all tests.

- [ ] **Step 6: Commit**

```bash
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: add PreviousJobInfo and previous_job slot on PrinterState"
```

---

### Task 2: Add `_should_adopt` heuristic helper

**Files:**
- Modify: `src/souzu/job_tracking.py` (add helper near other module-level helpers, after the `_format_*` functions, before `_ACTION_LABELS`)
- Modify: `tests/souzu/test_job_tracking.py` (add unit tests)

- [ ] **Step 1: Write failing tests for `_should_adopt`**

Append to `tests/souzu/test_job_tracking.py`:

```python
class TestShouldAdopt:
    def _make_previous(
        self,
        duration: timedelta = timedelta(hours=2),
        ended_at: datetime | None = None,
    ) -> "PreviousJobInfo":
        from souzu.job_tracking import PreviousJobInfo

        return PreviousJobInfo(
            slack_channel="C_PRINTS",
            slack_thread_ts="1111.0001",
            actions_ts=None,
            duration=duration,
            ended_at=ended_at or datetime(2026, 4, 16, 12, 0, 0),
        )

    def test_adopts_within_time_and_duration_window(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous()
        # 5 mins later, same duration → should adopt
        now = prev.ended_at + timedelta(minutes=5)
        assert _should_adopt(prev, timedelta(hours=2), now) is True

    def test_rejects_when_outside_time_window(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous()
        # 11 mins later → outside 10 min window
        now = prev.ended_at + timedelta(minutes=11)
        assert _should_adopt(prev, timedelta(hours=2), now) is False

    def test_adopts_at_time_window_boundary(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous()
        # Exactly 10 mins later → still within window
        now = prev.ended_at + timedelta(minutes=10)
        assert _should_adopt(prev, timedelta(hours=2), now) is True

    def test_adopts_within_plus_ten_percent_duration(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous(duration=timedelta(hours=2))
        now = prev.ended_at + timedelta(minutes=1)
        # +10% → 2h12m
        assert _should_adopt(prev, timedelta(hours=2, minutes=12), now) is True

    def test_adopts_within_minus_ten_percent_duration(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous(duration=timedelta(hours=2))
        now = prev.ended_at + timedelta(minutes=1)
        # -10% → 1h48m
        assert _should_adopt(prev, timedelta(hours=1, minutes=48), now) is True

    def test_rejects_when_duration_more_than_ten_percent_off(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous(duration=timedelta(hours=2))
        now = prev.ended_at + timedelta(minutes=1)
        # +20% → 2h24m
        assert _should_adopt(prev, timedelta(hours=2, minutes=24), now) is False
        # -20% → 1h36m
        assert _should_adopt(prev, timedelta(hours=1, minutes=36), now) is False

    def test_rejects_when_previous_duration_zero(self) -> None:
        from souzu.job_tracking import _should_adopt

        prev = self._make_previous(duration=timedelta(0))
        now = prev.ended_at + timedelta(minutes=1)
        assert _should_adopt(prev, timedelta(hours=1), now) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::TestShouldAdopt -v`
Expected: FAIL with `ImportError: cannot import name '_should_adopt'`.

- [ ] **Step 3: Implement `_should_adopt` and the constants it depends on**

In `src/souzu/job_tracking.py`, add module-level constants near the other timedeltas (around line 23-28):

```python
_ADOPTION_TIME_WINDOW = timedelta(minutes=10)
_ADOPTION_DURATION_TOLERANCE = 0.10
```

Then add the helper after `_format_eta` (around line 191), before `_ACTION_LABELS`:

```python
def _should_adopt(
    previous: PreviousJobInfo,
    new_duration: timedelta,
    now: datetime,
) -> bool:
    """Decide whether to re-use the previous attempt's Slack thread for a new print.

    Returns True only when the new attempt looks like a quick slicer-tweak retry of
    the previous one: started within ``_ADOPTION_TIME_WINDOW`` of the previous end,
    and with an estimated duration within ``_ADOPTION_DURATION_TOLERANCE`` of the
    previous estimate.
    """
    if now - previous.ended_at > _ADOPTION_TIME_WINDOW:
        return False
    prev_secs = previous.duration.total_seconds()
    if prev_secs <= 0:
        return False
    ratio = new_duration.total_seconds() / prev_secs
    return abs(ratio - 1.0) <= _ADOPTION_DURATION_TOLERANCE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py::TestShouldAdopt -v`
Expected: PASS for all 7 tests.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: add _should_adopt heuristic for thread re-use"
```

---

### Task 3: Add `_build_previous_job_info` extractor

**Files:**
- Modify: `src/souzu/job_tracking.py` (add helper after `_should_adopt`)
- Modify: `tests/souzu/test_job_tracking.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/souzu/test_job_tracking.py`:

```python
class TestBuildPreviousJobInfo:
    def test_returns_info_for_unclaimed_job_with_thread(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        ended_at = datetime(2026, 4, 16, 12, 0, 0)
        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel="C_PRINTS",
            slack_thread_ts="1111.0001",
            actions_ts="1111.0002",
        )
        info = _build_previous_job_info(job, ended_at)
        assert info is not None
        assert info.slack_channel == "C_PRINTS"
        assert info.slack_thread_ts == "1111.0001"
        assert info.actions_ts == "1111.0002"
        assert info.duration == timedelta(hours=2)
        assert info.ended_at == ended_at

    def test_returns_none_when_owner_set(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel="C_PRINTS",
            slack_thread_ts="1111.0001",
            owner="U_ALICE",
        )
        assert _build_previous_job_info(job, datetime(2026, 4, 16, 12, 0, 0)) is None

    def test_returns_none_when_no_thread_ts(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel="C_PRINTS",
            slack_thread_ts=None,
        )
        assert _build_previous_job_info(job, datetime(2026, 4, 16, 12, 0, 0)) is None

    def test_returns_none_when_no_channel(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel=None,
            slack_thread_ts="1111.0001",
        )
        assert _build_previous_job_info(job, datetime(2026, 4, 16, 12, 0, 0)) is None

    def test_actions_ts_can_be_none(self) -> None:
        from souzu.job_tracking import _build_previous_job_info

        job = PrintJob(
            duration=timedelta(hours=2),
            slack_channel="C_PRINTS",
            slack_thread_ts="1111.0001",
            actions_ts=None,
        )
        info = _build_previous_job_info(job, datetime(2026, 4, 16, 12, 0, 0))
        assert info is not None
        assert info.actions_ts is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::TestBuildPreviousJobInfo -v`
Expected: FAIL with `ImportError: cannot import name '_build_previous_job_info'`.

- [ ] **Step 3: Implement `_build_previous_job_info`**

In `src/souzu/job_tracking.py`, immediately after `_should_adopt`:

```python
def _build_previous_job_info(
    job: PrintJob, ended_at: datetime
) -> PreviousJobInfo | None:
    """Capture adoption metadata from a job, or None if it isn't eligible.

    A job is eligible only when it was unclaimed and has a Slack thread to adopt.
    """
    if job.owner is not None:
        return None
    if job.slack_channel is None or job.slack_thread_ts is None:
        return None
    return PreviousJobInfo(
        slack_channel=job.slack_channel,
        slack_thread_ts=job.slack_thread_ts,
        actions_ts=job.actions_ts,
        duration=job.duration,
        ended_at=ended_at,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py::TestBuildPreviousJobInfo -v`
Expected: PASS for all 5 tests.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: add _build_previous_job_info extractor"
```

---

### Task 4: Set `previous_job` on cancel; clear on non-cancel failure

**Files:**
- Modify: `src/souzu/job_tracking.py` (`_job_failed`, currently at lines 499-530)
- Modify: `tests/souzu/test_job_tracking.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/souzu/test_job_tracking.py`:

```python
@pytest.mark.asyncio
async def test_job_failed_cancel_sets_previous_job(mocker: MockerFixture) -> None:
    """A cancel on an unclaimed job populates state.previous_job."""
    from souzu.job_tracking import _job_failed

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC)
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())
    mocker.patch(
        "souzu.job_tracking.CANCELLED_ERROR_CODES",
        new={0x12345678},
    )

    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
    )
    state = PrinterState(current_job=job)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.print_error = 0x12345678

    await _job_failed(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is not None
    assert state.previous_job.slack_thread_ts == "1111.0001"
    assert state.previous_job.actions_ts == "1111.0002"
    assert state.previous_job.duration == timedelta(hours=2)
    assert state.previous_job.ended_at == fixed_now


@pytest.mark.asyncio
async def test_job_failed_cancel_does_not_set_previous_job_when_claimed(
    mocker: MockerFixture,
) -> None:
    """A cancel on a claimed job leaves previous_job as None."""
    from souzu.job_tracking import _job_failed

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC
    )
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())
    mocker.patch(
        "souzu.job_tracking.CANCELLED_ERROR_CODES",
        new={0x12345678},
    )

    job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        owner="U_ALICE",
    )
    state = PrinterState(current_job=job)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.print_error = 0x12345678

    await _job_failed(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is None


@pytest.mark.asyncio
async def test_job_failed_non_cancel_clears_previous_job(
    mocker: MockerFixture,
) -> None:
    """A non-cancel failure explicitly clears any prior previous_job."""
    from souzu.job_tracking import _job_failed, PreviousJobInfo

    mocker.patch("souzu.job_tracking.CONFIG")
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())
    mocker.patch(
        "souzu.job_tracking.CANCELLED_ERROR_CODES",
        new={0x12345678},
    )
    mocker.patch(
        "souzu.job_tracking.parse_error_code",
        return_value="some error",
    )

    job = PrintJob(duration=timedelta(hours=2))
    stale_previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="9999.0001",
        actions_ts=None,
        duration=timedelta(hours=1),
        ended_at=datetime(2026, 4, 16, 11, 0, 0),
    )
    state = PrinterState(current_job=job, previous_job=stale_previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.print_error = 0xDEADBEEF

    await _job_failed(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_job_failed_cancel_sets_previous_job tests/souzu/test_job_tracking.py::test_job_failed_cancel_does_not_set_previous_job_when_claimed tests/souzu/test_job_tracking.py::test_job_failed_non_cancel_clears_previous_job -v`
Expected: FAIL — assertions on `state.previous_job` fail because `_job_failed` doesn't touch it yet.

- [ ] **Step 3: Modify `_job_failed`**

Replace the body of `_job_failed` in `src/souzu/job_tracking.py` (lines 499-530):

```python
async def _job_failed(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
) -> None:
    assert state.current_job is not None
    if report.print_error in CANCELLED_ERROR_CODES:
        await _update_job(
            slack,
            state.current_job,
            device,
            ":heavy_minus_sign:",
            "Cancelled",
            "Print cancelled",
            actions=[],
            terminal_reason="print cancelled",
        )
        ended_at = datetime.now(tz=CONFIG.timezone)
        state.previous_job = _build_previous_job_info(state.current_job, ended_at)
        state.current_job = None
    else:
        error_message = parse_error_code(report.print_error)
        await _update_job(
            slack,
            state.current_job,
            device,
            ":x:",
            "Failed!",
            f"Print failed!\nMessage from printer: {error_message}",
            actions=[],
            terminal_reason="print failed",
        )
        state.previous_job = None
        state.current_job = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_job_failed_cancel_sets_previous_job tests/souzu/test_job_tracking.py::test_job_failed_cancel_does_not_set_previous_job_when_claimed tests/souzu/test_job_tracking.py::test_job_failed_non_cancel_clears_previous_job -v`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: capture previous_job on cancel for unclaimed prints"
```

---

### Task 5: Set `previous_job` on tracking lost; clear on completion

**Files:**
- Modify: `src/souzu/job_tracking.py` (`_job_tracking_lost` lines 553-570; `_job_completed` lines 533-550)
- Modify: `tests/souzu/test_job_tracking.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/souzu/test_job_tracking.py`:

```python
@pytest.mark.asyncio
async def test_job_tracking_lost_sets_previous_job(mocker: MockerFixture) -> None:
    """Tracking lost on an unclaimed job populates state.previous_job."""
    from souzu.job_tracking import _job_tracking_lost

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC)
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())

    job = PrintJob(
        duration=timedelta(hours=1, minutes=30),
        slack_channel="C_PRINTS",
        slack_thread_ts="2222.0001",
        actions_ts="2222.0002",
    )
    state = PrinterState(current_job=job)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)

    await _job_tracking_lost(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is not None
    assert state.previous_job.slack_thread_ts == "2222.0001"
    assert state.previous_job.duration == timedelta(hours=1, minutes=30)
    assert state.previous_job.ended_at == fixed_now


@pytest.mark.asyncio
async def test_job_tracking_lost_does_not_set_previous_job_when_claimed(
    mocker: MockerFixture,
) -> None:
    """Tracking lost on a claimed job leaves previous_job as None."""
    from souzu.job_tracking import _job_tracking_lost

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC
    )
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())

    job = PrintJob(
        duration=timedelta(hours=1, minutes=30),
        slack_channel="C_PRINTS",
        slack_thread_ts="2222.0001",
        owner="U_ALICE",
    )
    state = PrinterState(current_job=job)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)

    await _job_tracking_lost(AsyncMock(spec=SlackClient), report, state, device)

    assert state.previous_job is None


@pytest.mark.asyncio
async def test_job_completed_clears_previous_job(mocker: MockerFixture) -> None:
    """Successful completion clears any prior previous_job."""
    from souzu.job_tracking import _job_completed, PreviousJobInfo

    mocker.patch("souzu.job_tracking.CONFIG")
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())

    job = PrintJob(duration=timedelta(hours=2))
    stale_previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="9999.0001",
        actions_ts=None,
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 11, 0, 0),
    )
    state = PrinterState(current_job=job, previous_job=stale_previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)

    await _job_completed(AsyncMock(spec=SlackClient), report, state, device)

    assert state.current_job is None
    assert state.previous_job is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_job_tracking_lost_sets_previous_job tests/souzu/test_job_tracking.py::test_job_tracking_lost_does_not_set_previous_job_when_claimed tests/souzu/test_job_tracking.py::test_job_completed_clears_previous_job -v`
Expected: FAIL on the new previous_job assertions.

- [ ] **Step 3: Modify `_job_tracking_lost` and `_job_completed`**

Replace `_job_tracking_lost` body in `src/souzu/job_tracking.py` (lines 553-570):

```python
async def _job_tracking_lost(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
) -> None:
    assert state.current_job is not None
    await _update_job(
        slack,
        state.current_job,
        device,
        ":question:",
        "Tracking lost",
        "Lost tracking for print job - maybe the printer was disconnected?",
        actions=[],
        terminal_reason="print tracking lost",
    )
    ended_at = datetime.now(tz=CONFIG.timezone)
    state.previous_job = _build_previous_job_info(state.current_job, ended_at)
    state.current_job = None
```

Replace `_job_completed` body in `src/souzu/job_tracking.py` (lines 533-550):

```python
async def _job_completed(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
) -> None:
    assert state.current_job is not None
    await _update_job(
        slack,
        state.current_job,
        device,
        ":white_check_mark:",
        "Finished!",
        "Print finished!",
        actions=[],
        terminal_reason="print completed",
    )
    state.previous_job = None
    state.current_job = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_job_tracking_lost_sets_previous_job tests/souzu/test_job_tracking.py::test_job_tracking_lost_does_not_set_previous_job_when_claimed tests/souzu/test_job_tracking.py::test_job_completed_clears_previous_job -v`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: capture previous_job on tracking lost, clear on completion"
```

---

### Task 6: Implement `_adopt_thread` helper

**Files:**
- Modify: `src/souzu/job_tracking.py` (add helper after `_job_started`-related helpers, near `_update_job`)
- Modify: `tests/souzu/test_job_tracking.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/souzu/test_job_tracking.py`:

```python
@pytest.mark.asyncio
async def test_adopt_thread_edits_top_level_message_and_posts_restart_reply(
    mocker: MockerFixture,
) -> None:
    from souzu.job_tracking import PreviousJobInfo, _adopt_thread

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 4, 16, 12, 5, 0, tzinfo=pytz.UTC
    )

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    job = PrintJob(
        duration=timedelta(hours=2),
        eta=datetime(2026, 4, 16, 14, 5, 0, tzinfo=pytz.UTC),
        start_message="Test Printer: Print started, 2 hours, done around 2:05 PM",
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    mock_slack = AsyncMock(spec=SlackClient)

    await _adopt_thread(mock_slack, previous, job, device)

    # Top-level message edited with new start text + claim button
    edit_calls = mock_slack.edit_message.call_args_list
    assert len(edit_calls) == 2  # parent + actions
    parent_edit = edit_calls[0]
    assert parent_edit.args[0] == "C_PRINTS"
    assert parent_edit.args[1] == "1111.0001"
    assert ":progress_bar:" in parent_edit.args[2]
    assert "Print started" in parent_edit.args[2]
    parent_blocks = parent_edit.kwargs["blocks"]
    assert any(
        b.get("type") == "actions"
        and any(e.get("action_id") == "claim_print" for e in b.get("elements", []))
        for b in parent_blocks
    )

    # Restart reply posted in-thread
    post_calls = mock_slack.post_to_thread.call_args_list
    assert len(post_calls) == 1
    assert post_calls[0].args[0] == "C_PRINTS"
    assert post_calls[0].args[1] == "1111.0001"
    assert ":repeat:" in post_calls[0].args[2]
    assert "Test Printer" in post_calls[0].args[2]

    # Actions message edited to "awaiting claim" placeholder
    actions_edit = edit_calls[1]
    assert actions_edit.args[1] == "1111.0002"
    assert "awaiting claim" in str(actions_edit.kwargs.get("blocks", actions_edit.args))


@pytest.mark.asyncio
async def test_adopt_thread_skips_actions_edit_when_no_actions_ts(
    mocker: MockerFixture,
) -> None:
    from souzu.job_tracking import PreviousJobInfo, _adopt_thread

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts=None,
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    job = PrintJob(
        duration=timedelta(hours=2),
        eta=datetime(2026, 4, 16, 14, 5, 0, tzinfo=pytz.UTC),
        start_message="Test Printer: Print started, 2 hours, done around 2:05 PM",
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    mock_slack = AsyncMock(spec=SlackClient)

    await _adopt_thread(mock_slack, previous, job, device)

    # Only parent edit; no actions edit
    assert mock_slack.edit_message.call_count == 1


@pytest.mark.asyncio
async def test_adopt_thread_logs_and_continues_on_edit_error(
    mocker: MockerFixture,
) -> None:
    from souzu.job_tracking import PreviousJobInfo, _adopt_thread

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.timezone = pytz.UTC

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    job = PrintJob(
        duration=timedelta(hours=2),
        eta=datetime(2026, 4, 16, 14, 5, 0, tzinfo=pytz.UTC),
        start_message="Test Printer: Print started, 2 hours, done around 2:05 PM",
    )
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.edit_message.side_effect = SlackApiError("nope")
    mock_slack.post_to_thread.side_effect = SlackApiError("nope")

    mock_logging = mocker.patch("souzu.job_tracking.logging")

    await _adopt_thread(mock_slack, previous, job, device)

    # Should not raise; should log multiple errors
    assert mock_logging.error.call_count >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_adopt_thread_edits_top_level_message_and_posts_restart_reply tests/souzu/test_job_tracking.py::test_adopt_thread_skips_actions_edit_when_no_actions_ts tests/souzu/test_job_tracking.py::test_adopt_thread_logs_and_continues_on_edit_error -v`
Expected: FAIL with `ImportError: cannot import name '_adopt_thread'`.

- [ ] **Step 3: Implement `_adopt_thread`**

Add to `src/souzu/job_tracking.py`, after `_update_job` (around line 405):

```python
async def _adopt_thread(
    slack: SlackClient,
    previous: PreviousJobInfo,
    job: PrintJob,
    device: BambuDevice,
) -> None:
    """Re-use the previous attempt's Slack thread for a new print.

    Edits the top-level message to reflect the new attempt, posts a restart
    notification as a reply, and replaces the previous attempt's terminal
    actions placeholder with an "awaiting claim" placeholder for the new
    attempt. The previous attempt's status replies are intentionally left in
    place as a loose audit trail.
    """
    text = f":progress_bar: {job.start_message}"
    blocks = _build_status_blocks(text, None)
    try:
        await slack.edit_message(
            previous.slack_channel,
            previous.slack_thread_ts,
            text,
            blocks=blocks,
        )
    except SlackApiError as e:
        logging.error(f"Failed to edit adopted message: {e}")

    eta_str = _format_eta(job.eta) if job.eta is not None else "unknown"
    restart_text = (
        f":repeat: {device.device_name}: Print restarted, "
        f"{_format_duration(job.duration)}, done around {eta_str}"
    )
    try:
        await slack.post_to_thread(
            previous.slack_channel,
            previous.slack_thread_ts,
            restart_text,
        )
    except SlackApiError as e:
        logging.error(f"Failed to post restart notification: {e}")

    if previous.actions_ts is not None:
        try:
            await slack.edit_message(
                previous.slack_channel,
                previous.actions_ts,
                "No actions available — awaiting claim.",
                blocks=build_terminal_actions_blocks("awaiting claim"),
            )
        except SlackApiError as e:
            logging.error(f"Failed to update actions message on adoption: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_adopt_thread_edits_top_level_message_and_posts_restart_reply tests/souzu/test_job_tracking.py::test_adopt_thread_skips_actions_edit_when_no_actions_ts tests/souzu/test_job_tracking.py::test_adopt_thread_logs_and_continues_on_edit_error -v`
Expected: PASS.

- [ ] **Step 5: Format and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: add _adopt_thread helper for re-using cancelled-print threads"
```

---

### Task 7: Wire adoption into `_job_started`

**Files:**
- Modify: `src/souzu/job_tracking.py` (`_job_started`, lines 408-451)
- Modify: `tests/souzu/test_job_tracking.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/souzu/test_job_tracking.py`:

```python
@pytest.mark.asyncio
async def test_job_started_adopts_when_heuristic_matches(
    mocker: MockerFixture,
) -> None:
    """When previous_job matches the heuristic, _job_started adopts the thread."""
    from souzu.job_tracking import PreviousJobInfo, _job_started

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 12, 5, 0, tzinfo=pytz.UTC)
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now

    mock_adopt = AsyncMock()
    mocker.patch("souzu.job_tracking._adopt_thread", new=mock_adopt)

    mock_slack = AsyncMock(spec=SlackClient)

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    state = PrinterState(previous_job=previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 120  # 2 hours
    job_registry: dict[str, PrinterState] = {}

    await _job_started(mock_slack, report, state, device, job_registry)

    # Adoption was used — no fresh post_to_channel
    mock_slack.post_to_channel.assert_not_called()
    # _adopt_thread was called with the previous info
    assert mock_adopt.call_count == 1
    # Current job carries forward the adopted thread metadata
    assert state.current_job is not None
    assert state.current_job.slack_channel == "C_PRINTS"
    assert state.current_job.slack_thread_ts == "1111.0001"
    assert state.current_job.actions_ts == "1111.0002"
    # previous_job is consumed
    assert state.previous_job is None
    # Job registry is updated
    assert "1111.0001" in job_registry


@pytest.mark.asyncio
async def test_job_started_falls_back_to_fresh_when_outside_heuristic(
    mocker: MockerFixture,
) -> None:
    """When previous_job exists but heuristic rejects, post a fresh thread."""
    from souzu.job_tracking import PreviousJobInfo, _job_started

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 13, 0, 0, tzinfo=pytz.UTC)  # 1h after end
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now

    mock_adopt = AsyncMock()
    mocker.patch("souzu.job_tracking._adopt_thread", new=mock_adopt)

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "fresh.thread.ts"

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    state = PrinterState(previous_job=previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 120
    job_registry: dict[str, PrinterState] = {}

    await _job_started(mock_slack, report, state, device, job_registry)

    # Fresh thread used; adoption not called
    mock_adopt.assert_not_called()
    mock_slack.post_to_channel.assert_called_once()
    # previous_job is still consumed
    assert state.previous_job is None
    # Current job has the new thread ts
    assert state.current_job is not None
    assert state.current_job.slack_thread_ts == "fresh.thread.ts"


@pytest.mark.asyncio
async def test_job_started_consumes_previous_job_even_when_no_adoption(
    mocker: MockerFixture,
) -> None:
    """Confirm previous_job is cleared when heuristic mismatches (duration)."""
    from souzu.job_tracking import PreviousJobInfo, _job_started

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    fixed_now = datetime(2026, 4, 16, 12, 5, 0, tzinfo=pytz.UTC)
    mocker.patch("souzu.job_tracking.datetime").now.return_value = fixed_now

    mocker.patch("souzu.job_tracking._adopt_thread", new=AsyncMock())

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "fresh.thread.ts"

    previous = PreviousJobInfo(
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
        duration=timedelta(hours=2),  # 120 mins
        ended_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC),
    )
    state = PrinterState(previous_job=previous)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 60  # 50% of previous → mismatch
    job_registry: dict[str, PrinterState] = {}

    await _job_started(mock_slack, report, state, device, job_registry)

    assert state.previous_job is None


@pytest.mark.asyncio
async def test_job_started_with_no_previous_job_uses_fresh_thread(
    mocker: MockerFixture,
) -> None:
    """Existing fresh-thread behavior is preserved when no previous_job exists."""
    from souzu.job_tracking import _job_started

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC
    mocker.patch("souzu.job_tracking.datetime").now.return_value = datetime(
        2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC
    )

    mock_adopt = AsyncMock()
    mocker.patch("souzu.job_tracking._adopt_thread", new=mock_adopt)

    mock_slack = AsyncMock(spec=SlackClient)
    mock_slack.post_to_channel.return_value = "1234.5678"

    state = PrinterState()
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"
    report = MagicMock(spec=BambuStatusReport)
    report.mc_remaining_time = 60

    await _job_started(mock_slack, report, state, device, {})

    mock_adopt.assert_not_called()
    mock_slack.post_to_channel.assert_called_once()
    assert state.current_job is not None
    assert state.current_job.slack_thread_ts == "1234.5678"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_job_started_adopts_when_heuristic_matches tests/souzu/test_job_tracking.py::test_job_started_falls_back_to_fresh_when_outside_heuristic tests/souzu/test_job_tracking.py::test_job_started_consumes_previous_job_even_when_no_adoption tests/souzu/test_job_tracking.py::test_job_started_with_no_previous_job_uses_fresh_thread -v`
Expected: FAIL — `_job_started` doesn't yet consult `previous_job`.

- [ ] **Step 3: Modify `_job_started`**

Replace `_job_started` body in `src/souzu/job_tracking.py` (lines 408-451):

```python
async def _job_started(
    slack: SlackClient,
    report: BambuStatusReport,
    state: PrinterState,
    device: BambuDevice,
    job_registry: JobRegistry,
) -> None:
    assert report.mc_remaining_time is not None
    duration = timedelta(minutes=report.mc_remaining_time)
    now = datetime.now(tz=CONFIG.timezone)
    eta = now + duration
    start_message = (
        f"{device.device_name}: Print started, {_format_duration(duration)}, "
        f"done around {_format_eta(eta)}"
    )

    previous = state.previous_job
    state.previous_job = None  # Consumed regardless of adoption outcome

    job = PrintJob(
        duration=duration,
        eta=eta,
        state=JobState.RUNNING,
        start_message=start_message,
    )

    if previous is not None and _should_adopt(previous, duration, now):
        job.slack_channel = previous.slack_channel
        job.slack_thread_ts = previous.slack_thread_ts
        job.actions_ts = previous.actions_ts
        await _adopt_thread(slack, previous, job, device)
    else:
        claim_blocks = _build_status_blocks(f":progress_bar: {start_message}", None)
        try:
            thread_ts = await slack.post_to_channel(
                CONFIG.slack.print_notification_channel,
                f":progress_bar: {job.start_message}",
                blocks=claim_blocks,
            )
            job.slack_channel = CONFIG.slack.print_notification_channel
            job.slack_thread_ts = thread_ts
        except SlackApiError as e:
            logging.error(f"Failed to notify channel: {e}")

    state.current_job = job
    if job.slack_thread_ts is not None:
        job_registry[job.slack_thread_ts] = state
        actions = available_actions(job)
        if actions:
            action_blocks = build_actions_blocks(actions)
            try:
                actions_ts = await slack.post_to_thread(
                    CONFIG.slack.print_notification_channel,
                    job.slack_thread_ts,
                    "Actions",
                    blocks=action_blocks,
                )
                job.actions_ts = actions_ts
            except SlackApiError as e:
                logging.error(f"Failed to post actions message: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_job_started_adopts_when_heuristic_matches tests/souzu/test_job_tracking.py::test_job_started_falls_back_to_fresh_when_outside_heuristic tests/souzu/test_job_tracking.py::test_job_started_consumes_previous_job_even_when_no_adoption tests/souzu/test_job_tracking.py::test_job_started_with_no_previous_job_uses_fresh_thread -v`
Expected: PASS.

- [ ] **Step 5: Run full test file to confirm no regressions**

Run: `uv run pytest tests/souzu/test_job_tracking.py -v`
Expected: ALL PASS. In particular, `test_job_started_no_actions_for_unclaimed` (existing) must still pass — its setup uses `state = PrinterState()` (no previous_job) so adoption is skipped, original fresh-thread path runs.

- [ ] **Step 6: Format and commit**

```bash
uv run ruff format src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git add src/souzu/job_tracking.py tests/souzu/test_job_tracking.py
git commit -m "feat: adopt previous Slack thread on quick cancel/restart"
```

---

### Task 8: End-to-end integration test for cancel → restart adoption

**Files:**
- Modify: `tests/souzu/test_job_tracking.py`

- [ ] **Step 1: Write the integration test**

Append to `tests/souzu/test_job_tracking.py`:

```python
@pytest.mark.asyncio
async def test_cancel_then_restart_within_window_adopts_thread(
    mocker: MockerFixture,
) -> None:
    """Integration: a cancelled unclaimed print followed by a similar restart adopts."""
    from souzu.job_tracking import _job_failed, _job_started

    mock_config = mocker.patch("souzu.job_tracking.CONFIG")
    mock_config.slack.print_notification_channel = "C_PRINTS"
    mock_config.timezone = pytz.UTC

    cancel_time = datetime(2026, 4, 16, 12, 0, 0, tzinfo=pytz.UTC)
    restart_time = datetime(2026, 4, 16, 12, 3, 0, tzinfo=pytz.UTC)
    fake_dt = mocker.patch("souzu.job_tracking.datetime")
    fake_dt.now.return_value = cancel_time

    mocker.patch(
        "souzu.job_tracking.CANCELLED_ERROR_CODES",
        new={0x12345678},
    )
    mocker.patch("souzu.job_tracking._update_job", new=AsyncMock())

    mock_slack = AsyncMock(spec=SlackClient)

    # Existing unclaimed job to be cancelled
    existing_job = PrintJob(
        duration=timedelta(hours=2),
        slack_channel="C_PRINTS",
        slack_thread_ts="1111.0001",
        actions_ts="1111.0002",
    )
    state = PrinterState(current_job=existing_job)
    device = MagicMock(spec=BambuDevice)
    device.device_name = "Test Printer"

    cancel_report = MagicMock(spec=BambuStatusReport)
    cancel_report.print_error = 0x12345678
    await _job_failed(mock_slack, cancel_report, state, device)

    assert state.previous_job is not None
    assert state.previous_job.slack_thread_ts == "1111.0001"

    # Advance the clock to the restart time
    fake_dt.now.return_value = restart_time

    # Now a new print starts at the same printer with the same estimated duration
    start_report = MagicMock(spec=BambuStatusReport)
    start_report.mc_remaining_time = 120  # 2 hours, matches previous
    job_registry: dict[str, PrinterState] = {}

    await _job_started(mock_slack, start_report, state, device, job_registry)

    # No fresh post_to_channel — we adopted
    mock_slack.post_to_channel.assert_not_called()
    # Top-level message edited (parent + actions = 2 edits)
    assert mock_slack.edit_message.call_count == 2
    parent_edit = mock_slack.edit_message.call_args_list[0]
    assert parent_edit.args[1] == "1111.0001"
    # Restart reply posted in-thread
    post_calls = mock_slack.post_to_thread.call_args_list
    assert any(":repeat:" in str(c.args[2]) for c in post_calls if len(c.args) >= 3)

    assert state.current_job is not None
    assert state.current_job.slack_thread_ts == "1111.0001"
    assert state.previous_job is None
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/souzu/test_job_tracking.py::test_cancel_then_restart_within_window_adopts_thread -v`
Expected: PASS.

- [ ] **Step 3: Run full test suite to ensure nothing else regressed**

Run: `uv run pytest -v`
Expected: ALL PASS.

- [ ] **Step 4: Run linters and type checkers**

Run: `uv run ruff check src/souzu/job_tracking.py tests/souzu/test_job_tracking.py && uv run ruff format --check src/souzu/job_tracking.py tests/souzu/test_job_tracking.py && uv run mypy src/souzu/job_tracking.py && uv run pyright src/souzu/job_tracking.py`
Expected: All clean.

- [ ] **Step 5: Commit**

```bash
git add tests/souzu/test_job_tracking.py
git commit -m "test: add integration test for cancel→restart thread adoption"
```

---

## Self-review notes

Coverage of design points from the conversation:
- ✅ Heuristic: <10 min + ±10% duration + previous unclaimed (Task 2)
- ✅ Adopt = edit top-level message in place (Task 6, `_adopt_thread`)
- ✅ Adopt = edit actions reply (Task 6)
- ✅ All other replies preserved as audit trail (no deletion logic anywhere)
- ✅ Triggers: cancel + tracking-lost only (Tasks 4, 5)
- ✅ Excluded: completion + non-cancel failures clear `previous_job` (Tasks 4, 5)
- ✅ Persisted across bot restarts via existing `_STATE_SERIALIZER` flow (Task 1)
- ✅ Stale `previous_job` after restart is naturally rejected by time-window check (no special handling needed)

No placeholders. All function signatures and field names referenced consistently across tasks (`PreviousJobInfo`, `_should_adopt`, `_build_previous_job_info`, `_adopt_thread`, `previous_job`, `_ADOPTION_TIME_WINDOW`, `_ADOPTION_DURATION_TOLERANCE`).
