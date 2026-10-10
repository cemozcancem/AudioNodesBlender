"""Shared pytest setup for the engine tests.

Tests marked ``needs_audio`` only make sense with a working audio device
(they check the audio callback, FFT output, sequencer timing, device
switching...). CI machines have no sound hardware, so those tests are
skipped there instead of failing. Everything else still runs.
"""
import pytest

from harness import Engine

_audio_available = None


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "needs_audio: needs a working audio device; skipped when the engine has none",
    )


def _has_audio() -> bool:
    global _audio_available
    if _audio_available is None:
        try:
            with Engine(port=5588) as e:
                _audio_available = e.audio_running()
        except Exception:
            _audio_available = False
    return _audio_available


def pytest_runtest_setup(item):
    if item.get_closest_marker("needs_audio") and not _has_audio():
        pytest.skip("no working audio device on this machine")
