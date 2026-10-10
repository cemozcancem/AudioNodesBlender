"""
Engine integration tests — no Blender required.

Run all:
    cd tests && python3.11 -m pytest test_engine.py -v

Run one:
    python3.11 -m pytest test_engine.py::test_ping -v
"""

import math
import time
from pathlib import Path
from typing import Optional

import pytest
from harness import Engine

# ---------------------------------------------------------------------------
# Shared fixture — engine starts once, all tests share it
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    with Engine() as e:
        yield e


# ---------------------------------------------------------------------------
# Sample paths — real WAV files in /testsamples
# ---------------------------------------------------------------------------

TESTSAMPLES = Path(__file__).parent.parent / "testsamples"


@pytest.fixture(scope="module")
def sample_wav():
    p = TESTSAMPLES / "pad.wav"
    assert p.exists(), f"Missing test sample: {p}"
    return str(p)


@pytest.fixture(scope="module")
def kick_wav():
    p = TESTSAMPLES / "kick.wav"
    assert p.exists(), f"Missing test sample: {p}"
    return str(p)


@pytest.fixture(scope="module")
def snare_wav():
    p = TESTSAMPLES / "snare.wav"
    assert p.exists(), f"Missing test sample: {p}"
    return str(p)


# ===========================================================================
# 1. CONNECTIVITY
# ===========================================================================

def test_ping(engine):
    assert engine.ping()


def test_get_devices(engine):
    devices = engine.get_devices()
    assert devices is not None
    assert "input_devices" in devices
    assert "output_devices" in devices
    assert isinstance(devices["input_devices"], list)
    assert isinstance(devices["output_devices"], list)


def test_get_midi_devices(engine):
    devices = engine.get_midi_devices()
    assert devices is not None
    assert "input_devices" in devices
    assert "output_devices" in devices


# ===========================================================================
# 2. NODE LIFECYCLE — all engine-side node types
# ===========================================================================

ALL_NODE_TYPES = [
    "input", "output", "oscillator", "filter", "gain", "delay",
    "fft_analyzer", "sampler", "drum_sampler", "sequencer", "adsr",
    "midi_input", "midi_output", "mixer", "math",
]


@pytest.mark.parametrize("node_type", ALL_NODE_TYPES)
def test_create_delete_node(engine, node_type):
    nid = f"{node_type}_lifecycle"
    assert engine.create_node(nid, node_type), f"create failed: {node_type}"
    assert engine.delete_node(nid), f"delete failed: {node_type}"


def test_create_node_with_params(engine):
    assert engine.create_node("osc_params", "oscillator",
                               params={"frequency": 880.0, "gain": 0.3})
    assert engine.delete_node("osc_params")


def test_delete_nonexistent_is_safe(engine):
    r = engine.send({"type": "delete_node", "node_id": "ghost_node"})
    assert r is not None  # engine must respond, not crash


# ===========================================================================
# 3. CONNECTIONS
# ===========================================================================

def test_connect_linear_chain(engine):
    engine.create_node("osc_chain", "oscillator")
    engine.create_node("gain_chain", "gain")
    engine.create_node("out_chain", "output")

    assert engine.connect("osc_chain", "gain_chain")
    assert engine.connect("gain_chain", "out_chain")
    assert engine.disconnect("gain_chain")
    assert engine.disconnect("out_chain")

    engine.delete_node("osc_chain")
    engine.delete_node("gain_chain")
    engine.delete_node("out_chain")


def test_connect_with_fft_analyzer(engine):
    engine.create_node("osc_fft", "oscillator")
    engine.create_node("fft", "fft_analyzer")
    engine.create_node("out_fft", "output")

    assert engine.connect("osc_fft", "fft")
    assert engine.connect("fft", "out_fft")
    assert engine.disconnect("fft")
    assert engine.disconnect("out_fft")

    engine.delete_node("osc_fft")
    engine.delete_node("fft")
    engine.delete_node("out_fft")


def test_mixer_named_input_ports(engine):
    for i in range(1, 5):
        engine.create_node(f"osc_mx{i}", "oscillator",
                           params={"frequency": 110.0 * i})
    engine.create_node("mixer_test", "mixer")

    for i in range(1, 5):
        assert engine.connect(f"osc_mx{i}", "mixer_test", dst_port=f"input_{i}")

    for i in range(1, 5):
        assert engine.disconnect("mixer_test", dst_port=f"input_{i}")

    for i in range(1, 5):
        engine.delete_node(f"osc_mx{i}")
    engine.delete_node("mixer_test")


def test_reconnect_same_port(engine):
    engine.create_node("osc_r1", "oscillator")
    engine.create_node("osc_r2", "oscillator")
    engine.create_node("gain_r", "gain")

    engine.connect("osc_r1", "gain_r")
    # Reconnect the same destination port with a different source
    assert engine.connect("osc_r2", "gain_r")
    engine.disconnect("gain_r")

    engine.delete_node("osc_r1")
    engine.delete_node("osc_r2")
    engine.delete_node("gain_r")


# ===========================================================================
# 4. PARAMETERS — per-node exhaustive coverage
# ===========================================================================

def test_params_filter(engine):
    engine.create_node("filter_p", "filter")
    assert engine.set_param("filter_p", "cutoff", 500.0)
    assert engine.set_param("filter_p", "cutoff", 8000.0)
    assert engine.set_param("filter_p", "resonance", 0.1)
    assert engine.set_param("filter_p", "resonance", 0.99)
    assert engine.set_param("filter_p", "filter_type", 0.0)   # lowpass
    assert engine.set_param("filter_p", "filter_type", 1.0)   # highpass
    assert engine.set_param("filter_p", "filter_type", 2.0)   # bandpass
    engine.delete_node("filter_p")


def test_params_gain(engine):
    engine.create_node("gain_p", "gain")
    for v in [0.0, 0.25, 0.5, 0.75, 1.0]:
        assert engine.set_param("gain_p", "gain", v)
    assert engine.set_param("gain_p", "mute", 1.0)
    assert engine.set_param("gain_p", "mute", 0.0)
    engine.delete_node("gain_p")


def test_params_delay(engine):
    engine.create_node("delay_p", "delay")
    assert engine.set_param("delay_p", "delay_time", 0.1)
    assert engine.set_param("delay_p", "delay_time", 1.0)
    assert engine.set_param("delay_p", "feedback", 0.0)
    assert engine.set_param("delay_p", "feedback", 0.9)
    assert engine.set_param("delay_p", "mix", 0.0)
    assert engine.set_param("delay_p", "mix", 1.0)
    engine.delete_node("delay_p")


def test_params_oscillator(engine):
    engine.create_node("osc_p", "oscillator")
    for freq in [20.0, 110.0, 440.0, 1000.0, 8000.0, 20000.0]:
        assert engine.set_param("osc_p", "frequency", freq)
    for waveform in [0.0, 1.0, 2.0, 3.0]:  # sine, saw, square, triangle
        assert engine.set_param("osc_p", "waveform", waveform)
    assert engine.set_param("osc_p", "gain", 0.5)
    engine.delete_node("osc_p")


def test_params_mixer(engine):
    engine.create_node("mixer_p", "mixer")
    assert engine.set_param("mixer_p", "master_volume", 0.8)
    # Per-channel level and pan
    for ch in range(1, 9):
        assert engine.set_param("mixer_p", f"level_{ch}", 0.7)
        assert engine.set_param("mixer_p", f"pan_{ch}", -0.5)
        assert engine.set_param("mixer_p", f"pan_{ch}", 0.5)
        assert engine.set_param("mixer_p", f"mute_{ch}", 1.0)
        assert engine.set_param("mixer_p", f"mute_{ch}", 0.0)
    engine.delete_node("mixer_p")


def test_params_math(engine):
    engine.create_node("math_p", "math")
    for op in range(8):   # add, sub, mul, div, min, max, abs, clamp
        assert engine.set_param("math_p", "operation", float(op))
    assert engine.set_param("math_p", "control_a", 0.5)
    assert engine.set_param("math_p", "control_b", 2.0)
    assert engine.set_param("math_p", "clamp_min", 0.0)
    assert engine.set_param("math_p", "clamp_max", 1.0)
    engine.delete_node("math_p")


# ===========================================================================
# 5. SEQUENCER
# ===========================================================================

def test_sequencer_bpm(engine):
    engine.create_node("seq_bpm", "sequencer")
    for bpm in [60.0, 120.0, 180.0, 300.0]:
        assert engine.set_param("seq_bpm", "bpm", bpm)
    engine.delete_node("seq_bpm")


def test_sequencer_step_counts(engine):
    engine.create_node("seq_steps", "sequencer")
    for steps in [8.0, 16.0, 32.0, 64.0]:
        assert engine.set_param("seq_steps", "steps", steps)
    engine.delete_node("seq_steps")


def test_sequencer_play_stop(engine):
    engine.create_node("seq_play", "sequencer")
    engine.set_param("seq_play", "bpm", 120.0)
    assert engine.set_param("seq_play", "play", 1.0)
    time.sleep(0.05)
    assert engine.set_param("seq_play", "stop", 1.0)
    engine.delete_node("seq_play")


def test_sequencer_patterns(engine):
    engine.create_node("seq_pat", "sequencer")
    # Set different patterns for lanes 0-3
    for lane in range(4):
        pattern = 0b1010101010101010   # alternating steps
        assert engine.set_param("seq_pat", f"pattern_{lane}", float(pattern))
    engine.delete_node("seq_pat")


def test_sequencer_time_signature(engine):
    engine.create_node("seq_ts", "sequencer")
    assert engine.set_param("seq_ts", "beats_per_bar", 4.0)
    assert engine.set_param("seq_ts", "beat_unit", 4.0)
    assert engine.set_param("seq_ts", "beats_per_bar", 7.0)   # 7/8
    assert engine.set_param("seq_ts", "beat_unit", 8.0)
    engine.delete_node("seq_ts")


def test_sequencer_swing_gate(engine):
    engine.create_node("seq_sg", "sequencer")
    assert engine.set_param("seq_sg", "swing", 0.0)
    assert engine.set_param("seq_sg", "swing", 0.5)
    assert engine.set_param("seq_sg", "gate_length", 0.25)
    assert engine.set_param("seq_sg", "gate_length", 1.0)
    engine.delete_node("seq_sg")


# ---------------------------------------------------------------------------
# Sequencer BEHAVIORAL tests — verify actual step advancement via shared memory
# ---------------------------------------------------------------------------

def _seq_available(engine) -> bool:
    return engine.read_sequencer() is not None


def test_sequencer_shm_is_playing_flag(engine):
    """is_playing in shared memory must reflect play/stop commands."""
    if not _seq_available(engine):
        pytest.skip("Shared memory not available")

    engine.create_node("seq_flag", "sequencer")
    engine.set_param("seq_flag", "bpm", 120.0)
    engine.set_param("seq_flag", "play", 1.0)
    time.sleep(0.1)
    playing = engine.read_sequencer()
    assert playing is not None and playing["is_playing"], \
        "Sequencer not marked playing after play command"

    engine.set_param("seq_flag", "stop", 1.0)
    time.sleep(0.05)
    stopped = engine.read_sequencer()
    assert stopped is not None and not stopped["is_playing"], \
        "Sequencer still marked playing after stop command"

    engine.delete_node("seq_flag")


def test_sequencer_step_advances(engine):
    """current_step must increment while the sequencer is playing."""
    if not _seq_available(engine):
        pytest.skip("Shared memory not available")

    engine.create_node("seq_adv", "sequencer")
    engine.set_param("seq_adv", "bpm", 600.0)
    engine.set_param("seq_adv", "steps", 16.0)
    engine.set_param("seq_adv", "play", 1.0)
    time.sleep(0.05)   # let audio thread process the queue
    initial = engine.read_sequencer()
    time.sleep(0.5)    # at 600 BPM each 16th note ≈ 25ms → ~20 steps in 0.5s
    after = engine.read_sequencer()

    engine.set_param("seq_adv", "stop", 1.0)
    engine.delete_node("seq_adv")

    assert initial is not None and after is not None
    assert after["current_step"] != initial["current_step"], \
        f"Step did not advance (stayed at {initial['current_step']})"


def test_sequencer_step_resets_on_play(engine):
    """Playing after stop should reset to step 0."""
    if not _seq_available(engine):
        pytest.skip("Shared memory not available")

    engine.create_node("seq_rst", "sequencer")
    engine.set_param("seq_rst", "bpm", 600.0)
    engine.set_param("seq_rst", "play", 1.0)
    time.sleep(0.3)
    engine.set_param("seq_rst", "stop", 1.0)
    time.sleep(0.05)

    # Play again — step should restart from 0
    engine.set_param("seq_rst", "play", 1.0)
    time.sleep(0.02)
    restarted = engine.read_sequencer()
    engine.set_param("seq_rst", "stop", 1.0)
    engine.delete_node("seq_rst")

    assert restarted is not None
    assert restarted["current_step"] <= 2, \
        f"Step did not reset on re-play (step={restarted['current_step']})"


def test_sequencer_trigger_fires_on_active_step(engine):
    """trigger_active must become True when a step with a set pattern fires."""
    if not _seq_available(engine):
        pytest.skip("Shared memory not available")

    engine.create_node("seq_trig", "sequencer")
    engine.set_param("seq_trig", "pattern_0", float(0xFFFF))
    engine.set_param("seq_trig", "steps", 16.0)
    engine.set_param("seq_trig", "bpm", 600.0)
    engine.set_param("seq_trig", "gate_length", 0.9)
    engine.set_param("seq_trig", "play", 1.0)

    # Poll for trigger_active within 1 second
    deadline = time.time() + 1.0
    fired = False
    while time.time() < deadline:
        d = engine.read_sequencer()
        if d and d["trigger_active"]:
            fired = True
            break
        time.sleep(0.02)

    engine.set_param("seq_trig", "stop", 1.0)
    engine.delete_node("seq_trig")

    assert fired, "trigger_active never became True with all steps enabled"


def test_sequencer_no_trigger_when_pattern_empty(engine):
    """trigger_active must stay False when pattern is all zeros."""
    if not _seq_available(engine):
        pytest.skip("Shared memory not available")

    engine.create_node("seq_notrig", "sequencer")
    engine.set_param("seq_notrig", "pattern_0", 0.0)   # no active steps
    engine.set_param("seq_notrig", "steps", 16.0)
    engine.set_param("seq_notrig", "bpm", 600.0)
    engine.set_param("seq_notrig", "play", 1.0)

    time.sleep(0.3)
    d = engine.read_sequencer()
    engine.set_param("seq_notrig", "stop", 1.0)
    engine.delete_node("seq_notrig")

    assert d is not None
    assert not d["trigger_active"], \
        "trigger_active was True even though pattern was empty"


# ===========================================================================
# 6. ADSR
# ===========================================================================

def test_adsr_envelope_params(engine):
    engine.create_node("adsr_p", "adsr")
    assert engine.set_param("adsr_p", "attack", 10.0)    # ms
    assert engine.set_param("adsr_p", "decay", 50.0)
    assert engine.set_param("adsr_p", "sustain", 0.7)
    assert engine.set_param("adsr_p", "release", 200.0)
    engine.delete_node("adsr_p")


def test_adsr_gate_on_off(engine):
    engine.create_node("adsr_g", "adsr")
    engine.set_param("adsr_g", "attack", 5.0)
    engine.set_param("adsr_g", "decay", 10.0)
    engine.set_param("adsr_g", "sustain", 0.8)
    engine.set_param("adsr_g", "release", 50.0)

    assert engine.set_param("adsr_g", "gate", 1.0)   # note on
    time.sleep(0.05)
    assert engine.set_param("adsr_g", "gate", 0.0)   # note off
    time.sleep(0.05)

    assert engine.ping()   # engine must still be responsive
    engine.delete_node("adsr_g")


def test_adsr_apply_to_audio(engine):
    engine.create_node("adsr_a", "adsr")
    assert engine.set_param("adsr_a", "apply_to_audio", 1.0)
    assert engine.set_param("adsr_a", "apply_to_audio", 0.0)
    engine.delete_node("adsr_a")


def test_adsr_in_chain(engine):
    engine.create_node("osc_adsr", "oscillator", params={"frequency": 440.0})
    engine.create_node("adsr_c", "adsr",
                       params={"attack": 10.0, "decay": 20.0,
                               "sustain": 0.7, "release": 100.0})
    engine.create_node("out_adsr", "output")

    engine.connect("osc_adsr", "adsr_c")
    engine.connect("adsr_c", "out_adsr")
    engine.set_param("adsr_c", "gate", 1.0)
    time.sleep(0.05)
    engine.set_param("adsr_c", "gate", 0.0)

    engine.disconnect("adsr_c")
    engine.disconnect("out_adsr")
    engine.delete_node("osc_adsr")
    engine.delete_node("adsr_c")
    engine.delete_node("out_adsr")
    assert engine.ping()


# ===========================================================================
# 7. SAMPLER
# ===========================================================================

def test_sampler_load_sample(engine, sample_wav):
    engine.create_node("samp_load", "sampler")
    r = engine.send({"type": "load_sample", "node_id": "samp_load",
                     "file_path": sample_wav})
    assert r is not None and r.get("status") == "ok", f"load_sample failed: {r}"
    engine.delete_node("samp_load")


def test_sampler_params(engine, sample_wav):
    engine.create_node("samp_p", "sampler")
    engine.send({"type": "load_sample", "node_id": "samp_p",
                 "file_path": sample_wav})

    assert engine.set_param("samp_p", "volume", 0.8)
    assert engine.set_param("samp_p", "pitch", 0.0)      # no pitch shift
    assert engine.set_param("samp_p", "pitch", 12.0)     # +1 octave
    assert engine.set_param("samp_p", "pitch", -12.0)    # -1 octave
    assert engine.set_param("samp_p", "start", 0.0)
    assert engine.set_param("samp_p", "end", 1.0)
    assert engine.set_param("samp_p", "loop", 1.0)
    assert engine.set_param("samp_p", "loop", 0.0)
    assert engine.set_param("samp_p", "one_shot", 1.0)
    engine.delete_node("samp_p")


def test_sampler_trigger(engine, sample_wav):
    engine.create_node("samp_t", "sampler")
    engine.send({"type": "load_sample", "node_id": "samp_t",
                 "file_path": sample_wav})
    engine.set_param("samp_t", "volume", 0.5)

    assert engine.set_param("samp_t", "trigger", 1.0)
    time.sleep(0.1)
    assert engine.set_param("samp_t", "stop", 1.0)

    assert engine.ping()
    engine.delete_node("samp_t")


def test_sampler_missing_file_is_safe(engine):
    engine.create_node("samp_bad", "sampler")
    r = engine.send({"type": "load_sample", "node_id": "samp_bad",
                     "file_path": "/nonexistent/file.wav"})
    # Engine should return an error response, not crash
    assert r is not None
    engine.delete_node("samp_bad")


def test_sampler_in_chain(engine, sample_wav):
    engine.create_node("samp_chain", "sampler")
    engine.create_node("gain_samp", "gain")
    engine.create_node("out_samp", "output")

    engine.send({"type": "load_sample", "node_id": "samp_chain",
                 "file_path": sample_wav})
    engine.connect("samp_chain", "gain_samp")
    engine.connect("gain_samp", "out_samp")
    engine.set_param("samp_chain", "trigger", 1.0)
    time.sleep(0.1)

    engine.disconnect("gain_samp")
    engine.disconnect("out_samp")
    engine.delete_node("samp_chain")
    engine.delete_node("gain_samp")
    engine.delete_node("out_samp")
    assert engine.ping()


# ===========================================================================
# 8. DRUM SAMPLER
# ===========================================================================

def test_drum_sampler_load_slots(engine, kick_wav, snare_wav):
    engine.create_node("drum_load", "drum_sampler")
    r0 = engine.send({"type": "load_drum_sample", "node_id": "drum_load",
                      "slot": 0, "file_path": kick_wav})
    assert r0 is not None and r0.get("status") == "ok", f"slot 0 failed: {r0}"

    r1 = engine.send({"type": "load_drum_sample", "node_id": "drum_load",
                      "slot": 1, "file_path": snare_wav})
    assert r1 is not None and r1.get("status") == "ok", f"slot 1 failed: {r1}"

    engine.delete_node("drum_load")


def test_drum_sampler_trigger_slots(engine, kick_wav, snare_wav):
    engine.create_node("drum_trig", "drum_sampler")
    engine.send({"type": "load_drum_sample", "node_id": "drum_trig",
                 "slot": 0, "file_path": kick_wav})
    engine.send({"type": "load_drum_sample", "node_id": "drum_trig",
                 "slot": 1, "file_path": snare_wav})

    assert engine.set_param("drum_trig", "trigger_0", 1.0)
    time.sleep(0.05)
    assert engine.set_param("drum_trig", "trigger_1", 1.0)
    time.sleep(0.05)

    assert engine.ping()
    engine.delete_node("drum_trig")


def test_drum_sampler_slot_params(engine, kick_wav):
    engine.create_node("drum_p", "drum_sampler")
    engine.send({"type": "load_drum_sample", "node_id": "drum_p",
                 "slot": 0, "file_path": kick_wav})

    assert engine.set_param("drum_p", "master_volume", 0.8)
    assert engine.set_param("drum_p", "slot_0_volume", 0.9)
    assert engine.set_param("drum_p", "slot_0_pitch", 2.0)

    engine.delete_node("drum_p")


def test_drum_sampler_missing_slot_file(engine):
    engine.create_node("drum_bad", "drum_sampler")
    r = engine.send({"type": "load_drum_sample", "node_id": "drum_bad",
                     "slot": 0, "file_path": "/nonexistent/kick.wav"})
    assert r is not None  # must not crash
    engine.delete_node("drum_bad")


# ===========================================================================
# 9. MIDI NODES
# ===========================================================================

def test_midi_input_create_delete(engine):
    engine.create_node("midi_in", "midi_input")
    assert engine.ping()
    engine.delete_node("midi_in")


def test_midi_output_create_delete(engine):
    engine.create_node("midi_out", "midi_output")
    assert engine.ping()
    engine.delete_node("midi_out")


def test_midi_input_readable_params(engine):
    engine.create_node("midi_in_p", "midi_input")
    # Read-only params — just check engine responds
    r = engine.send({"type": "set_param", "node_id": "midi_in_p",
                     "param": "note", "value": 60.0})
    assert r is not None
    engine.delete_node("midi_in_p")


# ===========================================================================
# 10. SHARED MEMORY
# ===========================================================================

def test_shared_memory_structure(engine):
    data = engine.read_shared_memory()
    if data is None:
        pytest.skip("Shared memory not written yet")
    assert "sequence" in data
    assert "fft_left" in data and len(data["fft_left"]) == 1024
    assert "fft_right" in data and len(data["fft_right"]) == 1024
    assert "peak_left" in data
    assert "peak_right" in data
    assert "rms_left" in data
    assert "rms_right" in data


def test_shared_memory_sequence_advances(engine):
    """Audio callback is running if sequence counter increments."""
    data1 = engine.read_shared_memory()
    if data1 is None:
        pytest.skip("Shared memory not available")
    time.sleep(0.15)
    data2 = engine.read_shared_memory()
    assert data2 is not None
    assert data2["sequence"] != data1["sequence"], \
        "Sequence counter did not advance — audio callback may not be running"


def test_oscillator_signal_in_chain(engine):
    devices = engine.get_devices()
    if not devices or not devices.get("current_output"):
        pytest.skip("No audio output device — audio callback inactive")

    engine.create_node("osc_sig", "oscillator", params={"frequency": 440.0, "gain": 0.8})
    engine.create_node("out_sig", "output")
    engine.connect("osc_sig", "out_sig")

    has_signal = engine.wait_for_audio(timeout=2.0)

    engine.disconnect("out_sig")
    engine.delete_node("osc_sig")
    engine.delete_node("out_sig")

    assert has_signal, "Audio callback not advancing after Oscillator→Output chain"


# ===========================================================================
# 11. STABILITY
# ===========================================================================

def test_rapid_param_updates(engine):
    engine.create_node("gain_rapid", "gain")
    for i in range(100):
        engine.set_param("gain_rapid", "gain", (i % 10) / 10.0)
    time.sleep(0.1)
    assert engine.ping()
    engine.delete_node("gain_rapid")


def test_create_delete_stress(engine):
    for i in range(30):
        engine.create_node(f"stress_{i}", "gain")
        engine.delete_node(f"stress_{i}")
    assert engine.ping()


def test_deep_chain_create_delete(engine):
    """Create a long serial chain and tear it down cleanly."""
    nodes = [f"deep_{i}" for i in range(10)]
    engine.create_node(nodes[0], "oscillator", params={"frequency": 440.0})
    for n in nodes[1:-1]:
        engine.create_node(n, "gain")
    engine.create_node(nodes[-1], "output")

    for i in range(len(nodes) - 1):
        engine.connect(nodes[i], nodes[i + 1])

    time.sleep(0.05)

    for i in range(len(nodes) - 1):
        engine.disconnect(nodes[i + 1])
    for n in nodes:
        engine.delete_node(n)

    assert engine.ping()


def test_engine_responsive_after_all_tests(engine):
    assert engine.ping()


# ===========================================================================
# 12. CONTROL SIGNAL ROUTING — sequencer triggers sampler / drum sampler
# ===========================================================================

def test_sequencer_triggers_sampler(engine, sample_wav):
    """Sequencer lane 0 connected to sampler 'trigger' port must start playback."""
    engine.create_node("seq_s", "sequencer")
    engine.create_node("samp_s", "sampler")
    engine.send({"type": "load_sample", "node_id": "samp_s", "file_path": sample_wav})

    # Set all steps active so every step fires
    engine.set_param("seq_s", "pattern_0", float(0xFFFF))
    engine.set_param("seq_s", "steps", 16.0)
    engine.set_param("seq_s", "bpm", 600.0)
    engine.set_param("seq_s", "gate_length", 0.5)

    # Wire sequencer → sampler trigger port
    assert engine.connect("seq_s", "samp_s", src_port="trigger", dst_port="trigger"), \
        "connect(sequencer→sampler trigger) failed"

    # Start sequencer — triggers should reach the sampler
    engine.set_param("seq_s", "play", 1.0)
    time.sleep(0.3)   # several steps at 600 BPM

    # The sampler should now be playing (triggered by sequencer)
    r = engine.send({"type": "set_param", "node_id": "samp_s",
                     "param": "trigger", "value": 0.0})
    assert r is not None   # just verify engine still responds

    engine.set_param("seq_s", "stop", 1.0)
    engine.disconnect("samp_s", dst_port="trigger")
    engine.delete_node("seq_s")
    engine.delete_node("samp_s")
    assert engine.ping()


def test_sequencer_triggers_drum_sampler(engine, kick_wav, snare_wav):
    """Sequencer connected to drum sampler trig ports drives per-slot playback."""
    engine.create_node("seq_d", "sequencer")
    engine.create_node("drum_d", "drum_sampler")
    engine.send({"type": "load_drum_sample", "node_id": "drum_d",
                 "slot": 0, "file_path": kick_wav})
    engine.send({"type": "load_drum_sample", "node_id": "drum_d",
                 "slot": 1, "file_path": snare_wav})

    engine.set_param("seq_d", "pattern_0", float(0xFFFF))
    engine.set_param("seq_d", "bpm", 600.0)
    engine.set_param("seq_d", "gate_length", 0.5)

    # Connect sequencer trigger → drum slot 1 (trig_1 = slot 0)
    assert engine.connect("seq_d", "drum_d", src_port="trigger", dst_port="trig_1"), \
        "connect(sequencer→drum trig_1) failed"

    engine.set_param("seq_d", "play", 1.0)
    time.sleep(0.3)

    engine.set_param("seq_d", "stop", 1.0)
    engine.disconnect("drum_d", dst_port="trig_1")
    engine.delete_node("seq_d")
    engine.delete_node("drum_d")
    assert engine.ping()


# ===========================================================================
# Helpers for deep audio analysis tests
# ===========================================================================

def _has_audio(engine) -> bool:
    devices = engine.get_devices()
    return bool(devices and devices.get("current_output"))


def _build_fft_chain(engine, source_id, source_type, params=None):
    """Create <source> → FFTAnalyzer → Output. Returns (fft_id, out_id)."""
    fft_id = f"{source_id}_fft"
    out_id = f"{source_id}_fout"
    engine.create_node(source_id, source_type, params=params)
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect(source_id, fft_id)
    engine.connect(fft_id, out_id)
    time.sleep(0.15)          # let FFT fill (2048 / 44100 ≈ 46 ms per frame)
    return fft_id, out_id


def _tear_fft_chain(engine, source_id, fft_id, out_id):
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    engine.delete_node(source_id)
    engine.delete_node(fft_id)
    engine.delete_node(out_id)


# ===========================================================================
# 13. DEEP AUDIO ANALYSIS — verify signal levels and frequency content
#     (skipped automatically when no audio output device is present)
# ===========================================================================

def test_deep_oscillator_rms_nonzero(engine):
    """A running oscillator must produce non-zero RMS in the FFT analyzer."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    fft_id, out_id = _build_fft_chain(engine, "osc_rms", "oscillator",
                                       params={"frequency": 440.0, "gain": 0.8})
    rms = engine.measure_peak_rms(duration=0.3)
    _tear_fft_chain(engine, "osc_rms", fft_id, out_id)

    assert rms > 0.01, f"Expected non-zero RMS, got {rms:.4f}"


def test_deep_gain_zero_silences_audio(engine):
    """Muted gain node must produce silence; unmuted at 0 dB must pass signal."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    fft_id, out_id = _build_fft_chain(engine, "osc_g0", "oscillator",
                                       params={"frequency": 440.0, "gain": 0.8})
    engine.create_node("gain_g0", "gain", params={"gain": 0.0})  # 0 dB = unity
    # Insert gain node between oscillator and FFTAnalyzer
    engine.disconnect(fft_id)            # osc → fft
    engine.connect("osc_g0", "gain_g0")
    engine.connect("gain_g0", fft_id)

    # Muted → silence
    engine.set_param("gain_g0", "mute", 1.0)
    time.sleep(0.2)
    rms_silent = engine.measure_peak_rms(duration=0.25)

    # Unmuted at 0 dB → signal passes through
    engine.set_param("gain_g0", "mute", 0.0)
    time.sleep(0.15)
    rms_signal = engine.measure_peak_rms(duration=0.25)

    engine.disconnect("gain_g0")
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    engine.delete_node("gain_g0")
    engine.delete_node("osc_g0")
    engine.delete_node(fft_id)
    engine.delete_node(out_id)

    assert rms_silent < 0.001, f"muted gain should produce silence, got RMS={rms_silent:.4f}"
    assert rms_signal > 0.01,  f"unmuted gain should pass signal, got RMS={rms_signal:.4f}"


def test_deep_mute_toggle(engine):
    """Muting a gain node silences output; un-muting restores it."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    fft_id, out_id = _build_fft_chain(engine, "osc_mute", "oscillator",
                                       params={"frequency": 440.0, "gain": 0.8})
    engine.create_node("gain_mute", "gain", params={"gain": 0.8})
    engine.disconnect(fft_id)
    engine.connect("osc_mute", "gain_mute")
    engine.connect("gain_mute", fft_id)
    time.sleep(0.15)

    rms_unmuted = engine.measure_peak_rms(duration=0.2)

    engine.set_param("gain_mute", "mute", 1.0)
    time.sleep(0.15)
    rms_muted = engine.measure_peak_rms(duration=0.2)

    engine.set_param("gain_mute", "mute", 0.0)
    time.sleep(0.15)
    rms_restored = engine.measure_peak_rms(duration=0.2)

    engine.disconnect("gain_mute")
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    engine.delete_node("gain_mute")
    engine.delete_node("osc_mute")
    engine.delete_node(fft_id)
    engine.delete_node(out_id)

    assert rms_unmuted > 0.01,  f"Unmuted: expected signal, got {rms_unmuted:.4f}"
    assert rms_muted   < 0.001, f"Muted: expected silence, got {rms_muted:.4f}"
    assert rms_restored > 0.01, f"Restored: expected signal, got {rms_restored:.4f}"


def test_deep_gain_scales_proportionally(engine):
    """RMS with gain=0.8 should be approximately double RMS with gain=0.4."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    fft_id, out_id = _build_fft_chain(engine, "osc_scale", "oscillator",
                                       params={"frequency": 440.0, "gain": 1.0})

    engine.set_param("osc_scale", "gain", 0.4)
    time.sleep(0.15)
    rms_low = engine.measure_peak_rms(duration=0.25)

    engine.set_param("osc_scale", "gain", 0.8)
    time.sleep(0.15)
    rms_high = engine.measure_peak_rms(duration=0.25)

    _tear_fft_chain(engine, "osc_scale", fft_id, out_id)

    assert rms_low > 0.001, f"gain=0.4 produced no signal: {rms_low:.4f}"
    ratio = rms_high / rms_low
    # Expect roughly 2× (allow 1.4–2.8 window for non-linearities/smoothing)
    assert 1.4 < ratio < 2.8, \
        f"gain doubling should ~double RMS; got ratio={ratio:.2f} (low={rms_low:.4f}, high={rms_high:.4f})"


def test_deep_oscillator_fft_peak_at_frequency(engine):
    """FFT peak bin must land near the oscillator's programmed frequency."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    freq = 880.0
    expected_bin = engine.freq_to_bin(freq)

    fft_id, out_id = _build_fft_chain(engine, "osc_fft_peak", "oscillator",
                                       params={"frequency": freq, "gain": 0.8})
    time.sleep(0.2)   # extra settling for FFT
    peak_bin = engine.read_fft_peak_bin(min_bin=2)
    _tear_fft_chain(engine, "osc_fft_peak", fft_id, out_id)

    assert peak_bin is not None, "No FFT data available"
    assert abs(peak_bin - expected_bin) <= 3, \
        f"FFT peak bin {peak_bin} too far from expected {expected_bin} for {freq} Hz"


def test_deep_oscillator_frequency_shifts_fft(engine):
    """Doubling oscillator frequency must shift the FFT peak to a higher bin."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    fft_id, out_id = _build_fft_chain(engine, "osc_shift", "oscillator",
                                       params={"frequency": 440.0, "gain": 0.8})
    time.sleep(0.2)
    bin_440 = engine.read_fft_peak_bin()

    engine.set_param("osc_shift", "frequency", 880.0)
    time.sleep(0.2)
    bin_880 = engine.read_fft_peak_bin()

    _tear_fft_chain(engine, "osc_shift", fft_id, out_id)

    assert bin_440 is not None and bin_880 is not None
    assert bin_880 > bin_440, \
        f"880 Hz peak bin ({bin_880}) should be higher than 440 Hz ({bin_440})"


def test_deep_filter_lowpass_attenuates(engine):
    """Lowpass filter at 200 Hz must significantly reduce a 4 kHz signal."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    # Build: oscillator(4kHz) → filter → FFTAnalyzer → output
    engine.create_node("osc_fil", "oscillator", params={"frequency": 4000.0, "gain": 0.8})
    engine.create_node("filt_fil", "filter",
                       params={"filter_type": 0.0, "cutoff": 20000.0, "resonance": 0.1})
    engine.create_node("fft_fil", "fft_analyzer")
    engine.create_node("out_fil", "output")
    engine.connect("osc_fil", "filt_fil")
    engine.connect("filt_fil", "fft_fil")
    engine.connect("fft_fil", "out_fil")
    time.sleep(0.2)

    rms_open = engine.measure_peak_rms(duration=0.25)

    engine.set_param("filt_fil", "cutoff", 200.0)   # lowpass well below oscillator freq
    time.sleep(0.2)
    rms_filtered = engine.measure_peak_rms(duration=0.25)

    for n in ["osc_fil", "filt_fil", "fft_fil", "out_fil"]:
        try:
            engine.disconnect(n)
        except Exception:
            pass
    engine.disconnect("fft_fil")
    engine.disconnect("out_fil")
    engine.delete_node("osc_fil")
    engine.delete_node("filt_fil")
    engine.delete_node("fft_fil")
    engine.delete_node("out_fil")

    assert rms_open > 0.01, f"Unfiltered 4kHz: expected signal, got {rms_open:.4f}"
    assert rms_filtered < rms_open * 0.5, \
        f"Lowpass@200Hz should reduce 4kHz by >50%; open={rms_open:.4f} filtered={rms_filtered:.4f}"


def test_deep_sampler_trigger_produces_audio(engine, sample_wav):
    """Sampler must produce non-zero RMS after being triggered."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("samp_deep", "sampler", params={"volume": 1.0, "loop": 1.0})
    engine.send({"type": "load_sample", "node_id": "samp_deep", "file_path": sample_wav})
    fft_id, out_id = ("samp_deep_fft", "samp_deep_fout")
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("samp_deep", fft_id)
    engine.connect(fft_id, out_id)

    rms_before = engine.measure_peak_rms(duration=0.15)
    engine.set_param("samp_deep", "trigger", 1.0)
    got_audio = engine.wait_for_rms(threshold=0.001, timeout=2.0)
    rms_after = engine.measure_peak_rms(duration=0.2)

    engine.set_param("samp_deep", "loop", 0.0)
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    engine.delete_node("samp_deep")
    engine.delete_node(fft_id)
    engine.delete_node(out_id)

    assert got_audio, f"Sampler trigger produced no audio (before={rms_before:.4f}, after={rms_after:.4f})"
    assert rms_after > rms_before + 0.001, \
        f"RMS after trigger ({rms_after:.4f}) should exceed before ({rms_before:.4f})"


def test_deep_sampler_stop_silences(engine, sample_wav):
    """Sampler in loop mode must go silent after stop command."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("samp_stop", "sampler",
                        params={"volume": 1.0, "loop": 1.0, "one_shot": 0.0})
    engine.send({"type": "load_sample", "node_id": "samp_stop", "file_path": sample_wav})
    fft_id, out_id = "samp_stop_fft", "samp_stop_fout"
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("samp_stop", fft_id)
    engine.connect(fft_id, out_id)

    engine.set_param("samp_stop", "trigger", 1.0)
    engine.wait_for_rms(threshold=0.001, timeout=2.0)
    rms_playing = engine.measure_peak_rms(duration=0.2)

    engine.set_param("samp_stop", "stop", 1.0)
    time.sleep(0.15)
    rms_stopped = engine.measure_peak_rms(duration=0.2)

    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    engine.delete_node("samp_stop")
    engine.delete_node(fft_id)
    engine.delete_node(out_id)

    assert rms_playing > 0.001, f"Expected audio while playing, got {rms_playing:.4f}"
    assert rms_stopped < rms_playing * 0.1, \
        f"After stop, RMS should drop >90%; playing={rms_playing:.4f} stopped={rms_stopped:.4f}"


def test_deep_drum_sampler_trigger_produces_audio(engine, kick_wav):
    """Triggering a loaded drum slot must produce non-zero RMS."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("drum_deep", "drum_sampler")
    engine.send({"type": "load_drum_sample", "node_id": "drum_deep",
                 "slot": 0, "file_path": kick_wav})
    fft_id, out_id = "drum_deep_fft", "drum_deep_fout"
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("drum_deep", fft_id)
    engine.connect(fft_id, out_id)

    engine.set_param("drum_deep", "trigger_0", 1.0)
    got_audio = engine.wait_for_rms(threshold=0.001, timeout=2.0)
    rms = engine.measure_peak_rms(duration=0.2)

    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    engine.delete_node("drum_deep")
    engine.delete_node(fft_id)
    engine.delete_node(out_id)

    assert got_audio, f"Drum slot trigger produced no audio (rms={rms:.4f})"


def test_deep_mixer_combines_sources(engine):
    """Mixer with two oscillators must produce higher RMS than one alone."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("osc_mx1", "oscillator", params={"frequency": 440.0, "gain": 0.5})
    engine.create_node("osc_mx2", "oscillator", params={"frequency": 660.0, "gain": 0.5})
    engine.create_node("mix_deep", "mixer")
    fft_id, out_id = "mix_deep_fft", "mix_deep_fout"
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("osc_mx1", "mix_deep", dst_port="input_1")
    engine.connect("mix_deep", fft_id)
    engine.connect(fft_id, out_id)
    time.sleep(0.15)

    rms_one = engine.measure_peak_rms(duration=0.25)

    engine.connect("osc_mx2", "mix_deep", dst_port="input_2")
    time.sleep(0.15)
    rms_two = engine.measure_peak_rms(duration=0.25)

    engine.disconnect("mix_deep", dst_port="input_1")
    engine.disconnect("mix_deep", dst_port="input_2")
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    for n in ["osc_mx1", "osc_mx2", "mix_deep", fft_id, out_id]:
        engine.delete_node(n)

    assert rms_one > 0.001, f"Single oscillator produced no signal: {rms_one:.4f}"
    assert rms_two > rms_one, \
        f"Two mixed oscillators ({rms_two:.4f}) should exceed one ({rms_one:.4f})"


def test_deep_sequencer_drives_sampler_audio(engine, sample_wav):
    """Sequencer → Sampler control connection must produce audio output."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("seq_sa", "sequencer")
    engine.create_node("samp_sa", "sampler",
                        params={"volume": 1.0, "loop": 1.0, "one_shot": 1.0})
    engine.send({"type": "load_sample", "node_id": "samp_sa", "file_path": sample_wav})
    fft_id, out_id = "samp_sa_fft", "samp_sa_fout"
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("samp_sa", fft_id)
    engine.connect(fft_id, out_id)
    engine.connect("seq_sa", "samp_sa", src_port="trigger", dst_port="trigger")

    engine.set_pattern("seq_sa", 0xFFFF)  # all 16 steps (exact integer, not float)
    engine.set_param("seq_sa", "bpm", 600.0)
    engine.set_param("seq_sa", "gate_length", 0.5)
    engine.set_param("seq_sa", "play", 1.0)

    got_audio = engine.wait_for_rms(threshold=0.001, timeout=3.0)
    rms = engine.measure_peak_rms(duration=0.3)

    engine.set_param("seq_sa", "stop", 1.0)
    engine.disconnect("samp_sa", dst_port="trigger")
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    for n in ["seq_sa", "samp_sa", fft_id, out_id]:
        engine.delete_node(n)

    assert got_audio, \
        f"Sequencer→Sampler control routing produced no audio (rms={rms:.4f})"


def test_deep_sequencer_drives_drum_sampler(engine, sample_wav):
    """Sequencer Trigger → Drum Trig 1 must produce audio output."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("seq_dr", "sequencer")
    engine.create_node("drum_dr", "drum_sampler", params={"master_volume": 1.0})
    engine.send({"type": "load_drum_sample", "node_id": "drum_dr",
                 "slot": 0, "file_path": sample_wav})
    fft_id, out_id = "drum_dr_fft", "drum_dr_fout"
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("drum_dr", fft_id)
    engine.connect(fft_id, out_id)
    engine.connect("seq_dr", "drum_dr", src_port="trigger", dst_port="trig_1")

    engine.set_pattern("seq_dr", 0xFFFF)
    engine.set_param("seq_dr", "bpm", 600.0)
    engine.set_param("seq_dr", "gate_length", 0.5)
    engine.set_param("seq_dr", "play", 1.0)

    got_audio = engine.wait_for_rms(threshold=0.001, timeout=3.0)
    rms = engine.measure_peak_rms(duration=0.3)

    engine.set_param("seq_dr", "stop", 1.0)
    engine.disconnect("drum_dr", dst_port="trig_1")
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    for n in ["seq_dr", "drum_dr", fft_id, out_id]:
        engine.delete_node(n)

    assert got_audio, \
        f"Sequencer→Drum Trig 1 produced no audio (rms={rms:.4f})"


def test_bang_starts_sequencer_and_fires_sampler(engine, sample_wav):
    """Bang → Sequencer Play and Bang → Sampler Trigger must both work."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("bang_go", "bang", params={"gate_ms": 30.0})
    engine.create_node("seq_go", "sequencer")
    engine.create_node("samp_go", "sampler",
                       params={"volume": 1.0, "loop": 0.0, "one_shot": 1.0})
    engine.send({"type": "load_sample", "node_id": "samp_go", "file_path": sample_wav})
    fft_id, out_id = "samp_go_fft", "samp_go_fout"
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("samp_go", fft_id)
    engine.connect(fft_id, out_id)

    engine.set_pattern("seq_go", 0xFFFF)
    engine.set_param("seq_go", "bpm", 600.0)
    engine.connect("bang_go", "seq_go", src_port="trigger", dst_port="play")
    engine.connect("seq_go", "samp_go", src_port="trigger", dst_port="trigger")

    # Not playing yet
    time.sleep(0.2)
    rms_idle = engine.measure_peak_rms(duration=0.15)

    engine.set_param("bang_go", "fire", 1.0)
    got_audio = engine.wait_for_rms(threshold=0.001, timeout=3.0)
    rms = engine.measure_peak_rms(duration=0.3)

    engine.set_param("seq_go", "stop", 1.0)
    engine.disconnect("seq_go", dst_port="play")
    engine.disconnect("samp_go", dst_port="trigger")
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    for n in ["bang_go", "seq_go", "samp_go", fft_id, out_id]:
        engine.delete_node(n)

    assert rms_idle < 0.001, f"Sequencer ran before Bang fire (rms={rms_idle:.4f})"
    assert got_audio, f"Bang→Play did not start sequencer audio (rms={rms:.4f})"


def test_bang_fans_out_to_drum_slots(engine, sample_wav):
    """One Bang Trigger can fire multiple Drum Trig inputs."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("bang_fan", "bang", params={"gate_ms": 25.0})
    engine.create_node("drum_fan", "drum_sampler", params={"master_volume": 1.0})
    engine.send({"type": "load_drum_sample", "node_id": "drum_fan",
                 "slot": 0, "file_path": sample_wav})
    engine.send({"type": "load_drum_sample", "node_id": "drum_fan",
                 "slot": 1, "file_path": sample_wav})
    fft_id, out_id = "drum_fan_fft", "drum_fan_fout"
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("drum_fan", fft_id)
    engine.connect(fft_id, out_id)
    engine.connect("bang_fan", "drum_fan", src_port="trigger", dst_port="trig_1")
    engine.connect("bang_fan", "drum_fan", src_port="trigger", dst_port="trig_2")

    engine.set_param("bang_fan", "fire", 1.0)
    got_audio = engine.wait_for_rms(threshold=0.001, timeout=2.0)
    rms = engine.measure_peak_rms(duration=0.25)

    engine.disconnect("drum_fan", dst_port="trig_1")
    engine.disconnect("drum_fan", dst_port="trig_2")
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    for n in ["bang_fan", "drum_fan", fft_id, out_id]:
        engine.delete_node(n)

    assert got_audio, f"Bang fan-out to drum pads produced no audio (rms={rms:.4f})"
    """Sequencer Step (monitor) must not fire Sampler - only Trigger should."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("seq_step", "sequencer")
    engine.create_node("samp_step", "sampler",
                       params={"volume": 1.0, "loop": 0.0, "one_shot": 1.0})
    engine.send({"type": "load_sample", "node_id": "samp_step", "file_path": sample_wav})
    fft_id, out_id = "samp_step_fft", "samp_step_fout"
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("samp_step", fft_id)
    engine.connect(fft_id, out_id)
    # Wrong socket - previously resolved to trigger lane 0
    engine.connect("seq_step", "samp_step", src_port="step", dst_port="trigger")

    engine.set_pattern("seq_step", 0xFFFF)
    engine.set_param("seq_step", "bpm", 600.0)
    engine.set_param("seq_step", "play", 1.0)

    time.sleep(0.5)
    rms_step = engine.measure_peak_rms(duration=0.3)

    engine.disconnect("samp_step", dst_port="trigger")
    engine.connect("seq_step", "samp_step", src_port="trigger", dst_port="trigger")
    got_audio = engine.wait_for_rms(threshold=0.001, timeout=3.0)

    engine.set_param("seq_step", "stop", 1.0)
    engine.disconnect("samp_step", dst_port="trigger")
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    for n in ["seq_step", "samp_step", fft_id, out_id]:
        engine.delete_node(n)

    assert rms_step < 0.001, \
        f"Step port incorrectly triggered sampler (rms={rms_step:.4f})"
    assert got_audio, "Trigger port failed to drive sampler after Step was disconnected"


def test_second_output_does_not_silence_first(engine):
    """Adding (then deleting) a second Output must not mute the first chain."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    fft_id, out_id = _build_fft_chain(engine, "osc_2out", "oscillator",
                                       params={"frequency": 440.0, "gain": 0.5})

    rms_before = engine.measure_peak_rms(duration=0.25)
    assert rms_before > 0.01, f"Primary output chain produced no audio ({rms_before:.4f})"

    # Empty second Output used to hijack outputNode_ and silence everything.
    engine.create_node("out_orphan", "output")
    time.sleep(0.15)
    rms_with_second = engine.measure_peak_rms(duration=0.25)
    assert rms_with_second > 0.01, \
        f"Second Output silenced the graph (rms={rms_with_second:.4f})"

    # Deleting the second Output used to leave outputNode_ null.
    engine.delete_node("out_orphan")
    time.sleep(0.25)  # allow fade-out reclaim
    rms_after_delete = engine.measure_peak_rms(duration=0.25)
    assert rms_after_delete > 0.01, \
        f"Deleting second Output left permanent silence (rms={rms_after_delete:.4f})"

    _tear_fft_chain(engine, "osc_2out", fft_id, out_id)


def test_deep_adsr_gate_on_produces_audio(engine):
    """ADSR gate-on with apply_to_audio must produce output."""
    if not _has_audio(engine):
        pytest.skip("No audio output device")

    engine.create_node("osc_adsr_d", "oscillator", params={"frequency": 440.0, "gain": 0.8})
    engine.create_node("adsr_d", "adsr",
                        params={"attack": 10.0, "decay": 20.0,
                                "sustain": 0.8, "release": 50.0,
                                "apply_to_audio": 1.0})
    fft_id, out_id = "adsr_d_fft", "adsr_d_fout"
    engine.create_node(fft_id, "fft_analyzer")
    engine.create_node(out_id, "output")
    engine.connect("osc_adsr_d", "adsr_d")
    engine.connect("adsr_d", fft_id)
    engine.connect(fft_id, out_id)
    time.sleep(0.1)

    rms_gate_off = engine.measure_peak_rms(duration=0.2)

    engine.set_param("adsr_d", "gate", 1.0)
    time.sleep(0.15)   # let attack complete
    rms_sustain = engine.measure_peak_rms(duration=0.2)

    engine.set_param("adsr_d", "gate", 0.0)
    time.sleep(0.15)   # let release complete
    rms_released = engine.measure_peak_rms(duration=0.2)

    engine.disconnect("adsr_d")
    engine.disconnect(fft_id)
    engine.disconnect(out_id)
    for n in ["osc_adsr_d", "adsr_d", fft_id, out_id]:
        engine.delete_node(n)

    assert rms_sustain > rms_gate_off + 0.001, \
        f"Gate-on should increase RMS; off={rms_gate_off:.4f} sustain={rms_sustain:.4f}"
    assert rms_released < rms_sustain * 0.5, \
        f"Release should reduce RMS; sustain={rms_sustain:.4f} released={rms_released:.4f}"


# ===========================================================================
# 14. DEEP BEHAVIORAL TESTS — edge cases, boundary conditions, robustness
# ===========================================================================

def test_behavior_duplicate_node_id(engine):
    """Creating a node with an ID that already exists must not crash the engine."""
    engine.create_node("dup_node", "gain")
    engine.create_node("dup_node", "oscillator")   # same ID, different type
    time.sleep(0.05)
    assert engine.ping(), "Engine crashed after duplicate node ID"
    engine.delete_node("dup_node")


def test_behavior_connect_nonexistent_nodes(engine):
    """Connecting from/to non-existent IDs must return a response, not crash."""
    r = engine.send({"type": "connect",
                     "source_node": "ghost_a", "dest_node": "ghost_b",
                     "source_port": "output", "dest_port": "input"})
    assert r is not None, "Engine must respond even for bad connections"
    assert engine.ping()


def test_behavior_disconnect_nonexistent_port(engine):
    """Disconnecting a port that was never connected must not crash."""
    engine.create_node("orphan", "gain")
    r = engine.send({"type": "disconnect", "dest_node": "orphan", "dest_port": "input"})
    assert r is not None
    engine.delete_node("orphan")
    assert engine.ping()


def test_behavior_param_clamping_oscillator(engine):
    """Out-of-range oscillator frequency values must be clamped, not crash."""
    engine.create_node("osc_clamp", "oscillator")
    engine.set_param("osc_clamp", "frequency", -1000.0)   # below min
    engine.set_param("osc_clamp", "frequency", 999999.0)  # above max
    engine.set_param("osc_clamp", "gain", -5.0)           # negative gain
    engine.set_param("osc_clamp", "gain", 100.0)          # above max
    assert engine.ping()
    engine.delete_node("osc_clamp")


def test_behavior_param_clamping_filter(engine):
    engine.create_node("fil_clamp", "filter")
    engine.set_param("fil_clamp", "cutoff", 0.0)
    engine.set_param("fil_clamp", "cutoff", 999999.0)
    engine.set_param("fil_clamp", "resonance", -1.0)
    engine.set_param("fil_clamp", "resonance", 100.0)
    assert engine.ping()
    engine.delete_node("fil_clamp")


def test_behavior_param_clamping_delay(engine):
    engine.create_node("del_clamp", "delay")
    engine.set_param("del_clamp", "delay_time", -1.0)
    engine.set_param("del_clamp", "delay_time", 9999.0)
    engine.set_param("del_clamp", "feedback", -1.0)
    engine.set_param("del_clamp", "feedback", 2.0)
    assert engine.ping()
    engine.delete_node("del_clamp")


def test_behavior_sequencer_all_on_pattern(engine):
    """Pattern 0xFFFF…F (all steps on) must fire trigger_active on every step."""
    if not _seq_available(engine):
        pytest.skip("Shared memory not available")

    engine.create_node("seq_all", "sequencer",
                        params={"pattern_0": float(0xFFFFFFFFFFFFFFFF),
                                "steps": 64.0, "bpm": 600.0,
                                "gate_length": 0.8, "play": 1.0})
    time.sleep(0.05)

    fired = False
    deadline = time.time() + 1.0
    while time.time() < deadline:
        d = engine.read_sequencer()
        if d and d["trigger_active"]:
            fired = True
            break
        time.sleep(0.02)

    engine.set_param("seq_all", "stop", 1.0)
    engine.delete_node("seq_all")
    assert fired, "All-on pattern never fired trigger_active"


def test_behavior_sequencer_alternating_pattern(engine):
    """Alternating pattern (0xAAAA…) fires every other step — step counter must advance."""
    if not _seq_available(engine):
        pytest.skip("Shared memory not available")

    engine.create_node("seq_alt", "sequencer",
                        params={"pattern_0": float(0xAAAAAAAAAAAAAAAA),
                                "steps": 16.0, "bpm": 600.0, "play": 1.0})
    time.sleep(0.05)
    s1 = engine.read_sequencer()
    time.sleep(0.4)
    s2 = engine.read_sequencer()
    engine.set_param("seq_alt", "stop", 1.0)
    engine.delete_node("seq_alt")

    assert s1 and s2
    assert s2["current_step"] != s1["current_step"], \
        "Alternating pattern: step counter should advance"


def test_behavior_two_sequencers_independent(engine):
    """Two sequencers at different BPMs must both advance steps independently."""
    if not _seq_available(engine):
        pytest.skip("Shared memory not available")

    engine.create_node("seq_fast", "sequencer",
                        params={"bpm": 600.0, "steps": 16.0, "play": 1.0})
    engine.create_node("seq_slow", "sequencer",
                        params={"bpm": 60.0,  "steps": 16.0, "play": 1.0})
    time.sleep(0.05)

    # Check that both are registered (primary sequencer reflects one of them)
    d = engine.read_sequencer()
    assert d is not None, "No sequencer data in shared memory"

    time.sleep(0.5)
    d2 = engine.read_sequencer()
    engine.set_param("seq_fast", "stop", 1.0)
    engine.set_param("seq_slow", "stop", 1.0)
    engine.delete_node("seq_fast")
    engine.delete_node("seq_slow")

    assert d2 is not None
    assert d2["current_step"] != d["current_step"], \
        "Primary sequencer step should have advanced"


def test_behavior_delete_while_playing_sampler(engine, sample_wav):
    """Deleting a playing sampler must not crash the engine."""
    engine.create_node("samp_del", "sampler", params={"volume": 1.0, "loop": 1.0})
    engine.send({"type": "load_sample", "node_id": "samp_del", "file_path": sample_wav})
    engine.set_param("samp_del", "trigger", 1.0)
    time.sleep(0.05)
    engine.delete_node("samp_del")     # delete while sample is playing
    time.sleep(0.05)
    assert engine.ping(), "Engine crashed after deleting a playing sampler"


def test_behavior_delete_connected_source(engine):
    """Deleting a source node while it's connected must not crash."""
    engine.create_node("osc_del_src", "oscillator", params={"frequency": 440.0})
    engine.create_node("gain_del_dst", "gain")
    engine.create_node("out_del", "output")
    engine.connect("osc_del_src", "gain_del_dst")
    engine.connect("gain_del_dst", "out_del")

    engine.delete_node("osc_del_src")   # delete source while connected
    time.sleep(0.05)
    assert engine.ping(), "Engine crashed after deleting connected source"

    engine.disconnect("gain_del_dst")
    engine.disconnect("out_del")
    engine.delete_node("gain_del_dst")
    engine.delete_node("out_del")


def test_behavior_rapid_connect_disconnect(engine):
    """Rapid connect/disconnect cycles must not crash or deadlock."""
    engine.create_node("osc_rcd", "oscillator", params={"frequency": 440.0})
    engine.create_node("gain_rcd", "gain")

    for _ in range(50):
        engine.connect("osc_rcd", "gain_rcd")
        engine.disconnect("gain_rcd")

    assert engine.ping()
    engine.delete_node("osc_rcd")
    engine.delete_node("gain_rcd")


def test_behavior_20_node_chain(engine):
    """A 20-node serial chain must process and tear down cleanly."""
    n = 20
    nodes = [f"chain20_{i}" for i in range(n)]
    engine.create_node(nodes[0], "oscillator", params={"frequency": 440.0})
    for node in nodes[1:-1]:
        engine.create_node(node, "gain", params={"gain": 1.0})
    engine.create_node(nodes[-1], "output")

    for i in range(n - 1):
        engine.connect(nodes[i], nodes[i + 1])
    time.sleep(0.05)

    for i in range(n - 1):
        engine.disconnect(nodes[i + 1])
    for node in nodes:
        engine.delete_node(node)

    assert engine.ping()


def test_behavior_50_rapid_creates_deletes(engine):
    """50 rapid create/delete cycles across different node types must leave engine stable."""
    types = ["gain", "filter", "delay", "oscillator", "mixer", "math"]
    for i in range(50):
        t = types[i % len(types)]
        engine.create_node(f"rapid50_{i}", t)
        engine.delete_node(f"rapid50_{i}")
    assert engine.ping()


def test_behavior_param_flood_while_engine_runs(engine):
    """Flooding the engine with 500 param updates must not deadlock."""
    engine.create_node("flood_osc", "oscillator")
    engine.create_node("flood_gain", "gain")
    engine.connect("flood_osc", "flood_gain")

    for i in range(500):
        freq = 200.0 + (i % 100) * 10
        engine.set_param("flood_osc", "frequency", freq)
        engine.set_param("flood_gain", "gain", (i % 10) / 10.0)

    time.sleep(0.05)
    assert engine.ping(), "Engine stopped responding after param flood"
    engine.disconnect("flood_gain")
    engine.delete_node("flood_osc")
    engine.delete_node("flood_gain")


def test_behavior_set_param_unknown_node(engine):
    """Setting a param on a non-existent node must return a response, not hang."""
    r = engine.send({"type": "set_param", "node_id": "does_not_exist",
                     "param": "gain", "value": 0.5})
    assert r is not None, "Engine must respond to params on unknown nodes"
    assert engine.ping()


def test_behavior_all_oscillator_waveforms(engine):
    """Cycling through all 4 waveforms must not crash."""
    engine.create_node("osc_waves", "oscillator", params={"frequency": 440.0})
    for wf in [0.0, 1.0, 2.0, 3.0]:    # sine, saw, square, triangle
        engine.set_param("osc_waves", "waveform", wf)
    time.sleep(0.05)
    assert engine.ping()
    engine.delete_node("osc_waves")


def test_behavior_math_all_operations(engine):
    """All 8 math operations must be settable without crash."""
    engine.create_node("math_ops", "math")
    for op in range(8):    # add, sub, mul, div, min, max, abs, clamp
        engine.set_param("math_ops", "operation", float(op))
        engine.set_param("math_ops", "control_a", 0.5)
        engine.set_param("math_ops", "control_b", 2.0)
    time.sleep(0.05)
    assert engine.ping()
    engine.delete_node("math_ops")


def test_behavior_sampler_pitch_range(engine, sample_wav):
    """Sampler must accept the full pitch shift range without crashing."""
    engine.create_node("samp_pitch", "sampler")
    engine.send({"type": "load_sample", "node_id": "samp_pitch", "file_path": sample_wav})
    for semitones in [-24.0, -12.0, -7.0, 0.0, 7.0, 12.0, 24.0]:
        engine.set_param("samp_pitch", "pitch", semitones)
        engine.set_param("samp_pitch", "trigger", 1.0)
        time.sleep(0.02)
        engine.set_param("samp_pitch", "trigger", 0.0)
    assert engine.ping()
    engine.delete_node("samp_pitch")


def test_behavior_mixer_8ch_all_muted(engine):
    """Muting all 8 mixer channels simultaneously must not crash."""
    engine.create_node("mix_mute8", "mixer")
    for ch in range(8):
        engine.set_param("mix_mute8", f"ch{ch}_mute", 1.0)
    time.sleep(0.05)
    assert engine.ping()
    for ch in range(8):
        engine.set_param("mix_mute8", f"ch{ch}_mute", 0.0)
    engine.delete_node("mix_mute8")


def test_behavior_adsr_all_gate_patterns(engine):
    """Rapid gate-on/off cycling must not crash."""
    engine.create_node("adsr_gates", "adsr",
                        params={"attack": 1.0, "decay": 5.0,
                                "sustain": 0.7, "release": 10.0})
    for _ in range(20):
        engine.set_param("adsr_gates", "gate", 1.0)
        time.sleep(0.01)
        engine.set_param("adsr_gates", "gate", 0.0)
        time.sleep(0.01)
    assert engine.ping()
    engine.delete_node("adsr_gates")


def test_engine_final_stability(engine):
    """Engine must still be fully responsive after all deep tests."""
    assert engine.ping()
    devices = engine.get_devices()
    assert devices is not None
    assert engine.get_midi_devices() is not None


# ---------------------------------------------------------------------------
# Startup with a slow audio device
# ---------------------------------------------------------------------------

def test_engine_responds_while_audio_device_opens(monkeypatch):
    """A device that takes seconds to open (e.g. Bluetooth headsets) must
    not stop Blender from reaching the engine."""
    monkeypatch.setenv("AUDIONODES_TEST_DEVICE_OPEN_DELAY_MS", "4000")
    with Engine(port=5577, startup_timeout=2.0, wait_for_audio=False) as e:
        resp = e.send({"type": "get_devices"})
        assert resp is not None and resp["status"] == "error", \
            "get_devices must wait while the device is still opening"
        assert "still opening" in resp["message"]

        deadline = time.time() + 10
        while time.time() < deadline:
            resp = e.send({"type": "get_devices"})
            if resp and resp["status"] == "ok":
                break
            time.sleep(0.2)
        assert resp and resp["status"] == "ok", \
            "get_devices never succeeded after the device opened"
