"""Pure-logic tests for the 4.15 fixes - no Blender, no engine.

Covers the parts that fail silently if they drift:

  * Step Sequencer meter: the time signature has to change the LOOP
    LENGTH and the step duration, not just the label. 7/8 must not come
    out identical to 4/4 - that was the bug.
  * Legacy step-count migration picks a grid + bars that reproduce the
    old loop exactly, so an existing pattern keeps playing as it did.
  * Shared-memory layout: the Python reader's offsets must match the C++
    struct's static_asserts. A mismatch is not an error anywhere, it is
    silently wrong numbers in every meter and overlay.

Usage:
    python3 tests/test_meter_and_layout.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "blender_addon"
ENGINE = ROOT / "engine" / "src"

FAILURES = []


def check(label, got, expected):
    ok = got == expected
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}"
          + ("" if ok else f", expected {expected!r}"))
    if not ok:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# The engine's timing formula, mirrored from SequencerNode::calculateTiming.
# ---------------------------------------------------------------------------

def samples_per_step(sample_rate, bpm, beat_unit, steps_per_beat):
    samples_per_quarter = (sample_rate * 60.0) / bpm
    samples_per_beat = samples_per_quarter * (4.0 / beat_unit)
    return max(1.0, samples_per_beat / steps_per_beat)


def derived_step_count(beats_per_bar, steps_per_beat, bars):
    return max(1, min(64, beats_per_bar * steps_per_beat * bars))


def samples_per_step_fitted(sample_rate, bpm, steps, fit_beats):
    """Polyrhythm: the whole loop spans fit_beats quarter notes."""
    samples_per_quarter = (sample_rate * 60.0) / bpm
    return max(1.0, samples_per_quarter * fit_beats / steps)


def bar_seconds(sample_rate, bpm, beats_per_bar, beat_unit, steps_per_beat, bars):
    step = samples_per_step(sample_rate, bpm, beat_unit, steps_per_beat)
    steps = derived_step_count(beats_per_bar, steps_per_beat, bars)
    return step * steps / sample_rate


print("[1] Time signature drives real timing")
SR, BPM = 48000.0, 120.0

# 4/4 at 4 steps per beat is the historical default and must be unchanged:
# 16 steps of a 16th note, a two-second bar at 120 BPM.
check("4/4 step count", derived_step_count(4, 4, 1), 16)
check("4/4 step = 16th note (samples)", samples_per_step(SR, BPM, 4, 4), 6000.0)
check("4/4 bar length (s)", round(bar_seconds(SR, BPM, 4, 4, 4, 1), 6), 2.0)

# 7/8 at 2 steps per beat: 14 steps of a 16th note, i.e. seven eighth
# notes. This is the case that used to be indistinguishable from 4/4.
check("7/8 step count", derived_step_count(7, 2, 1), 14)
check("7/8 step = 16th note (samples)", samples_per_step(SR, BPM, 8, 2), 6000.0)
check("7/8 bar length (s)", round(bar_seconds(SR, BPM, 7, 8, 2, 1), 6), 1.75)

check("7/8 differs from 4/4",
      bar_seconds(SR, BPM, 7, 8, 2, 1) != bar_seconds(SR, BPM, 4, 4, 4, 1), True)

# 6/8 in triplets: six eighths per bar, grouped in threes.
check("6/8 step count", derived_step_count(6, 3, 1), 18)
check("6/8 bar length (s)", round(bar_seconds(SR, BPM, 6, 8, 3, 1), 6), 1.5)

# Two bars of 5/4 still fit inside the engine's 64-step mask.
check("5/4 x2 step count", derived_step_count(5, 4, 2), 40)
check("step count is capped at 64", derived_step_count(16, 8, 8), 64)


print("\n[1b] Polyrhythm: different step RATES across the same span")

# The reported case. 7/4 and 4/4 at the same BPM both step in quarter notes,
# so with every step filled they play in unison - polymeter, not polyrhythm.
plain_7 = samples_per_step(SR, BPM, 4, 1)      # 7/4, grid 1
plain_4 = samples_per_step(SR, BPM, 4, 1)      # 4/4, grid 1
check("without fit, 7/4 and 4/4 steps are identical", plain_7 == plain_4, True)
check("...so both are a plain quarter note", plain_4, 24000.0)

# Fitting 7 steps into 4 beats is the actual 7:4 cross-rhythm.
fitted = samples_per_step_fitted(SR, BPM, 7, 4.0)
check("7 steps fitted into 4 beats (samples/step)", round(fitted, 4), 13714.2857)
check("  -> step is shorter than the pulse", fitted < plain_4, True)
check("  -> loop matches the 4/4 loop exactly",
      round(fitted * 7 / SR, 6), round(bar_seconds(SR, BPM, 4, 4, 1, 1), 6))
check("  -> both loops are 2.0s", round(fitted * 7 / SR, 6), 2.0)

# Classic small ratios land on exact subdivisions.
check("3:2 step (samples)", round(samples_per_step_fitted(SR, BPM, 3, 2.0), 4), 16000.0)
check("5:4 step (samples)", round(samples_per_step_fitted(SR, BPM, 5, 4.0), 4), 19200.0)
check("4:4 fitted == plain quarter", samples_per_step_fitted(SR, BPM, 4, 4.0), 24000.0)

# fit_beats = 0 must leave the meter path untouched.
check("fit off keeps the meter's own step", samples_per_step(SR, BPM, 8, 2), 6000.0)


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

print("\n[2] Legacy step-count migration")
sys.path.insert(0, str(ADDON / "nodes" / "timing"))
source = (ADDON / "nodes" / "timing" / "sequencer_node.py").read_text()
namespace = {}
match = re.search(r"def choose_grid_and_bars.*?\n    return None\n", source, re.S)
exec(match.group(0), namespace)
choose = namespace["choose_grid_and_bars"]

# Each of these reproduces the old loop length exactly.
for legacy, beats, expected in (
        (16, 4, (4, 1)),
        (8, 4, (2, 1)),
        (32, 4, (4, 2)),
        (64, 4, (4, 4)),
        # 4 steps/beat is preferred, so this lands on 2 bars, not a
        # denser grid - the loop length is what has to match.
        (16, 2, (4, 2)),
):
    result = choose(legacy, beats)
    check(f"{legacy} steps in {beats}/x", result, expected)
    per_beat, bars = result
    check(f"  -> reproduces {legacy} steps",
          derived_step_count(beats, per_beat, bars), legacy)

check("no grid fits 7 beats x 16 steps", choose(16, 7), None)


# ---------------------------------------------------------------------------
# Shared-memory layout agreement
# ---------------------------------------------------------------------------

print("\n[3] Shared memory layout: C++ header vs Python reader")

header = (ENGINE / "common" / "SharedTypes.h").read_text()
reader = (ADDON / "shared_memory_reader.py").read_text()

cpp_offsets = dict(
    (name, int(value)) for name, value in re.findall(
        r"offsetof\(AudioVisualizationData,\s*(\w+)\)\s*==\s*(\d+)", header))
cpp_size = int(re.search(
    r"sizeof\(AudioVisualizationData\)\s*==\s*(\d+)", header).group(1))

def py_const(name):
    found = re.search(rf"^{name}\s*=\s*(\d+)", reader, re.M)
    return int(found.group(1)) if found else None

check("struct size == SHARED_MEMORY_SIZE", py_const("SHARED_MEMORY_SIZE"), cpp_size)
check("size is in KNOWN_MEMORY_SIZES",
      str(cpp_size) in re.search(r"KNOWN_MEMORY_SIZES\s*=\s*\(([^)]*)\)",
                                 reader).group(1), True)

for field, constant in (
        ("sequence", "OFFSET_SEQUENCE"),
        ("fft_left", "OFFSET_FFT_LEFT"),
        ("fft_right", "OFFSET_FFT_RIGHT"),
        ("peak_left", "OFFSET_PEAK_LEFT"),
        ("waveform", "OFFSET_WAVEFORM"),
        ("midi", "OFFSET_MIDI"),
        ("sequencer", "OFFSET_SEQUENCER"),
        ("sample_rate", "OFFSET_SAMPLE_RATE"),
        ("num_input_channels", "OFFSET_NUM_INPUT_CHANNELS"),
        ("input_peaks", "OFFSET_INPUT_PEAKS"),
        ("num_fft_analyzers", "OFFSET_NUM_FFT_ANALYZERS"),
        ("fft_slots", "OFFSET_FFT_SLOTS"),
        ("num_sequencers", "OFFSET_NUM_SEQUENCERS"),
        ("sequencer_slots", "OFFSET_SEQUENCER_SLOTS"),
        ("num_control_nodes", "OFFSET_NUM_CONTROL_NODES"),
        ("control_slots", "OFFSET_CONTROL_SLOTS"),
):
    check(f"offsetof({field})", py_const(constant), cpp_offsets.get(field))

cpp_slot_size = int(re.search(r"sizeof\(ControlSlot\)\s*==\s*(\d+)", header).group(1))
check("ControlSlot size", py_const("CONTROL_SLOT_SIZE"), cpp_slot_size)
check("control slots fit the struct",
      cpp_offsets["control_slots"] + cpp_slot_size * py_const("MAX_CONTROL_NODES"),
      cpp_size)


print("\n" + "=" * 60)
print(f"FAILURES: {len(FAILURES)}")
for name in FAILURES:
    print(f"  - {name}")
sys.exit(1 if FAILURES else 0)
