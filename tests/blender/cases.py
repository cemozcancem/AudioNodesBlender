"""
Test cases driven through the live Blender add-on. Each `case_*` function is
discovered by `run_in_blender.py` and executed inside Blender's Python.

Tests are split into two phases:
  Phase 1 — addon plumbing (no engine subprocess required).
  Phase 2 — engine integration (started via the real addon operator).

Phase 2 cases call `_require_engine()` which raises `Skip` if the engine
couldn't acquire an audio device (common in headless Blender on macOS).
"""
import os
import time
import tempfile
from pathlib import Path

import bpy

from .utils import Skip, apply_test_port, assert_eq, assert_true, wait_until

ADDON_MODULE = "blender_addon"
TESTSAMPLES = Path(__file__).resolve().parents[2] / "testsamples"

ALL_NODE_BL_IDNAMES = [
    "AudioInputNode",
    "AudioOutputNode",
    "AudioOscillatorNode",
    "AudioLFONode",
    "AudioFilterNode",
    "AudioGainNode",
    "AudioDelayNode",
    "AudioMixerNode",
    "AudioMathNode",
    "AudioFFTAnalyzerNode",
    "AudioValueOutputNode",
    "AudioMIDIInputNode",
    "AudioMIDICCNode",
    "AudioMIDIClockNode",
    "AudioMIDIOutputNode",
    "AudioMIDIMonitorNode",
    "AudioSequencerNode",
    "AudioBangNode",
    "AudioNLAScrubNode",
    "AudioSamplerNode",
    "AudioDrumSamplerNode",
    "AudioADSRNode",
    "AudioTriggerNode",
    "AudioAccumulatorNode",
    "AudioDecayNode",
    "AudioCameraCutterNode",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _addon():
    import importlib
    return importlib.import_module(ADDON_MODULE)


def _fresh_tree(name="TestTree"):
    for tree in list(bpy.data.node_groups):
        if tree.bl_idname == "AudioNodeTree":
            bpy.data.node_groups.remove(tree)
    return bpy.data.node_groups.new(name=name, type="AudioNodeTree")


def _ensure_engine_started():
    addon = _addon()
    if addon.engine_manager.is_engine_running() and addon.command_client.ping():
        return True, ""
    try:
        bpy.ops.audionodes.start_engine()
    except RuntimeError as exc:
        return False, f"start_engine operator failed: {exc}"
    if not addon.engine_manager.is_engine_running():
        return False, "engine process not running after start"
    if not addon.command_client.ping():
        return False, "engine running but ZMQ unreachable"
    return True, ""


def _require_engine():
    ok, why = _ensure_engine_started()
    if not ok:
        raise AssertionError(
            f"{why}. The engine should start cleanly via the addon's operator. "
            f"Check that pyzmq is installed and the engine binary is built "
            f"({addon_dir() / 'bin'})."
        )


def addon_dir():
    return Path(__file__).resolve().parents[2] / "blender_addon"


# ---------------------------------------------------------------------------
# Phase 1 — addon plumbing
# ---------------------------------------------------------------------------

def case_addon_enabled():
    addon = _addon()
    enabled = bpy.context.preferences.addons.get(ADDON_MODULE)
    assert_true(enabled is not None, f"{ADDON_MODULE} not enabled")
    assert_true(addon.bl_info["name"] == "Audio Nodes", "bl_info name mismatch")


def case_no_silent_registration_failures():
    """The addon swallows registration errors with try/except + a print, so a
    busted class still ships. Capture those warnings at addon_enable time."""
    from tests.blender.utils import REGISTER_FAILURES as failures
    assert_true(
        not failures,
        "addon printed register-time failures:\n  " + "\n  ".join(failures),
    )


def case_addon_disable_reenable():
    bpy.ops.preferences.addon_disable(module=ADDON_MODULE)
    assert_true(
        bpy.context.preferences.addons.get(ADDON_MODULE) is None,
        "addon still enabled after addon_disable",
    )
    bpy.ops.preferences.addon_enable(module=ADDON_MODULE)
    assert_true(
        bpy.context.preferences.addons.get(ADDON_MODULE) is not None,
        "addon failed to re-enable",
    )
    apply_test_port(ADDON_MODULE)  # re-enable reset the port preference
    # Round-trip proof: after re-enable, creating the tree type must work.
    tree = bpy.data.node_groups.new(name="ReenableProbe", type="AudioNodeTree")
    assert_eq(tree.bl_idname, "AudioNodeTree", "node tree type unusable after re-enable")
    bpy.data.node_groups.remove(tree)


def case_node_tree_creation():
    tree = _fresh_tree()
    assert_eq(tree.bl_idname, "AudioNodeTree", "wrong tree type")
    assert_eq(len(tree.nodes), 0, "fresh tree should have no nodes")


def case_create_every_node_type():
    tree = _fresh_tree()
    failed = []
    for bl_idname in ALL_NODE_BL_IDNAMES:
        try:
            n = tree.nodes.new(bl_idname)
            assert_true(
                hasattr(n, "engine_node_id") and n.engine_node_id,
                f"{bl_idname} did not set engine_node_id",
            )
        except Exception as exc:
            failed.append(f"{bl_idname}: {exc}")
    assert_true(not failed, "node creation failures:\n  " + "\n  ".join(failed))
    assert_eq(
        len(tree.nodes), len(ALL_NODE_BL_IDNAMES),
        "tree node count mismatch",
    )


def case_node_socket_layout():
    """A handful of representative nodes — sanity check that init() ran."""
    tree = _fresh_tree()
    osc = tree.nodes.new("AudioOscillatorNode")
    assert_true(any(s.bl_idname == "AudioSocketType" for s in osc.outputs),
                "Oscillator missing audio output socket")

    out = tree.nodes.new("AudioOutputNode")
    assert_true(any(s.bl_idname == "AudioSocketType" for s in out.inputs),
                "Output missing audio input socket")

    gain = tree.nodes.new("AudioGainNode")
    assert_true(any(s.bl_idname == "AudioSocketType" for s in gain.inputs),
                "Gain missing audio input socket")
    assert_true(any(s.bl_idname == "AudioSocketType" for s in gain.outputs),
                "Gain missing audio output socket")


def case_links_create_and_remove():
    tree = _fresh_tree()
    osc = tree.nodes.new("AudioOscillatorNode")
    gain = tree.nodes.new("AudioGainNode")
    out = tree.nodes.new("AudioOutputNode")

    src = next(s for s in osc.outputs if s.bl_idname == "AudioSocketType")
    g_in = next(s for s in gain.inputs if s.bl_idname == "AudioSocketType")
    g_out = next(s for s in gain.outputs if s.bl_idname == "AudioSocketType")
    o_in = next(s for s in out.inputs if s.bl_idname == "AudioSocketType")

    tree.links.new(src, g_in)
    tree.links.new(g_out, o_in)
    audio_links = [l for l in tree.links if l.from_socket.bl_idname == "AudioSocketType"]
    assert_eq(len(audio_links), 2, "expected 2 audio links")

    # Remove one and verify
    tree.links.remove(audio_links[0])
    audio_links_after = [l for l in tree.links if l.from_socket.bl_idname == "AudioSocketType"]
    assert_eq(len(audio_links_after), 1, "link removal failed")


def case_parameter_property_assignment():
    tree = _fresh_tree()
    gain = tree.nodes.new("AudioGainNode")
    gain.gain_db = -6.5
    assert_true(abs(gain.gain_db - (-6.5)) < 1e-4, "gain_db prop did not stick")
    gain.mute = True
    assert_true(gain.mute is True, "mute prop did not stick")


class _detached_file_handlers:
    """Detach the add-on's load_pre/load_post handlers for a save/open test.

    These cases open a .blend mid-suite. `on_file_load_pre` would stop the
    engine `run_in_blender` adopted, and `on_file_load_post` would schedule
    an auto-start for the incoming file - both wreck the surrounding suite.
    (Before 4.13 there was only one handler and it was called
    `on_blender_quit`, which was a misnomer: it never ran on quit.)
    """

    def __init__(self, addon):
        self._pairs = []
        for name, handler_list in (("on_file_load_pre", bpy.app.handlers.load_pre),
                                   ("on_file_load_post", bpy.app.handlers.load_post)):
            fn = getattr(addon, name, None)
            if fn is not None:
                self._pairs.append((fn, handler_list, fn in handler_list))

    def __enter__(self):
        for fn, handler_list, was_attached in self._pairs:
            if was_attached:
                handler_list.remove(fn)
        return self

    def __exit__(self, *exc):
        for fn, handler_list, was_attached in self._pairs:
            if was_attached and fn not in handler_list:
                handler_list.append(fn)
        return False


def case_save_load_roundtrip():
    addon = _addon()

    with _detached_file_handlers(addon):
        tree = _fresh_tree("SaveLoadTree")
        # Orphan node-groups are dropped on save unless something pins them.
        tree.use_fake_user = True
        tree.nodes.new("AudioOscillatorNode")
        tree.nodes.new("AudioGainNode")
        tree.nodes.new("AudioOutputNode")

        tmp = Path(tempfile.gettempdir()) / "audionodes_roundtrip.blend"
        if tmp.exists():
            tmp.unlink()
        bpy.ops.wm.save_as_mainfile(filepath=str(tmp), copy=True)
        assert_true(tmp.exists(), "blend file not written")

        bpy.ops.wm.open_mainfile(filepath=str(tmp))
        reopened = bpy.data.node_groups.get("SaveLoadTree")
        assert_true(reopened is not None, "tree missing after reload")
        assert_eq(reopened.bl_idname, "AudioNodeTree", "reopened tree wrong type")
        assert_eq(len(reopened.nodes), 3, "node count drifted across save/load")

        tmp.unlink()


def case_undo_redo_node_creation():
    """Smoke-test that node creation participates in Blender's undo stack."""
    tree = _fresh_tree("UndoTree")
    # Make sure the tree creation itself is committed before pushing the
    # operator that we'll undo.
    bpy.ops.ed.undo_push(message="setup")

    before = len(tree.nodes)
    tree.nodes.new("AudioGainNode")
    bpy.ops.ed.undo_push(message="add gain")

    # Note: bpy.ops.ed.undo() requires a UI context. In --background it
    # often raises RuntimeError. Treat that as Skip rather than fail.
    try:
        bpy.ops.ed.undo()
    except RuntimeError as exc:
        raise Skip(f"undo unavailable in background context: {exc}")

    tree_after = bpy.data.node_groups.get("UndoTree")
    if tree_after is None:
        # The whole tree got rolled back — that's still a valid undo.
        return
    assert_true(
        len(tree_after.nodes) <= before,
        f"undo did not remove the gain node (before={before}, after={len(tree_after.nodes)})",
    )


def case_tap_driver_refs_survive_delete():
    """A driver ref must follow its tap, and never be inherited by another.

    Regression: taps used to be addressed by list position, so deleting a
    tap shifted every later tap down one and the next tap added into the
    freed slot silently picked up the deleted tap's drivers.
    """
    from importlib import import_module
    fft_taps = import_module(f"{ADDON_MODULE}.nodes.effects.fft_taps")
    audio_drivers = import_module(f"{ADDON_MODULE}.audio_drivers")

    tree = _fresh_tree()
    node = tree.nodes.new("AudioFFTAnalyzerNode")
    doomed = node.add_tap(150.0, name="Doomed")
    keeper = node.add_tap(800.0, name="Keeper")

    doomed_ref = fft_taps.tap_ref(doomed)
    keeper_ref = fft_taps.tap_ref(keeper)
    keeper_id = keeper.tap_id
    assert_true("#" in keeper_ref and keeper_ref.endswith(keeper_id),
                f"tap_ref should be 'Name#id', got {keeper_ref!r}")
    assert_eq(fft_taps.resolve_tap_index(node.taps, keeper_ref), 1,
              "fresh ref should resolve to the tap it names")

    # Seed the channel the tap would publish so audio_tap() has a value.
    channels = bpy.context.scene.audio_nodes_data.channels
    name = f"{node.engine_node_id}_tap_{keeper_id}"
    if name not in channels:
        channels.add().name = name
    channels[name].value = 0.75
    assert_eq(round(audio_drivers.audio_tap(node.name, keeper_ref), 4), 0.75,
              "audio_tap did not read the tap's channel")

    # Delete the first tap: every later tap shifts down one.
    node.remove_tap(0)
    assert_eq(len(node.taps), 1, "remove_tap did not remove the tap")
    assert_eq(fft_taps.resolve_tap_index(node.taps, keeper_ref), 0,
              "ref should follow its tap across the shift")
    assert_eq(round(audio_drivers.audio_tap(node.name, keeper_ref), 4), 0.75,
              "audio_tap lost the tap after an earlier one was deleted")
    assert_eq(fft_taps.resolve_tap_index(node.taps, doomed_ref), -1,
              "a deleted tap's ref must resolve to nothing")
    assert_eq(audio_drivers.audio_tap(node.name, doomed_ref, -1.0), -1.0,
              "a deleted tap's driver must fall back to its default")

    # Re-add a tap: it lands where the deleted one was and must NOT inherit.
    replacement = node.add_tap(2000.0, name="Replacement")
    assert_true(replacement.tap_id not in (doomed_ref, ""),
                "new tap reused an existing tap_id")
    assert_eq(fft_taps.resolve_tap_index(node.taps, doomed_ref), -1,
              "deleted tap's ref latched onto the newly added tap")
    assert_eq(audio_drivers.audio_tap(node.name, doomed_ref, -1.0), -1.0,
              "newly added tap inherited the deleted tap's driver")
    assert_eq(round(audio_drivers.audio_tap(node.name, keeper_ref), 4), 0.75,
              "keeper ref broke after a tap was added")

    # Legacy integer refs still resolve, by position - which is exactly the
    # behaviour stable refs exist to replace, so pin it deliberately.
    assert_eq(fft_taps.resolve_tap_index(node.taps, 0), 0,
              "integer refs should still resolve by position")
    assert_eq(fft_taps.resolve_tap_index(node.taps, 99), -1,
              "out-of-range integer ref should resolve to nothing")


def case_tap_ref_resolution_forms():
    """tap_id, Name#id, bare name and legacy index all resolve."""
    from importlib import import_module
    fft_taps = import_module(f"{ADDON_MODULE}.nodes.effects.fft_taps")

    tree = _fresh_tree()
    node = tree.nodes.new("AudioFFTAnalyzerNode")
    lows = node.add_tap(120.0, name="Lows")
    node.add_tap(4000.0, name="Highs")
    tap_id = lows.tap_id

    for ref, want in ((tap_id, 0), (f"Lows#{tap_id}", 0), ("Lows", 0),
                      ("Highs", 1), (0, 0), ("0", 0),
                      (f"Renamed#{tap_id}", 0),      # id wins over the hint
                      (f"Lows#{'0' * 8}", -1),       # explicit id, gone
                      ("Nope", -1), (None, -1), ("", -1)):
        assert_eq(fft_taps.resolve_tap_index(node.taps, ref), want,
                  f"ref {ref!r} resolved wrong")

    # Renaming a tap keeps the id-bearing ref working, breaks the bare name.
    lows.name = "Sub"
    assert_eq(fft_taps.resolve_tap_index(node.taps, f"Lows#{tap_id}"), 0,
              "rename broke an id-bearing ref")
    assert_eq(fft_taps.resolve_tap_index(node.taps, "Lows"), -1,
              "bare name resolved to a renamed tap")

    # Duplicate names are ambiguous - refuse rather than guess.
    dup = node.add_tap(9000.0, name="Highs")
    assert_eq(fft_taps.resolve_tap_index(node.taps, "Highs"), -1,
              "ambiguous name should not resolve")
    assert_eq(fft_taps.resolve_tap_index(node.taps, fft_taps.tap_ref(dup)), 2,
              "id disambiguates duplicate names")


def case_audio_device_props_registered():
    """v3.0.2: the AudioNodesData scene property group must expose
    `input_device_name` and `output_device_name` StringProperties so the
    user's audio routing survives .blend save/reload."""
    scene = bpy.context.scene
    assert_true(
        hasattr(scene, "audio_nodes_data"),
        "scene.audio_nodes_data not registered",
    )
    data = scene.audio_nodes_data
    assert_true(
        hasattr(data, "input_device_name"),
        "AudioNodesData.input_device_name missing — "
        "device choice will not be persisted across .blend save/load",
    )
    assert_true(
        hasattr(data, "output_device_name"),
        "AudioNodesData.output_device_name missing — "
        "device choice will not be persisted across .blend save/load",
    )
    # Defaults should be empty (no device chosen yet on a fresh scene).
    assert_eq(data.input_device_name, "", "input_device_name should default to empty")
    assert_eq(data.output_device_name, "", "output_device_name should default to empty")


def case_audio_device_names_save_load_roundtrip():
    """Setting a device name on the scene and round-tripping through
    save/open must preserve the value — the whole point of v3.0.2."""
    addon = _addon()

    with _detached_file_handlers(addon):
        scene = bpy.context.scene
        data = scene.audio_nodes_data
        data.input_device_name = "TestVirtualInput-7"
        data.output_device_name = "TestVirtualOutput-42"

        tmp = Path(tempfile.gettempdir()) / "audionodes_devices_roundtrip.blend"
        if tmp.exists():
            tmp.unlink()
        bpy.ops.wm.save_as_mainfile(filepath=str(tmp), copy=True)
        assert_true(tmp.exists(), "blend file not written")

        bpy.ops.wm.open_mainfile(filepath=str(tmp))
        reopened = bpy.context.scene.audio_nodes_data
        assert_eq(
            reopened.input_device_name, "TestVirtualInput-7",
            "input_device_name did not survive save/reload — v3.0.2 fix broken",
        )
        assert_eq(
            reopened.output_device_name, "TestVirtualOutput-42",
            "output_device_name did not survive save/reload — v3.0.2 fix broken",
        )

        tmp.unlink()


def case_restore_saved_audio_devices_exists():
    """The engine_manager must expose `restore_saved_audio_devices` and
    `start_engine` must call it before `sync_all_nodes_to_engine`.

    Without this, reopening a saved project leaves the engine on the macOS
    system default for both input and output (the BlackHole feedback loop
    the user originally hit)."""
    addon = _addon()
    em = addon.engine_manager
    assert_true(
        hasattr(em, "restore_saved_audio_devices"),
        "engine_manager.restore_saved_audio_devices not defined — "
        "saved devices will not be re-applied on engine start",
    )
    assert_true(
        callable(em.restore_saved_audio_devices),
        "engine_manager.restore_saved_audio_devices is not callable",
    )

    # The function must run safely when called outside the engine startup
    # flow (e.g. with no engine attached) — it should just silently no-op
    # for any commands that fail.
    em.restore_saved_audio_devices()


def case_set_device_operator_writes_scene_props():
    """The `audionodes.set_device` operator must store the picked device
    name on `scene.audio_nodes_data` so it gets persisted to the .blend
    on save. This validates the panel-side half of the v3.0.2 fix.
    """
    _require_engine()
    addon = _addon()

    # Discover an available output device — we need at least one.
    devices = addon.command_client.get_devices()
    if not devices or not devices.get("output_devices"):
        raise Skip("engine reports no output devices")

    target = devices["output_devices"][0]
    bpy.ops.audionodes.set_device(device_type="output", device_name=target)

    scene = bpy.context.scene
    assert_eq(
        scene.audio_nodes_data.output_device_name, target,
        "set_device operator did not write output_device_name to the scene",
    )


def case_menu_categories_registered():
    expected = [
        "AUDIONODES_MT_add",
        "AUDIONODES_MT_add_io",
        "AUDIONODES_MT_add_generators",
        "AUDIONODES_MT_add_effects",
        "AUDIONODES_MT_add_analysis",
        "AUDIONODES_MT_add_midi",
        "AUDIONODES_MT_add_sequencer",
    ]
    missing = [n for n in expected if not hasattr(bpy.types, n)]
    assert_true(not missing, f"missing menus: {missing}")


# ---------------------------------------------------------------------------
# Phase 2 — engine integration
# ---------------------------------------------------------------------------

def case_engine_start_via_operator():
    _require_engine()
    addon = _addon()
    assert_true(addon.command_client.ping(), "engine ping failed after start")


def case_node_creation_syncs_to_engine():
    _require_engine()
    addon = _addon()
    tree = _fresh_tree("EngineSyncTree")
    osc = tree.nodes.new("AudioOscillatorNode")
    gain = tree.nodes.new("AudioGainNode")
    out = tree.nodes.new("AudioOutputNode")

    # The addon's node init() registers each node with the running engine.
    # If we got here, ZMQ create_node calls already happened. Verify with a
    # set_param round-trip that the engine knows about the gain node.
    assert_true(
        addon.command_client.set_parameter_immediate(gain.engine_node_id, "gain_db", -3.0),
        "engine did not accept set_parameter on a created node",
    )

    # Cleanup: removing nodes triggers delete_node on the engine.
    for n in (osc, gain, out):
        tree.nodes.remove(n)


def case_connection_syncs_to_engine():
    _require_engine()
    addon = _addon()
    tree = _fresh_tree("ConnTree")
    osc = tree.nodes.new("AudioOscillatorNode")
    gain = tree.nodes.new("AudioGainNode")
    out = tree.nodes.new("AudioOutputNode")

    src = next(s for s in osc.outputs if s.bl_idname == "AudioSocketType")
    g_in = next(s for s in gain.inputs if s.bl_idname == "AudioSocketType")
    g_out = next(s for s in gain.outputs if s.bl_idname == "AudioSocketType")
    o_in = next(s for s in out.inputs if s.bl_idname == "AudioSocketType")

    tree.links.new(src, g_in)
    tree.links.new(g_out, o_in)

    # The addon debounces sync via bpy.app.timers (~100ms). Force-tick.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        # Drive timers manually since we're in --background and there's no
        # event loop spinning.
        try:
            bpy.app.timers._run_pending = True  # noqa: SLF001
        except Exception:
            pass
        time.sleep(0.1)
        # Just yield — Blender's timers tick on screen redraws normally,
        # but bpy.app.timers also fire on time-based callbacks. As a fallback
        # call sync_all_connections directly.
        if any(l.is_valid for l in tree.links):
            break

    tree.sync_all_connections()
    # No direct way to query engine-side connection table, but the call must
    # not raise.


def case_parameter_set_propagates_to_engine():
    _require_engine()
    addon = _addon()
    tree = _fresh_tree("ParamTree")
    gain = tree.nodes.new("AudioGainNode")

    gain.gain_db = -12.0  # triggers send_param via update= callback
    # Give the param-sender thread a moment.
    time.sleep(0.1)
    # Round-trip via immediate set: must return True (engine knows the node).
    assert_true(
        addon.command_client.set_parameter_immediate(gain.engine_node_id, "gain_db", -9.0),
        "set_parameter_immediate failed",
    )


def case_sampler_load_wav():
    _require_engine()
    addon = _addon()
    wav = TESTSAMPLES / "kick.wav"
    if not wav.exists():
        raise Skip(f"missing fixture {wav}")
    tree = _fresh_tree("SamplerTree")
    sampler = tree.nodes.new("AudioSamplerNode")
    ok = addon.command_client.load_sample(sampler.engine_node_id, str(wav))
    assert_true(ok, "sample load was not queued")
    wait_until(lambda: addon.command_client.get_sample_load_state(
        sampler.engine_node_id).get("status") == "Loaded", desc="sample loaded")


def case_sequencer_play_operator():
    _require_engine()
    addon = _addon()
    tree = _fresh_tree("SeqTree")
    seq = tree.nodes.new("AudioSequencerNode")

    # The play operator looks up the active node via a tree walk; set a
    # property the operator expects if any. It currently iterates trees
    # so just calling it should be enough.
    try:
        bpy.ops.audionodes.sequencer_play()
    except Exception as exc:
        raise AssertionError(f"sequencer_play raised: {exc}")


def case_two_sequencers_report_independently():
    """One sequencer playing and one stopped must report different transports.

    Regression: every sequencer read the single global `seq_*` shared-memory
    field, which the engine fills from the primary (oldest) sequencer - so a
    second Step Sequencer echoed the first one's playing state, step and BPM.

    Note this pumps `_update_audio_data()` by hand. Blender's timers run from
    the event loop, which is blocked while this function does, so the 60fps
    publisher never fires during a test - the scene channels would stay empty
    no matter how long we waited.
    """
    _require_engine()
    addon = _addon()
    smr = addon.shared_memory_reader

    tree = _fresh_tree("SeqIndepTree")
    a = tree.nodes.new("AudioSequencerNode")
    b = tree.nodes.new("AudioSequencerNode")
    a.bpm, b.bpm = 200.0, 60.0
    addon.command_client.flush_queue()

    # Start only the first one, through the real UI operator.
    bpy.ops.audionodes.sequencer_play(node_name=a.name, tree_name=tree.name)
    addon.command_client.flush_queue()

    def slot_of(node):
        data = smr.read_visualization_data()
        if data is None:
            return None
        return data.get("sequencer_nodes", {}).get(node.engine_node_id)

    deadline = time.time() + 8.0
    slot_a = None
    while time.time() < deadline:
        slot_a = slot_of(a)
        if slot_a is not None and slot_a["is_playing"]:
            break
        time.sleep(0.1)

    if slot_a is None:
        data = smr.read_visualization_data()
        published = list((data or {}).get("sequencer_nodes", {}))
        raise Skip(
            f"engine published no slot for this sequencer (has {published}); "
            f"more than {smr.MAX_SEQUENCERS} sequencers may be live from "
            f"earlier cases"
        )

    try:
        assert_true(slot_a["is_playing"],
                    "the played sequencer never reported playing in its slot")

        slot_b = slot_of(b)
        assert_true(slot_b is not None,
                    "the second sequencer published no slot of its own")
        assert_true(not slot_b["is_playing"],
                    "the STOPPED sequencer reports playing - it is echoing the "
                    "other sequencer's transport (per-node slots regressed)")
        assert_eq(round(slot_a["bpm"]), 200,
                  "first sequencer's slot carries the wrong BPM")
        assert_eq(round(slot_b["bpm"]), 60,
                  "second sequencer's slot carries the first one's BPM")

        # And the Blender-side channels each node exposes must follow suit.
        smr._update_audio_data()
        channels = bpy.context.scene.audio_nodes_data.channels
        ch_a = channels.get(f"{a.engine_node_id}_playing")
        ch_b = channels.get(f"{b.engine_node_id}_playing")
        assert_true(ch_a is not None and ch_b is not None,
                    "per-node transport channels were not published")
        assert_true(ch_a.value > 0.5 and ch_b.value < 0.5,
                    f"channels disagree with the slots: "
                    f"a={ch_a.value} b={ch_b.value}")
    finally:
        bpy.ops.audionodes.sequencer_stop(node_name=a.name, tree_name=tree.name)


def case_audio_callback_advancing():
    _require_engine()
    # If the engine is running, its audio thread should be advancing the
    # sequence counter in shared memory.
    #
    # This used to open "/tmp/AudioNodesVisualization" directly, which does
    # not exist on Windows - the engine publishes a named CreateFileMapping
    # there, not a file - so the one case that proves the audio thread is
    # actually running silently skipped on the whole platform. Go through the
    # add-on's own reader instead, which knows both attach paths.
    smr = _addon().shared_memory_reader

    if not smr._open_shared_memory():
        raise Skip("shared memory not published by the engine yet")

    def read_seq():
        data = smr.read_visualization_data()
        return None if data is None else data.get("sequence")

    s0 = read_seq()
    if s0 is None:
        raise Skip("shared memory readable but carries no sequence counter yet")

    wait_until(
        lambda: read_seq() not in (None, s0),
        timeout=3.0,
        desc="audio callback to advance the sequence counter",
    )


def case_shared_memory_path_follows_engine_port():
    """Issue #5: each Blender reads the mapping of ITS engine. On the test
    port (5599) that is AudioNodesVisualization_5599, never the default
    file that a Blender the user has open writes to."""
    import sys
    smr = _addon().shared_memory_reader
    port = apply_test_port(ADDON_MODULE)
    expected = ("AudioNodesVisualization" if port == 5555
                else f"AudioNodesVisualization_{port}")
    assert_eq(smr.shared_memory_name(port), expected,
              "mapping name for the test port")
    if sys.platform == "win32":
        assert_eq(smr._get_shm_path(), expected, "reader mapping name")
    else:
        assert_eq(smr._get_shm_path(), f"/tmp/{expected}", "reader path")


def case_engine_stop_via_operator():
    addon = _addon()
    if not addon.engine_manager.is_engine_running():
        raise Skip("engine wasn't running — nothing to stop")
    bpy.ops.audionodes.stop_engine()
    wait_until(
        lambda: not addon.engine_manager.is_engine_running(),
        timeout=5.0,
        desc="engine process to terminate",
    )


def case_sequencer_output_channels_are_per_node():
    """Two Step Sequencers must not share one set of output channels.

    Regression: Trigger/Step/Playing mapped onto the GLOBAL seq_* channels,
    which the engine fills from the primary (oldest) sequencer - so the
    second sequencer's control outputs, and its step highlight, silently
    carried the first one's transport.
    """
    tree = _fresh_tree("SeqChannelTree")
    a = tree.nodes.new("AudioSequencerNode")
    b = tree.nodes.new("AudioSequencerNode")

    assert_true(a.engine_node_id != b.engine_node_id,
                "two sequencers should have distinct engine ids")

    for socket_name in ("Trigger", "Step", "Playing"):
        ch_a = a.get_output_channel_name(socket_name)
        ch_b = b.get_output_channel_name(socket_name)
        assert_true(ch_a and ch_b, f"{socket_name} produced an empty channel name")
        assert_true(ch_a != ch_b,
                    f"both sequencers map {socket_name} to {ch_a!r} - "
                    f"the per-node slot fix has regressed")
        assert_true(ch_a.startswith(a.engine_node_id),
                    f"{socket_name} channel {ch_a!r} is not scoped to its node")


SHARED_TYPES_H = (Path(__file__).resolve().parents[2]
                  / "engine" / "src" / "common" / "SharedTypes.h")


def _cpp_shared_layout():
    """Parse the shared-memory layout pinned by SharedTypes.h static_asserts."""
    import re
    if not SHARED_TYPES_H.is_file():
        raise Skip(f"SharedTypes.h not found at {SHARED_TYPES_H}; "
                   "cannot check the shared-memory layout")
    header = SHARED_TYPES_H.read_text()

    def size_of(struct):
        found = re.search(rf"sizeof\({struct}\)\s*==\s*(\d+)", header)
        assert_true(found, f"no sizeof({struct}) static_assert in SharedTypes.h")
        return int(found.group(1))

    offsets = {name: int(value) for name, value in re.findall(
        r"offsetof\(AudioVisualizationData,\s*(\w+)\)\s*==\s*(\d+)", header)}
    return {
        "size": size_of("AudioVisualizationData"),
        "seq_slot_size": size_of("SequencerSlot"),
        "offsets": offsets,
    }


def case_sequencer_slots_in_shared_memory():
    """The reader must expose a per-sequencer slot map, keyed by node id."""
    addon = _addon()
    smr = addon.shared_memory_reader

    # Compare against the layout the engine is actually built with, read from
    # the static_asserts in SharedTypes.h, not against hard-coded numbers.
    layout = _cpp_shared_layout()
    assert_eq(smr.SHARED_MEMORY_SIZE, layout["size"],
              "SHARED_MEMORY_SIZE does not match sizeof(AudioVisualizationData) "
              "in SharedTypes.h")
    assert_eq(smr.OFFSET_NUM_SEQUENCERS, layout["offsets"].get("num_sequencers"),
              "num_sequencers offset does not match SharedTypes.h")
    assert_eq(smr.OFFSET_SEQUENCER_SLOTS, layout["offsets"].get("sequencer_slots"),
              "sequencer_slots offset does not match SharedTypes.h")
    assert_eq(smr.SEQ_SLOT_SIZE, layout["seq_slot_size"],
              "SEQ_SLOT_SIZE does not match sizeof(SequencerSlot) in SharedTypes.h")

    data = smr.read_visualization_data()
    if data is None:
        raise Skip("no shared memory frame available")
    assert_true("sequencer_nodes" in data,
                "reader did not expose the per-sequencer slot map")
    assert_true(isinstance(data["sequencer_nodes"], dict),
                "sequencer_nodes should be a dict keyed by engine node id")


def case_value_output_writes_geometry_nodes_input():
    """A Value Output must actually land on a Geometry Nodes modifier input.

    Regression: Blender 5.0 moved Geometry Nodes modifier inputs off
    IDProperties. `mod.keys()` and `mod["Socket_9"] = v` now raise TypeError,
    and the write sat inside a bare `except Exception: pass` - so on 5.x every
    Value Output silently wrote nowhere. Meters moved, the node UI showed live
    numbers, and nothing in the scene ever changed.
    """
    smr = _addon().shared_memory_reader

    assert_true(hasattr(smr, "_write_geometry_nodes_input"),
                "the Geometry Nodes writer helper is gone - the 5.x fix has "
                "been reverted")

    group = bpy.data.node_groups.new("GNProbe", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out='INPUT',
                               socket_type='NodeSocketGeometry')
    group.interface.new_socket("Geometry", in_out='OUTPUT',
                               socket_type='NodeSocketGeometry')
    energy = group.interface.new_socket("Energy", in_out='INPUT',
                                        socket_type='NodeSocketFloat')

    mesh = bpy.data.meshes.new("GNProbeMesh")
    obj = bpy.data.objects.new("GNProbe", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new("GNProbe", 'NODES')
    modifier.node_group = group

    try:
        wrote = smr._write_geometry_nodes_input(modifier, "Energy", 0.75)
        assert_true(wrote, "writer reported failure on a plain float input")

        # Read it back the way the running Blender stores it.
        readback = None
        inputs = getattr(getattr(modifier, "properties", None), "inputs", None)
        if inputs is not None:                      # Blender 5.x
            readback = getattr(inputs, energy.identifier).value
        else:                                       # Blender 4.x
            readback = modifier[energy.identifier]
        assert_true(abs(readback - 0.75) < 1e-5,
                    f"input did not take the value: {readback!r}")

        # A name that matches nothing must report False, not silently "succeed".
        assert_true(
            not smr._write_geometry_nodes_input(modifier, "NoSuchInput", 1.0),
            "writer claimed success for an input that does not exist")
    finally:
        obj.modifiers.remove(modifier)
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
        bpy.data.node_groups.remove(group)


def case_engine_sync_survives_plain_blender_nodes():
    """A Frame or Reroute in an audio tree must not kill the reconcile pass.

    Regression: `ensure_engine_sync` built its live-id set with
    `n.engine_node_id` instead of `getattr(...)`. A NodeFrame has neither
    `engine_node_id` nor `node_type`, so it slipped past the
    BLENDER_ONLY_NODE_TYPES filter and raised AttributeError - killing the
    whole self-healing pass on every tick. Any tree with a frame in it lost
    the repair mechanism entirely, and the only trace was one throttled line
    in the console.
    """
    from blender_addon.nodes.core import node_tree as nt

    tree = _fresh_tree("FrameTree")
    tree.nodes.new("AudioOscillatorNode")
    tree.nodes.new("NodeFrame")
    tree.nodes.new("NodeReroute")

    # Must not raise, engine running or not.
    nt.ensure_engine_sync()

    live = {nid for t in bpy.data.node_groups
            if t.bl_idname == 'AudioNodeTree' for n in t.nodes
            if getattr(n, 'node_type', '') not in nt.BLENDER_ONLY_NODE_TYPES
            for nid in (getattr(n, 'engine_node_id', ''),) if nid}
    assert_true("" not in live,
                "empty engine ids leaked into the reconcile set")


def case_mixer_control_sockets_collapse():
    """A fresh 8ch Mixer must not show all sixteen Level/Pan inputs."""
    tree = _fresh_tree("MixerTree")
    mixer = tree.nodes.new("AudioMixerNode")

    def ctrl_sockets():
        return [s for s in mixer.inputs
                if s.bl_idname == "ControlSocketType"]

    all_ctrl = ctrl_sockets()
    assert_eq(len(all_ctrl), 16, "mixer should still have 16 control inputs")
    assert_true(all(s.hide for s in all_ctrl),
                "a freshly added mixer should hide every per-channel control "
                "input - that is the whole point of the collapse")

    # Expanding one channel reveals exactly that channel's pair.
    mixer.channels_expanded[2] = True
    mixer.update_channel_socket_visibility()
    visible = [s.name for s in ctrl_sockets() if not s.hide]
    assert_eq(sorted(visible), ["Ch3 Level", "Ch3 Pan"],
              f"expanding channel 3 should reveal only its pair, got {visible}")

    mixer.channels_expanded[2] = False
    mixer.update_channel_socket_visibility()

    # A linked socket stays visible even when its channel is collapsed,
    # so wiring never vanishes.
    lfo = tree.nodes.new("AudioLFONode")
    target = mixer.inputs.get("Ch5 Level")
    tree.links.new(lfo.outputs["Output"], target)
    mixer.update_channel_socket_visibility()
    assert_true(not target.hide,
                "a linked control input must stay visible when collapsed")
    assert_true(mixer.inputs.get("Ch5 Pan") is not None
                and not mixer.inputs.get("Ch5 Pan").hide,
                "the linked channel's pair should be revealed together")
    assert_true(mixer.inputs.get("Ch1 Level").hide,
                "unrelated channels should stay collapsed")


# ---------------------------------------------------------------------------
# Control nodes (Trigger / Accumulator / Decay / Camera Cutter) - 5.0.0
# ---------------------------------------------------------------------------

def _tick_controls(n=1, dt=1.0 / 60.0):
    """Run the control-node pass n times with a fixed dt (no engine needed)."""
    from blender_addon import shared_memory_reader as smr
    from blender_addon.nodes.control import control_nodes as cn
    audio_data = bpy.context.scene.audio_nodes_data
    for _ in range(n):
        cn._last_tick = time.monotonic() - dt
        smr._update_math_nodes(audio_data)      # same order as the timer
        smr._update_control_nodes(audio_data)
        smr._update_math_nodes(audio_data)
    return audio_data


def _chan(node, socket):
    ch = bpy.context.scene.audio_nodes_data.channels.get(
        node.get_output_channel_name(socket))
    return None if ch is None else ch.value


def case_control_nodes_are_blender_only():
    from blender_addon.nodes.core.node_tree import BLENDER_ONLY_NODE_TYPES
    tree = _fresh_tree()
    for idname in ("AudioTriggerNode", "AudioAccumulatorNode",
                   "AudioDecayNode", "AudioCameraCutterNode"):
        n = tree.nodes.new(idname)
        assert_true(n.node_type in BLENDER_ONLY_NODE_TYPES,
                    f"{idname} ({n.node_type}) must be Blender-only")
        assert_true(n.engine_node_id.startswith(n.node_type + "_"),
                    f"{idname} id shape: {n.engine_node_id}")


def case_control_chain_trigger_decay_accumulator():
    """Math(constant) -> Trigger -> Decay, and a Rate -> Accumulator."""
    tree = _fresh_tree()
    src = tree.nodes.new("AudioMathNode")          # constant source
    src.operation = 'ADD'
    trig = tree.nodes.new("AudioTriggerNode")
    dec = tree.nodes.new("AudioDecayNode")
    acc = tree.nodes.new("AudioAccumulatorNode")
    post = tree.nodes.new("AudioMathNode")          # downstream of a control node
    post.operation = 'MUL'
    post.input_b = 2.0
    tree.links.new(src.outputs["Value"], trig.inputs["Signal"])
    tree.links.new(trig.outputs["Trigger"], dec.inputs["Trigger"])
    tree.links.new(src.outputs["Value"], acc.inputs["Rate"])
    tree.links.new(acc.outputs["Value"], post.inputs["A"])
    acc.mode = 'NONE'
    dec.decay_time = 0.5

    src.input_a = 0.0
    _tick_controls(5)
    assert_eq(_chan(trig, "Count"), 0.0, "no hit while signal is 0")

    src.input_a = 1.0
    _tick_controls(1)
    assert_eq(_chan(trig, "Trigger"), 1.0, "trigger pulse on the rising edge")
    assert_eq(_chan(trig, "Count"), 1.0, "one hit counted")
    assert_true(abs(_chan(dec, "Value") - 1.0) < 1e-6, "decay jumped to peak")

    _tick_controls(29)   # +0.5 s at signal 1
    assert_eq(_chan(trig, "Count"), 1.0, "held signal must not re-fire")
    v = _chan(dec, "Value")
    assert_true(abs(v - 0.1) < 0.02, f"decay ~10% after decay time, got {v}")
    a = _chan(acc, "Value")
    assert_true(0.45 < a < 0.55, f"accumulator ~0.5 after 0.5 s at rate 1, got {a}")
    p = _chan(post, "Value")
    assert_true(abs(p - 2 * a) < 1e-6, f"Math after a control node is fresh ({p} vs {2*a})")


def _make_camera(name, loc, keyed_to=None):
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    bpy.context.scene.collection.objects.link(cam)
    cam.location = loc
    if keyed_to is not None:
        cam.keyframe_insert("location", frame=1)
        cam.location = keyed_to
        cam.keyframe_insert("location", frame=101)
        cam.location = loc
    return cam


def case_camera_cutter_switches_and_scrubs():
    from blender_addon.nodes.control import camera_cutter_node as ccn
    from blender_addon.nodes.control import control_nodes as cn
    scene = bpy.context.scene
    tree = _fresh_tree()
    a = _make_camera("CC_A", (0, 0, 0), keyed_to=(10, 0, 0))
    b = _make_camera("CC_B", (0, 5, 0))
    node = tree.nodes.new("AudioCameraCutterNode")
    for cam in (a, b):
        shot = node.shots.add()
        shot.camera = cam
        shot.name = cam.name
    msg = ccn.prepare_object(a)
    assert_eq(msg, "", "prepare_object should succeed")
    assert_true(a.animation_data.action is None, "action moved onto the strip")
    assert_true(a.animation_data.nla_tracks.get(ccn.TRACK_NAME) is not None,
                "shot NLA track created")
    assert_true(not ccn.needs_setup(a), "prepared camera needs no setup")

    node.shot_length = 1.0
    node.base_speed = 0.5          # 2 s per shot at Speed 0
    node.min_shot = 0.2
    node.auto_advance = False
    _tick_controls(60)             # 1 s -> progress 0.5
    assert_true(scene.camera == a, "first shot's camera is the scene camera")
    prog = _chan(node, "Progress")
    assert_true(abs(prog - 0.5) < 0.02, f"progress ~0.5, got {prog}")
    bpy.context.view_layer.update()
    x = a.matrix_world.translation.x
    assert_true(abs(x - 5.0) < 0.3, f"camera move scrubbed to the middle, x={x}")

    # Cut input via a constant Math node -> rising edge -> next shot
    src = tree.nodes.new("AudioMathNode")
    tree.links.new(src.outputs["Value"], node.inputs["Cut"])
    src.input_a = 1.0
    _tick_controls(1)
    assert_eq(_chan(node, "Shot"), 1.0, "cut to shot 1")
    assert_eq(_chan(node, "Cut"), 1.0, "cut pulse")
    assert_true(scene.camera == b, "scene camera switched on the cut")
    _tick_controls(5)
    assert_eq(_chan(node, "Shot"), 1.0, "held Cut input must not keep cutting")
    cn._STATE.pop(node.engine_node_id, None)


def case_camera_cutter_record_bakes_camera():
    from blender_addon.nodes.control import camera_cutter_node as ccn
    from blender_addon.nodes.control import control_nodes as cn
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.render.fps = 30
    tree = _fresh_tree()
    a = _make_camera("REC_A", (0, 0, 1), keyed_to=(4, 0, 1))
    b = _make_camera("REC_B", (0, 8, 1))
    node = tree.nodes.new("AudioCameraCutterNode")
    for cam in (a, b):
        s = node.shots.add()
        s.camera = cam
    ccn.prepare_object(a)
    node.shot_length = 0.3
    node.base_speed = 1.0
    node.auto_advance = True

    ccn._baked_camera_for(node, scene, a)
    key = node.engine_node_id + "_rec"
    cn._STATE[key] = ccn._Recorder(node, scene)
    node.is_recording = True
    for _ in range(40):            # ~0.8 s of real time
        _tick_controls(1)
        bpy.context.view_layer.update()
        time.sleep(0.02)
    rec = cn._STATE.pop(key)
    node.is_recording = False
    keys = ccn._finish_recording(node, rec, scene)
    baked = node.baked_camera
    assert_true(baked is not None and baked.type == 'CAMERA', "baked camera created")
    assert_true(keys >= 20, f"expected a key per tick, got {keys}")
    assert_true(len(rec.cut_frames) >= 1, "auto-advance cuts were recorded")
    assert_true(scene.frame_end > scene.frame_start, "frame range set to the take")
    fcs = ccn._fcurves_of(baked)
    assert_true(any(fc.data_path == "location" for fc in fcs), "location keyed")
    consts = sum(1 for fc in fcs for kp in fc.keyframe_points
                 if kp.interpolation == 'CONSTANT')
    assert_true(consts >= 1, "hard cut (CONSTANT key) at each cut")


# ---------------------------------------------------------------------------
# FFT tap Accumulate (#13)
#
# The engine is never needed: each case seeds the tap's published channel
# '<engine_id>_tap_<tap_id>' by hand and runs the reader's per-tick passes
# in the reader's own order (_tick_taps), with a fixed dt.
# ---------------------------------------------------------------------------

ACC_LABEL = " · Σ"        # "<Tap name> · Σ", pinned on purpose


def _taps_mod():
    from blender_addon.nodes.effects import fft_taps
    return fft_taps


def _acc_tree(name="AccTree", n_taps=1):
    tree = _fresh_tree(name)
    node = tree.nodes.new("AudioFFTAnalyzerNode")
    taps = [node.add_tap(200.0 * (i + 1), name=f"T{i + 1}")
            for i in range(n_taps)]
    return tree, node, taps


def _seed(name, value):
    channels = bpy.context.scene.audio_nodes_data.channels
    ch = channels.get(name)
    if ch is None:
        ch = channels.add()
        ch.name = name
    ch.value = value


def _channel(name):
    ch = bpy.context.scene.audio_nodes_data.channels.get(name)
    return None if ch is None else ch.value


def _seed_tap(node, tap, value):
    _seed(_taps_mod().tap_channel_name(node, tap), value)


def _acc_of(node, tap):
    return _channel(_taps_mod().acc_channel_name(node, tap))


def _tick_taps(n=1, dt=1.0 / 60.0):
    """Reader tick after the shared-memory read, in _update_audio_data order."""
    smr = _addon().shared_memory_reader
    audio_data = bpy.context.scene.audio_nodes_data
    for _ in range(n):
        smr._update_tap_accumulators(audio_data, dt)
        smr._update_math_nodes(audio_data)
        if smr._has_control_nodes():
            smr._update_control_nodes(audio_data, dt)
            smr._update_math_nodes(audio_data)
        smr._update_value_output_nodes(audio_data, {})
    return audio_data


def _tap_outputs(node):
    return [(s.tap_id, s.tap_param, s.name) for s in node.outputs
            if getattr(s, "tap_id", "")]


def _close(a, b, tol=1e-4):
    return a is not None and abs(a - b) <= tol


def case_tap_accumulate_off_by_default_and_old_files():
    """New taps and taps from older files have Accumulate off, nothing extra."""
    fft_taps = _taps_mod()
    addon = _addon()
    tree, node, (tap,) = _acc_tree("AccOldFile")
    assert_eq(tap.accumulate, False, "Accumulate must default to off")
    assert_eq(round(tap.accumulate_gain, 6), 1.0, "Rate Gain default")
    assert_eq(tap.accumulate_mode, 'NONE', "Mode default must be Unbounded")
    assert_eq((tap.accumulate_low, tap.accumulate_high), (0.0, 1.0),
              "Low/High defaults")
    assert_eq(_tap_outputs(node), [(tap.tap_id, "", "T1")],
              "a fresh tap has exactly one output socket")

    _seed_tap(node, tap, 0.5)
    _tick_taps(5)
    channel = fft_taps.acc_channel_name(node, tap)
    assert_true(_channel(channel) is None,
                "a tap with Accumulate off must not publish an _acc channel")
    assert_true(channel not in fft_taps._ACC_STATE,
                "a tap with Accumulate off must not hold a running total")

    # A file saved before Accumulate existed simply lacks these properties.
    for prop in ("accumulate", "accumulate_gain", "accumulate_mode",
                 "accumulate_low", "accumulate_high"):
        tap.property_unset(prop)
    vo = tree.nodes.new("AudioValueOutputNode")
    tree.links.new(node.get_tap_socket(tap.tap_id), vo.inputs["Value"])
    tree.use_fake_user = True
    tap_id, node_name, vo_name = tap.tap_id, node.name, vo.name

    with _detached_file_handlers(addon):
        tmp = Path(tempfile.gettempdir()) / "audionodes_acc_oldfile.blend"
        if tmp.exists():
            tmp.unlink()
        bpy.ops.wm.save_as_mainfile(filepath=str(tmp), copy=True)
        bpy.ops.wm.open_mainfile(filepath=str(tmp))
        tmp.unlink()

    tree = bpy.data.node_groups.get("AccOldFile")
    assert_true(tree is not None, "tree missing after reload")
    node = tree.nodes[node_name]
    tap = node.taps[0]
    assert_eq(tap.accumulate, False, "old-file tap loaded with Accumulate on")
    _tick_taps(1)
    node.sync_tap_sockets()
    assert_eq(_tap_outputs(node), [(tap_id, "", "T1")],
              "old-file tap gained or lost output sockets")
    link_from = tree.nodes[vo_name].inputs["Value"].links[0].from_socket
    assert_eq((link_from.tap_id, link_from.tap_param), (tap_id, ""),
              "old-file link from the tap output was disturbed")


def case_tap_accumulate_toggle_adds_and_removes_socket():
    fft_taps = _taps_mod()
    tree, node, (a, b) = _acc_tree("AccToggle", n_taps=2)
    plain_a = node.get_tap_socket(a.tap_id)
    plain_a_id = plain_a.identifier

    a.accumulate = True
    assert_eq(_tap_outputs(node),
              [(a.tap_id, "", "T1"),
               (a.tap_id, fft_taps.ACC_PARAM, "T1" + ACC_LABEL),
               (b.tap_id, "", "T2")],
              "Σ output must sit right after its tap's normal output")
    assert_eq(fft_taps.ACC_PARAM, "accumulated", "socket marker changed")
    assert_eq(node.get_tap_socket(a.tap_id).identifier, plain_a_id,
              "get_tap_socket must still return the plain output")
    acc_a = node.get_tap_acc_socket(a.tap_id)
    assert_true(acc_a is not None and acc_a.bl_idname == "ControlSocketType",
                "Σ output must be a control socket")
    assert_true(len({s.identifier for s in node.outputs}) == len(node.outputs),
                "output socket identifiers must be unique")

    b.accumulate = True
    assert_eq([(t, p) for t, p, _n in _tap_outputs(node)],
              [(a.tap_id, ""), (a.tap_id, "accumulated"),
               (b.tap_id, ""), (b.tap_id, "accumulated")],
              "two accumulating taps keep their sockets paired")

    # The label follows a rename.
    a.name = "Lows"
    node.sync_tap_sockets()
    assert_eq(node.get_tap_acc_socket(a.tap_id).name, "Lows" + ACC_LABEL,
              "Σ label did not follow the tap rename")

    # Links to Σ go when the toggle goes off; the plain link stays.
    vo_acc = tree.nodes.new("AudioValueOutputNode")
    vo_plain = tree.nodes.new("AudioValueOutputNode")
    tree.links.new(node.get_tap_acc_socket(a.tap_id), vo_acc.inputs["Value"])
    tree.links.new(node.get_tap_socket(a.tap_id), vo_plain.inputs["Value"])
    _seed_tap(node, a, 0.5)
    _tick_taps(3)
    channel = fft_taps.acc_channel_name(node, a)
    assert_true(channel in fft_taps._ACC_STATE and _channel(channel),
                "Σ did not run while on")

    a.accumulate = False
    assert_true(node.get_tap_acc_socket(a.tap_id) is None,
                "toggle off did not remove the Σ output")
    assert_eq([(t, p) for t, p, _n in _tap_outputs(node)],
              [(a.tap_id, ""), (b.tap_id, ""), (b.tap_id, "accumulated")],
              "toggle off disturbed other sockets")
    assert_true(not vo_acc.inputs["Value"].is_linked,
                "the link to the removed Σ output survived")
    assert_true(vo_plain.inputs["Value"].is_linked
                and vo_plain.inputs["Value"].links[0].from_socket.identifier
                == plain_a_id,
                "toggle off broke the plain tap output's link")
    assert_true(channel not in fft_taps._ACC_STATE,
                "toggle off kept the running total")
    assert_true(_channel(channel) is None, "toggle off kept the _acc channel")

    # Back on: right place again, and a fresh total (no state carried over).
    a.accumulate = True
    outs = _tap_outputs(node)
    assert_eq(outs[1][:2], (a.tap_id, "accumulated"),
              "Σ output not back right after its tap")
    _tick_taps(1)
    assert_true(_close(_acc_of(node, a), 0.5 / 60.0),
                f"re-enabled Σ did not restart from 0: {_acc_of(node, a)}")


def case_tap_accumulate_integrates_tap_times_gain_dt():
    from blender_addon.nodes.control import control_logic
    fft_taps = _taps_mod()
    tree, node, (tap,) = _acc_tree("AccIntegrate")
    tap.accumulate = True
    tap.accumulate_gain = 2.0
    _seed_tap(node, tap, 0.5)
    dt = 1.0 / 60.0
    for i in range(30):
        _tick_taps(1, dt)
        want = 0.5 * 2.0 * dt * (i + 1)
        got = _acc_of(node, tap)
        assert_true(_close(got, want),
                    f"tick {i + 1}: Σ = {got}, expected tap*gain*dt sum {want}")
    assert_true(_close(_acc_of(node, tap), 0.5), "Σ after 0.5 s at rate 1")

    # Uneven ticks integrate their real dt.
    _tick_taps(1, 0.1)
    assert_true(_close(_acc_of(node, tap), 0.6), "Σ ignored a longer dt")

    # The running total is an AccumulatorLogic, kept at 64-bit in Python.
    channel = fft_taps.acc_channel_name(node, tap)
    logic = fft_taps._ACC_STATE.get(channel)
    assert_true(isinstance(logic, control_logic.AccumulatorLogic),
                f"tap state is not AccumulatorLogic: {type(logic)!r}")
    assert_true(abs(logic.raw - 0.6) < 1e-6, f"raw total drifted: {logic.raw}")

    # Silence holds; a muted analyzer or a disabled tap holds too.
    _seed_tap(node, tap, 0.0)
    _tick_taps(10)
    held = _acc_of(node, tap)
    assert_true(_close(held, 0.6), f"silence moved Σ: {held}")
    _seed_tap(node, tap, 0.5)
    node.mute = True
    _tick_taps(10)
    assert_true(_close(_acc_of(node, tap), held), "muted analyzer moved Σ")
    node.mute = False
    tap.enabled = False
    _tick_taps(10)
    assert_true(_close(_acc_of(node, tap), held), "disabled tap moved Σ")
    tap.enabled = True
    _tick_taps(6)
    assert_true(_close(_acc_of(node, tap), held + 0.1),
                "Σ did not resume after re-enabling the tap")

    # Negative Rate Gain counts down.
    tap.accumulate_gain = -1.0
    _tick_taps(12)
    assert_true(_close(_acc_of(node, tap), held),
                f"negative gain: {_acc_of(node, tap)} vs {held}")


def case_tap_accumulate_plain_output_unchanged():
    """With Accumulate on, the tap's own output is exactly what it was."""
    smr = _addon().shared_memory_reader
    fft_taps = _taps_mod()
    audio_data = bpy.context.scene.audio_nodes_data
    tree, node, (a, b) = _acc_tree("AccPlain", n_taps=2)
    a.out_min, a.out_max = 1.0, 3.0           # final output is mapped
    a.gain = b.gain = 1.0
    plain = node.get_tap_socket(a.tap_id)
    plain_id = plain.identifier
    vo = tree.nodes.new("AudioValueOutputNode")
    tree.links.new(plain, vo.inputs["Value"])
    math = tree.nodes.new("AudioMathNode")
    math.operation = 'ADD'
    math.input_b = 0.0
    tree.links.new(plain, math.inputs["A"])

    bins = tuple([0.4] * 1024)
    data = {"fft_nodes": {}, "fft_left": bins, "fft_right": bins}

    def tick(n):
        for _ in range(n):
            smr._update_fft_tap_outputs(audio_data, data)
            _tick_taps(1)

    def snapshot():
        p = node.get_tap_socket(a.tap_id)
        return (round(_channel(fft_taps.tap_channel_name(node, a)), 6),
                round(_channel(fft_taps.tap_channel_name(node, b)), 6),
                round(_channel(vo.get_channel_name()), 6),
                round(_channel(math.get_channel_name()), 6),
                round(smr._resolve_control_source_value(audio_data, node, p), 6),
                p.identifier, p.name, p.tap_param)

    tick(5)
    before = snapshot()
    assert_true(abs(before[0] - 1.8) < 1e-4,
                f"seeded spectrum should map to 1 + 2*0.4, got {before[0]}")
    a.accumulate = True
    tick(5)
    after = snapshot()
    assert_eq(after, before, "Accumulate on changed the tap's normal output")
    assert_true(vo.inputs["Value"].links[0].from_socket.identifier == plain_id,
                "Accumulate on moved the plain tap link")
    # ... and Σ integrates that final (mapped) output: 1.8 * 5 ticks * dt.
    assert_true(_close(_acc_of(node, a), 1.8 * 5 / 60.0),
                f"Σ did not integrate the final output: {_acc_of(node, a)}")


def case_tap_accumulate_wrap_pingpong_clamp_bounds():
    tree, node, (tap,) = _acc_tree("AccBounds")
    tap.accumulate = True
    tap.accumulate_mode = 'WRAP'
    tap.accumulate_low, tap.accumulate_high = 0.25, 0.75
    tap.accumulate_gain = 3.0
    _seed_tap(node, tap, 1.0)                 # 3 units/s across a 0.5 span
    values = []
    for _ in range(120):
        _tick_taps(1)
        values.append(_acc_of(node, tap))
    assert_true(all(0.25 - 1e-6 <= v <= 0.75 + 1e-6 for v in values),
                f"Wrap left Low/High: min {min(values)}, max {max(values)}")
    wraps = sum(1 for p, v in zip(values, values[1:]) if v < p)
    assert_true(wraps >= 10, f"Wrap should have wrapped ~12 times, saw {wraps}")

    tap.accumulate_mode = 'PINGPONG'
    values = []
    for _ in range(120):
        _tick_taps(1)
        values.append(_acc_of(node, tap))
    assert_true(all(0.25 - 1e-6 <= v <= 0.75 + 1e-6 for v in values),
                f"Ping-Pong left Low/High: {min(values)}..{max(values)}")
    ups = sum(1 for p, v in zip(values, values[1:]) if v > p)
    downs = sum(1 for p, v in zip(values, values[1:]) if v < p)
    assert_true(ups > 10 and downs > 10, f"Ping-Pong did not bounce ({ups}/{downs})")

    tap.accumulate_mode = 'CLAMP'
    _tick_taps(60)
    assert_true(_close(_acc_of(node, tap), 0.75), "Clamp did not stop at High")
    tap.accumulate_gain = -3.0
    _tick_taps(60)
    assert_true(_close(_acc_of(node, tap), 0.25), "Clamp did not stop at Low")


def case_tap_accumulate_reset():
    fft_taps = _taps_mod()
    tree, node, (a, b) = _acc_tree("AccReset", n_taps=2)
    for t in (a, b):
        t.accumulate = True
        _seed_tap(node, t, 0.5)
    _tick_taps(30)
    assert_true(_close(_acc_of(node, a), 0.25), "setup: Σ did not run")
    other = _acc_of(node, b)

    res = bpy.ops.audionodes.reset_tap_accumulator(node_name=node.name,
                                                    tap_id=a.tap_id)
    assert_eq(res, {'FINISHED'}, "Reset operator did not run")
    assert_eq(_acc_of(node, a), 0.0, "Unbounded Reset must publish 0 at once")
    assert_eq(_acc_of(node, b), other, "Reset touched another tap")
    _tick_taps(1)
    assert_true(_close(_acc_of(node, a), 0.5 / 60.0),
                f"Reset only changed the channel, not the total: {_acc_of(node, a)}")

    a.accumulate_mode = 'WRAP'
    a.accumulate_low, a.accumulate_high = 0.3, 0.9
    _tick_taps(20)
    bpy.ops.audionodes.reset_tap_accumulator(node_name=node.name,
                                             tap_id=a.tap_id)
    assert_true(_close(_acc_of(node, a), 0.3), "bounded Reset must go to Low")
    logic = fft_taps._ACC_STATE[fft_taps.acc_channel_name(node, a)]
    assert_true(abs(logic.raw - 0.3) < 1e-6, f"total not reset: {logic.raw}")

    b.accumulate = False
    res = bpy.ops.audionodes.reset_tap_accumulator(node_name=node.name,
                                                    tap_id=b.tap_id)
    assert_eq(res, {'CANCELLED'}, "Reset on a tap without Accumulate")
    assert_true(_acc_of(node, b) is None, "Reset recreated a removed channel")


def case_tap_accumulate_remove_tap_clears_state():
    fft_taps = _taps_mod()
    tree, node, (a, b, c) = _acc_tree("AccRemove", n_taps=3)
    for t, v in ((a, 0.5), (b, 0.25), (c, 1.0)):
        t.accumulate = True
        _seed_tap(node, t, v)
    _tick_taps(30)
    got = [_acc_of(node, t) for t in (a, b, c)]
    for v, rate in zip(got, (0.5, 0.25, 1.0)):
        assert_true(_close(v, rate * 0.5), f"taps leaked into each other: {got}")

    ch_a = fft_taps.acc_channel_name(node, a)
    b_id, c_id = b.tap_id, c.tap_id
    node.remove_tap(0)        # 'a', 'b', 'c' RNA refs are stale from here
    assert_true(ch_a not in fft_taps._ACC_STATE, "removed tap kept its total")
    assert_true(_channel(ch_a) is None, "removed tap kept its _acc channel")
    assert_eq([(t, p) for t, p, _n in _tap_outputs(node)],
              [(b_id, ""), (b_id, "accumulated"),
               (c_id, ""), (c_id, "accumulated")],
              "remaining taps lost their Σ outputs")
    b = node.taps[0]
    c = node.taps[1]
    assert_true(_close(_acc_of(node, b), 0.125) and _close(_acc_of(node, c), 0.5),
                "removing a tap disturbed the others")

    # A new tap does not inherit the removed tap's total.
    d = node.add_tap(3000.0, name="D")
    d.accumulate = True
    _tick_taps(1)
    assert_eq(_acc_of(node, d), 0.0, "new tap inherited a total")

    # Deleting the analyzer drops every total it owned.
    prefix = node.engine_node_id + "_tap_"
    tree.nodes.remove(node)
    _tick_taps(1)
    left = [k for k in fft_taps._ACC_STATE if k.startswith(prefix)]
    assert_eq(left, [], "deleted analyzer left running totals behind")


def case_tap_accumulate_value_output_reads_acc_channel():
    """Value Output fed by Σ reads '_acc' and lands on Geometry Nodes."""
    smr = _addon().shared_memory_reader
    fft_taps = _taps_mod()
    tree, node, (tap,) = _acc_tree("AccVO")
    tap.accumulate = True
    tap.accumulate_gain = 4.0
    _seed_tap(node, tap, 0.25)                # rate 1 unit/s

    group = bpy.data.node_groups.new("AccGN", "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out='INPUT',
                               socket_type='NodeSocketGeometry')
    group.interface.new_socket("Geometry", in_out='OUTPUT',
                               socket_type='NodeSocketGeometry')
    spin = group.interface.new_socket("Spin", in_out='INPUT',
                                      socket_type='NodeSocketFloat')
    mesh = bpy.data.meshes.new("AccGNMesh")
    obj = bpy.data.objects.new("AccGN", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new("AccGN", 'NODES')
    modifier.node_group = group

    def readback():
        inputs = getattr(getattr(modifier, "properties", None), "inputs", None)
        if inputs is not None:                       # Blender 5.x
            return getattr(inputs, spin.identifier).value
        return modifier[spin.identifier]             # Blender 4.x

    try:
        acc = node.get_tap_acc_socket(tap.tap_id)
        vo = tree.nodes.new("AudioValueOutputNode")
        vo.smoothing = 0.0
        vo.target_object = obj
        vo.property_name = "Spin"
        tree.links.new(acc, vo.inputs["Value"])

        assert_eq(node.get_output_channel_name(acc.name),
                  fft_taps.acc_channel_name(node, tap),
                  "get_output_channel_name maps Σ to the wrong channel")
        assert_eq(node.get_output_channel_name(
                      node.get_tap_socket(tap.tap_id).name),
                  fft_taps.tap_channel_name(node, tap),
                  "get_output_channel_name broke for the plain output")

        _tick_taps(30)
        first = _acc_of(node, tap)
        assert_true(_close(first, 0.5), f"Σ after 0.5 s: {first}")
        assert_true(_close(_channel(vo.get_channel_name()), first),
                    f"Value Output read {_channel(vo.get_channel_name())}, "
                    f"not the Σ value {first} (plain tap is 0.25)")
        assert_true(_close(readback(), first),
                    f"Geometry Nodes input is {readback()}, expected {first}")

        _tick_taps(30)
        assert_true(_close(readback(), 1.0) and readback() > first,
                    f"Geometry Nodes input did not keep climbing: {readback()}")

        # "Drives:" names which of the tap's outputs is used.
        fft_taps._tap_targets_stamp = 0.0
        labels = fft_taps.get_tap_targets(node).get(0, [])
        assert_true(any(l.endswith(ACC_LABEL) and "AccGN" in l for l in labels),
                    f"tap targets do not mark the Σ link: {labels}")
    finally:
        obj.modifiers.remove(modifier)
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
        bpy.data.node_groups.remove(group)


def case_tap_accumulate_feeds_math_and_control_nodes_same_tick():
    import inspect
    smr = _addon().shared_memory_reader
    audio_data = bpy.context.scene.audio_nodes_data
    tree, node, (tap,) = _acc_tree("AccMath")
    tap.accumulate = True
    _seed_tap(node, tap, 0.5)
    acc = node.get_tap_acc_socket(tap.tap_id)
    plain = node.get_tap_socket(tap.tap_id)

    math = tree.nodes.new("AudioMathNode")
    math.operation = 'ADD'
    math.input_b = 0.0
    tree.links.new(acc, math.inputs["A"])
    integ = tree.nodes.new("AudioAccumulatorNode")
    integ.mode = 'NONE'
    tree.links.new(acc, integ.inputs["Rate"])

    dt = 1.0 / 60.0
    expected_integ = 0.0
    for i in range(10):
        _tick_taps(1, dt)
        sigma = _acc_of(node, tap)
        assert_true(_close(sigma, 0.5 * dt * (i + 1)), f"Σ tick {i + 1}: {sigma}")
        assert_eq(_channel(math.get_channel_name()), sigma,
                  f"tick {i + 1}: Math fed by Σ lags a tick")
        expected_integ += sigma * dt
        got = _chan(integ, "Value")
        assert_true(_close(got, expected_integ, 1e-6),
                    f"tick {i + 1}: Accumulator fed by Σ got {got}, "
                    f"expected this tick's Σ integrated ({expected_integ})")

    assert_eq(smr._resolve_control_source_value(audio_data, node, acc),
              _acc_of(node, tap), "control links from Σ resolve wrongly")
    assert_true(_close(smr._resolve_control_source_value(audio_data, node, plain),
                       0.5), "control links from the plain tap changed")

    # The real timer runs the tap pass between tap publishing and Math.
    src = inspect.getsource(smr._update_audio_data)
    i_taps = src.find("_update_fft_tap_outputs(")
    i_acc = src.find("_update_tap_accumulators(")
    i_math = src.find("_update_math_nodes(")
    i_ctrl = src.find("_update_control_nodes(")
    i_vo = src.find("_update_value_output_nodes(")
    assert_true(0 <= i_taps < i_acc < i_math < i_ctrl and i_acc < i_vo,
                f"reader order changed: taps {i_taps}, acc {i_acc}, "
                f"math {i_math}, control {i_ctrl}, value out {i_vo}")


def case_tap_accumulate_audio_tap_driver():
    from types import SimpleNamespace
    fft_taps = _taps_mod()
    audio_drivers = _addon().audio_drivers
    tree, node, (tap,) = _acc_tree("AccDriver")
    ref = fft_taps.tap_ref(tap)
    _seed_tap(node, tap, 0.5)

    # Accumulate off: every existing call form unchanged.
    assert_true(_close(audio_drivers.audio_tap(node.name, ref), 0.5),
                "audio_tap(node, ref) changed")
    assert_true(_close(audio_drivers.audio_tap(node.name, ref, -1.0), 0.5),
                "audio_tap(node, ref, default) changed")
    assert_true(_close(audio_drivers.audio_tap(node.name, 0), 0.5),
                "audio_tap(node, index) changed")
    assert_true(_close(audio_drivers.audio_tap(node.name), 0.5),
                "audio_tap(node) changed")
    assert_eq(audio_drivers.audio_tap(node.name, ref, -2.0, accumulated=True),
              -2.0, "accumulated=True with Accumulate off must give default")
    assert_eq(audio_drivers.audio_tap("NoSuchNode", ref, -3.0, accumulated=True),
              -3.0, "unknown node must give default")

    tap.accumulate = True
    _tick_taps(30)
    sigma = _acc_of(node, tap)
    assert_true(_close(sigma, 0.25), f"setup: Σ {sigma}")
    assert_eq(audio_drivers.audio_tap(node.name, ref, accumulated=True), sigma,
              "audio_tap(..., accumulated=True) does not read Σ")
    assert_eq(audio_drivers.audio_tap(node.name, ref, 0.0, True), sigma,
              "positional accumulated argument does not read Σ")
    assert_eq(audio_drivers.audio_tap(node.name, 0, accumulated=True), sigma,
              "index ref + accumulated=True")
    assert_true(_close(audio_drivers.audio_tap(node.name, ref), 0.5),
                "plain audio_tap changed with Accumulate on")

    # A real scripted driver on an object reads it.
    obj = bpy.data.objects.new("AccDriverProbe", None)
    bpy.context.scene.collection.objects.link(obj)
    try:
        fc_acc = obj.driver_add("location", 0)
        fc_acc.driver.type = 'SCRIPTED'
        fc_acc.driver.expression = (
            f'audio_tap("{node.name}", "{ref}", accumulated=True)')
        fc_lvl = obj.driver_add("location", 1)
        fc_lvl.driver.type = 'SCRIPTED'
        fc_lvl.driver.expression = f'audio_tap("{node.name}", "{ref}")'
        bpy.context.view_layer.update()
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)
        assert_true(_close(obj.location.x, sigma),
                    f"driver with accumulated=True read {obj.location.x}")
        assert_true(_close(obj.location.y, 0.5),
                    f"plain driver read {obj.location.y}")

        fft_taps._tap_targets_stamp = 0.0
        labels = fft_taps.get_tap_targets(node).get(0, [])
        marked = [l for l in labels if l.endswith(ACC_LABEL)]
        assert_eq(len(marked), 1, f"Σ driver not marked once in {labels}")
        assert_eq(len(labels), 2, f"both drivers should be listed: {labels}")
    finally:
        bpy.data.objects.remove(obj)

    # Copy Driver Expression: plain text unchanged, Σ adds accumulated=True.
    op_cls = audio_drivers.AUDIONODES_OT_copy_tap_driver

    def copied(accumulated, paired=False):
        ctx = SimpleNamespace(window_manager=SimpleNamespace(clipboard=""))
        fake = SimpleNamespace(tap_ref=ref, node_ref="", node_name=node.name,
                               tap_index=0, accumulated=accumulated,
                               paired=paired, report=lambda *_a: None,
                               _lookup_ref=lambda: ref)
        op_cls.execute(fake, ctx)
        return ctx.window_manager.clipboard

    assert_eq(copied(False), f'audio_tap("{node.name}", "{ref}")',
              "plain copied expression changed")
    assert_eq(copied(False, paired=True), f'audio_tap("{node.name}", "{ref}")',
              "paired level button must copy the plain expression")
    assert_eq(copied(True), f'audio_tap("{node.name}", "{ref}", accumulated=True)',
              "Σ copy button expression")
    desc = op_cls.description
    assert_eq(desc(None, SimpleNamespace(accumulated=False, paired=False)), "",
              "unpaired button must keep Blender's default tooltip")
    assert_true(desc(None, SimpleNamespace(accumulated=True, paired=False)),
                "Σ button needs its own tooltip")
    assert_true(desc(None, SimpleNamespace(accumulated=False, paired=True)),
                "paired level button needs its own tooltip")


def case_prune_keeps_live_tap_acc_channels():
    """prune_stale_channels must not delete a live tap's '_acc' channel.

    The id regex is greedy: with an all-letter engine id and tap id, it
    reads '<id>_tap_<tap_id>' as the node id, finds no such node, and used
    to delete the channel.
    """
    smr = _addon().shared_memory_reader
    tree, node, (tap,) = _acc_tree("AccPrune")
    node.engine_node_id = "fft_analyzer_abcdefab"
    tap.tap_id = "fedcbaee"
    node.sync_tap_sockets()
    live_plain = "fft_analyzer_abcdefab_tap_fedcbaee"
    live_acc = live_plain + "_acc"
    dead = ("fft_analyzer_00000000_tap_fedcbaee_acc",
            "fft_analyzer_dddddddd_tap_eeeeeeee_acc",
            "fft_analyzer_dddddddd_tap_eeeeeeee")
    for name in (live_plain, live_acc) + dead:
        _seed(name, 0.5)

    try:
        smr.prune_stale_channels()
        assert_true(_channel(live_plain) is not None, "pruned a live tap channel")
        assert_true(_channel(live_acc) is not None,
                    "pruned a live tap's _acc channel")
        for name in dead:
            assert_true(_channel(name) is None, f"kept dead channel {name}")

        # Same through the real feature path.
        tap.accumulate = True
        _seed(live_plain, 0.5)
        _tick_taps(3)
        smr.prune_stale_channels()
        assert_true(_close(_channel(live_acc), 0.5 * 3 / 60.0),
                    f"live Σ channel lost to prune: {_channel(live_acc)}")
    finally:
        tree.nodes.remove(node)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover():
    """Return [(name, fn), ...] preserving definition order."""
    import inspect
    mod = inspect.getmodule(discover)
    pairs = []
    for name, obj in vars(mod).items():
        if name.startswith("case_") and callable(obj):
            pairs.append((name, obj))
    # Preserve source order (definition order in this module)
    pairs.sort(key=lambda p: inspect.getsourcelines(p[1])[1])
    return pairs
