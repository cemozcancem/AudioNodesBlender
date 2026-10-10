"""
Audio device switching tests — verifies the fix that landed in v3.0.2 so that
`set_device` actually applies the chosen input/output device.

Before the fix, switching from one device to another reused the prior
device's sample rate / buffer size / channel masks, which on macOS often
made JUCE silently keep the old device when those values were
incompatible (e.g. BlackHole at 48 kHz → MacBook speakers at 44.1 kHz).
These tests prove that:

  1. `set_device` for both input and output returns `status=ok`.
  2. `get_devices` reflects the newly-selected device.
  3. The audio callback keeps running across the switch.
  4. Switching back works.
  5. Asking for a non-existent device returns `status=error`, not `ok`.
  6. The engine logs that it re-negotiated sample rate / buffer size for
     the new device — proves the new code path is actually exercised.

Run:
    cd tests && python -m pytest test_devices.py -v
"""

import time

import pytest

from harness import Engine, ENGINE_BINARY

# Switching devices needs real audio hardware.
pytestmark = pytest.mark.needs_audio


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    log_path = tmp_path_factory.mktemp("engine_logs") / "engine.log"
    with Engine(log_path=log_path) as e:
        # Stash the log path so tests can inspect engine diagnostics.
        e.log_path = log_path
        yield e


def _pick_alternative(devices, current):
    """Return a device name from `devices` that is not `current`, or None."""
    return next((d for d in devices if d != current), None)


def test_get_devices_returns_current_selection(engine):
    """Baseline: engine reports both lists and a currently-selected device."""
    info = engine.get_devices()
    assert info is not None
    assert isinstance(info.get("input_devices"), list)
    assert isinstance(info.get("output_devices"), list)
    assert "current_input" in info
    assert "current_output" in info


def test_set_input_device_round_trip(engine):
    """Switch the input device to a different available one and back.

    This is the core regression test: with the pre-3.0.2 implementation
    the second `set_device` call would silently keep the original device
    because the cached `sampleRate` / `bufferSize` from the previous
    device were incompatible with the new one.
    """
    info = engine.get_devices()
    inputs = info["input_devices"]
    original = info["current_input"]

    other = _pick_alternative(inputs, original)
    if other is None:
        pytest.skip(f"Need at least two input devices to test switching, got: {inputs}")

    try:
        # Switch to the alternative device.
        assert engine.ok({"type": "set_device", "device_type": "input",
                          "device_name": other}), \
            f"set_device(input -> {other!r}) returned status=error"

        # Engine must now report the new device as current.
        after = engine.get_devices()
        assert after["current_input"] == other, (
            f"Engine reports current_input={after['current_input']!r} after "
            f"set_device, expected {other!r}. This is the pre-3.0.2 bug: "
            f"set_device said ok but the device did not actually change."
        )
    finally:
        # Always restore the original input device so other tests aren't
        # affected by lingering state.
        if original:
            engine.ok({"type": "set_device", "device_type": "input",
                       "device_name": original})


def test_set_output_device_round_trip(engine):
    """Switch the output device to a different available one and back."""
    info = engine.get_devices()
    outputs = info["output_devices"]
    original = info["current_output"]

    other = _pick_alternative(outputs, original)
    if other is None:
        pytest.skip(f"Need at least two output devices to test switching, got: {outputs}")

    try:
        assert engine.ok({"type": "set_device", "device_type": "output",
                          "device_name": other}), \
            f"set_device(output -> {other!r}) returned status=error"

        after = engine.get_devices()
        assert after["current_output"] == other, (
            f"Engine reports current_output={after['current_output']!r} after "
            f"set_device, expected {other!r}."
        )
    finally:
        if original:
            engine.ok({"type": "set_device", "device_type": "output",
                       "device_name": original})


def test_audio_callback_survives_device_switch(engine):
    """Switching the output device should not stop the audio callback.

    The fix resets sampleRate/bufferSize/channel-mask before re-applying
    the setup so JUCE can re-negotiate valid values for the new device.
    If we silently lose the device, the shared-memory sequence counter
    stops advancing.
    """
    info = engine.get_devices()
    outputs = info["output_devices"]
    original = info["current_output"]

    other = _pick_alternative(outputs, original)
    if other is None:
        pytest.skip(f"Need at least two output devices, got: {outputs}")

    try:
        # Confirm the callback is currently running.
        assert engine.wait_for_audio(timeout=2.0), \
            "Audio callback was not running before the switch"

        assert engine.ok({"type": "set_device", "device_type": "output",
                          "device_name": other}), \
            f"set_device(output -> {other!r}) failed"

        # Give JUCE a moment to renegotiate and re-open the new device.
        time.sleep(0.3)

        # Sequence counter must advance on the new device too.
        assert engine.wait_for_audio(timeout=2.0), (
            f"Audio callback stopped after switching output device to {other!r}. "
            f"set_device likely silently lost the device."
        )
    finally:
        if original:
            engine.ok({"type": "set_device", "device_type": "output",
                       "device_name": original})


def test_set_device_rejects_unknown_device(engine):
    """Asking for a device that doesn't exist must return status=error.

    Previously this could be reported as success because we did not look
    at the JUCE error string. Now the engine surfaces the failure.
    """
    r = engine.send({"type": "set_device", "device_type": "input",
                     "device_name": "definitely_not_a_real_device_xyz_12345"})
    assert r is not None
    assert r.get("status") == "error", (
        f"set_device with a bogus name should return status=error, got: {r}"
    )

    # Engine must still be responsive after the rejected call.
    assert engine.ping()


def test_set_device_rejects_invalid_type(engine):
    """device_type must be 'input' or 'output'."""
    r = engine.send({"type": "set_device", "device_type": "sidechain",
                     "device_name": "anything"})
    assert r is not None
    assert r.get("status") == "error"
    assert engine.ping()


def test_set_device_logs_renegotiated_settings(engine):
    """Verify the new code path runs: `setInputDevice` / `setOutputDevice`
    must zero out sampleRate/bufferSize/channel-mask and emit a log line
    with the values JUCE re-negotiated for the new device.

    This catches a regression where someone reverts the fix and the
    setter falls back to blindly forwarding the previous device's
    settings (which silently fails when the new device cannot support
    them — the user's original BlackHole → MacBook-Speakers symptom).

    We can't read the log while the engine is running (the engine
    keeps stdout open and may have buffered it), so we look for the
    diagnostic string by triggering a set_device call and tail-reading
    the partially-flushed log file.
    """
    log_path = getattr(engine, "log_path", None)
    if log_path is None:
        pytest.skip("Engine fixture is not capturing logs")

    info = engine.get_devices()
    outputs = info["output_devices"]
    current = info["current_output"]
    other = _pick_alternative(outputs, current)
    if other is None:
        pytest.skip(f"Need at least two output devices, got: {outputs}")

    bytes_before = log_path.stat().st_size if log_path.exists() else 0

    try:
        assert engine.ok({"type": "set_device", "device_type": "output",
                          "device_name": other}), \
            f"set_device(output -> {other!r}) failed"

        # Give the C++ side a moment to flush (std::cout is line-buffered
        # when stdout is a file, so this should be immediate, but be safe).
        time.sleep(0.2)

        log_text = log_path.read_text(errors="replace")[bytes_before:]

        # The new code path emits exactly this style of line. Without the
        # fix this string is never logged.
        assert "[AudioEngine] Output device switch:" in log_text, (
            f"Expected '[AudioEngine] Output device switch: ...' diagnostic "
            f"line in engine log after set_device — the v3.0.2 fix isn't "
            f"running.\n--- engine log (after set_device) ---\n{log_text}"
        )
        # And it should include the negotiated sample-rate / buffer-size
        # information, which is the whole point of resetting them to 0.
        assert " Hz / " in log_text and " samples" in log_text, (
            f"Engine log should include negotiated SR/buffer-size after "
            f"set_device, got:\n{log_text}"
        )
    finally:
        if current:
            engine.ok({"type": "set_device", "device_type": "output",
                       "device_name": current})


def test_repeated_input_switches(engine):
    """Switching back and forth between two inputs several times must not crash."""
    info = engine.get_devices()
    inputs = info["input_devices"]
    original = info["current_input"]

    other = _pick_alternative(inputs, original)
    if other is None:
        pytest.skip(f"Need at least two input devices, got: {inputs}")

    try:
        for _ in range(3):
            assert engine.ok({"type": "set_device", "device_type": "input",
                              "device_name": other})
            assert engine.get_devices()["current_input"] == other

            assert engine.ok({"type": "set_device", "device_type": "input",
                              "device_name": original})
            assert engine.get_devices()["current_input"] == original

        assert engine.ping()
    finally:
        if original:
            engine.ok({"type": "set_device", "device_type": "input",
                       "device_name": original})
