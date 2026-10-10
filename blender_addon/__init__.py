bl_info = {
    "name": "Audio Nodes",
    "author": "Audio Nodes Team",
    "version": (5, 0, 0),
    "blender": (5, 0, 0),
    "location": "Editor Type > Audio Nodes",
    "description": "Real-time audio processing and visualization with a node-based workflow",
    "category": "Audio",
}

import bpy
from bpy.props import PointerProperty

# Check and install dependencies first
from . import dependencies
_deps_ok, _deps_missing = dependencies.ensure_dependencies()
if not _deps_ok:
    print(f"Audio Nodes: WARNING - Missing dependencies: {_deps_missing}")
    print("Audio Nodes: Please install manually: pip install " + " ".join(_deps_missing))

from . import preferences
from . import engine_manager
from . import command_client
from .nodes.core import node_tree
from .nodes.core import node_base
from .nodes.core import input_node
from .nodes.core import output_node
from .nodes.core import value_output_node
from .nodes.core import nla_scrub_node
from .nodes.effects import filter_node
from .nodes.effects import gain_node
from .nodes.effects import delay_node
from .nodes.effects import mixer_node
from .nodes.effects import math_node
from .nodes.effects import fft_taps
from .nodes.effects import fft_analyzer_node
from .nodes.midi import midi_input_node
from .nodes.midi import midi_cc_node
from .nodes.midi import midi_clock_node
from .nodes.midi import midi_output_node
from .nodes.midi import midi_monitor_node
from .nodes.samplers import sampler_node
from .nodes.samplers import drum_sampler_node
from .nodes.samplers import adsr_node
from .nodes.timing import oscillator_node
from .nodes.timing import sequencer_node
from .nodes.timing import lfo_node
from .nodes.timing import bang_node
from .nodes.control import control_nodes
from .nodes.control import camera_cutter_node
from .ui import editor
from .ui import panels
from .ui import menus
from .ui import spectrum_overlay
from .ui import node_graphics
from . import audio_drivers

# All classes to register
classes = [
    # Preferences
    preferences.AUDIONODES_OT_patch_plist,
    preferences.AUDIONODES_OT_reset_tcc,
    preferences.AUDIONODES_OT_request_mic_permission,
    preferences.AudioNodesPreferences,
    
    # Property groups
    node_tree.AudioChannelData,
    node_tree.AudioNodesData,
    
    # Node tree and sockets
    node_tree.AudioNodeTree,
    node_tree.AudioSocket,
    node_tree.ControlSocket,
    
    # I/O Nodes
    input_node.AudioInputNode,
    output_node.AudioOutputNode,
    
    # Generator Nodes
    oscillator_node.AudioOscillatorNode,
    lfo_node.AudioLFONode,
    
    # Effect Nodes
    filter_node.AudioFilterNode,
    gain_node.AudioGainNode,
    delay_node.AudioDelayNode,
    mixer_node.AudioMixerNode,
    math_node.AudioMathNode,
    
    # Analysis Nodes (tap PropertyGroup must register before the node that
    # uses it in a CollectionProperty)
    fft_taps.AudioFrequencyTap,
    fft_taps.AUDIONODES_UL_taps,
    fft_analyzer_node.AudioFFTAnalyzerNode,
    
    # Output Nodes
    value_output_node.AudioValueOutputNode,
    nla_scrub_node.AudioNLAScrubNode,
    
    # Control Nodes (Blender-only). The shot PropertyGroup must register
    # before the Camera Cutter node that holds it in a CollectionProperty.
    control_nodes.AudioTriggerNode,
    control_nodes.AudioAccumulatorNode,
    control_nodes.AudioDecayNode,
    control_nodes.AUDIONODES_OT_copy_channel_driver,
    camera_cutter_node.AudioCameraShot,
    camera_cutter_node.AUDIONODES_UL_camera_shots,
    camera_cutter_node.AudioCameraCutterNode,
    camera_cutter_node.AUDIONODES_OT_camera_cutter_add_shot,
    camera_cutter_node.AUDIONODES_OT_camera_cutter_remove_shot,
    camera_cutter_node.AUDIONODES_OT_camera_cutter_move_shot,
    camera_cutter_node.AUDIONODES_OT_camera_cutter_setup,
    camera_cutter_node.AUDIONODES_OT_camera_cutter_next,
    camera_cutter_node.AUDIONODES_OT_camera_cutter_record,
    camera_cutter_node.AUDIONODES_OT_camera_cutter_use_baked,

    # MIDI Nodes
    midi_input_node.AudioMIDIInputNode,
    midi_input_node.AUDIONODES_MT_midi_input_devices,
    midi_input_node.AUDIONODES_OT_select_midi_device,
    midi_input_node.AUDIONODES_OT_refresh_midi_devices,
    midi_cc_node.AudioMIDICCNode,
    midi_clock_node.AudioMIDIClockNode,
    midi_clock_node.AUDIONODES_OT_dummy,
    midi_output_node.AudioMIDIOutputNode,
    midi_output_node.AUDIONODES_OT_send_midi_note,
    midi_monitor_node.AudioMIDIMonitorNode,
    
    # Sequencer/Sampler Nodes
    sequencer_node.AudioSequencerNode,
    bang_node.AudioBangNode,
    bang_node.AUDIONODES_OT_bang_fire,
    sampler_node.AudioSamplerNode,
    drum_sampler_node.AudioDrumSamplerNode,
    adsr_node.AudioADSRNode,
    
    # UI
    editor.AUDIONODES_OT_new_tree,
    panels.AUDIONODES_PT_devices,
    panels.AUDIONODES_PT_engine,
    panels.AUDIONODES_OT_refresh_audio_devices,
    menus.AUDIONODES_MT_add,
    menus.AUDIONODES_MT_add_io,
    menus.AUDIONODES_MT_add_generators,
    menus.AUDIONODES_MT_add_effects,
    menus.AUDIONODES_MT_add_analysis,
    menus.AUDIONODES_MT_add_midi,
    menus.AUDIONODES_MT_add_sequencer,
    menus.AUDIONODES_MT_add_control,
    
    # Operators
    engine_manager.AUDIONODES_OT_start_engine,
    engine_manager.AUDIONODES_OT_stop_engine,
    panels.AUDIONODES_OT_set_device,
    sequencer_node.AUDIONODES_OT_sequencer_play,
    sequencer_node.AUDIONODES_OT_sequencer_stop,
    sequencer_node.AUDIONODES_OT_sequencer_reset,
    sequencer_node.AUDIONODES_OT_toggle_step,
    sequencer_node.AUDIONODES_OT_fold_steps_into_loop,
    sequencer_node.AUDIONODES_OT_clear_steps_beyond_loop,
    sampler_node.AUDIONODES_OT_load_sample,
    sampler_node.AUDIONODES_OT_send_sample_path,
    sampler_node.AUDIONODES_OT_trigger_sampler,
    drum_sampler_node.AUDIONODES_OT_trigger_drum_slot,
    drum_sampler_node.AUDIONODES_OT_load_drum_sample,
    adsr_node.AUDIONODES_OT_trigger_adsr,
    adsr_node.AUDIONODES_OT_release_adsr,
    spectrum_overlay.AUDIONODES_OT_add_tap,
    spectrum_overlay.AUDIONODES_OT_remove_tap,
    spectrum_overlay.AUDIONODES_OT_tap_to_value_output,
    audio_drivers.AUDIONODES_OT_copy_tap_driver,
    nla_scrub_node.AUDIONODES_OT_nla_setup,
]


def _file_has_audio_tree() -> bool:
    """Does the currently loaded .blend contain an Audio Nodes tree?"""
    return any(t.bl_idname == 'AudioNodeTree' for t in bpy.data.node_groups)


def _auto_start_enabled() -> bool:
    prefs = bpy.context.preferences.addons.get(__package__)
    if not prefs:
        return False
    return bool(getattr(prefs.preferences, "auto_start_engine", False))


def _maybe_auto_start():
    """Start the engine if the preference is on and this file uses Audio Nodes.

    Deferred onto a timer rather than run inline in the load_post handler:
    start_engine() waits up to 15s for the engine to answer on ZMQ, and
    blocking there would freeze Blender in the middle of opening a file.
    Only fires for files that actually contain an Audio Nodes tree - we do
    not grab the audio device for every unrelated .blend the user opens.
    """
    try:
        if not _auto_start_enabled():
            return None
        if not _file_has_audio_tree():
            return None
        if engine_manager.is_engine_running():
            return None
        print("Audio Nodes: auto-starting engine for this file...")
        engine_manager.start_engine()
    except Exception as e:
        print(f"Audio Nodes: auto-start failed: {e!r}")
    return None  # one shot


@bpy.app.handlers.persistent
def on_file_load_pre(dummy):
    """Stop the engine before a different .blend is loaded.

    The engine holds the node graph of the file being replaced, so it has
    to go. on_file_load_post restarts it for the incoming file when
    auto-start is enabled - without that pair, opening a saved Audio Nodes
    project always landed with the engine stopped.
    """
    print("Audio Nodes: file load - stopping engine...")
    engine_manager.stop_engine()


def _refresh_collapsible_sockets():
    """Re-apply socket visibility rules to nodes loaded from a .blend.

    Socket `hide` flags are saved in the file, so a mixer authored before
    4.14 loads with all sixteen per-channel control inputs showing. The
    node's own update() only fires on a tree change, which may never come.
    """
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            fn = getattr(node, "update_channel_socket_visibility", None)
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                pass


def _migrate_sequencer_meters():
    """Convert pre-4.15 sequencers to the derived time-signature grid.

    The loop length now comes from the time signature instead of a separate
    step count, so a file saved with, say, 32 steps in 4/4 is rewritten to
    the grid + bars that produce the same 32 steps. Runs once per node
    (each one records that it has been migrated).
    """
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            if node.bl_idname != 'AudioSequencerNode':
                continue
            try:
                node.migrate_meter()
            except Exception as e:
                print(f"Audio Nodes: sequencer meter migration failed "
                      f"for '{node.name}': {e!r}")


def _post_load_housekeeping():
    """Deferred tidy-up for a freshly loaded file."""
    try:
        _migrate_sequencer_meters()
    except Exception as e:
        print(f"Audio Nodes: sequencer meter migration failed: {e!r}")
    try:
        from . import shared_memory_reader
        shared_memory_reader.prune_stale_channels()
    except Exception as e:
        print(f"Audio Nodes: channel prune failed: {e!r}")
    try:
        _refresh_collapsible_sockets()
    except Exception as e:
        print(f"Audio Nodes: socket visibility refresh failed: {e!r}")
    return _maybe_auto_start()


@bpy.app.handlers.persistent
def on_file_load_post(dummy):
    """Tidy up and restart the engine for the newly loaded file."""
    bpy.app.timers.register(_post_load_housekeeping, first_interval=0.5)


def register():
    print("Audio Nodes: Registering classes...")
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
            print(f"  Registered: {cls.__name__}")
        except Exception as e:
            print(f"  FAILED to register {cls.__name__}: {e}")
    
    # Register node tree data on Scene
    bpy.types.Scene.audio_nodes_data = PointerProperty(type=node_tree.AudioNodesData)
    
    # Register node categories
    print("Audio Nodes: Registering node categories...")
    menus.register_node_categories()
    
    # Start visualization timer
    from . import shared_memory_reader
    shared_memory_reader.register_timer()

    # Engine status + Add button, appended onto Blender's node header
    editor.register_header()

    # Spectrum overlay in the node editor (FFT Analyzer live display)
    spectrum_overlay.register_overlay()

    # Waveform / envelope / response graphics inside Sampler, ADSR,
    # Delay and Filter nodes
    node_graphics.register_overlay()

    # audio_tap()/audio_channel() for driver expressions
    audio_drivers.register_driver_namespace()

    # Request audio permissions on macOS
    from . import permissions
    permissions.request_audio_permission()
    
    # Engine lifecycle across file loads
    if on_file_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(on_file_load_pre)
    if on_file_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_file_load_post)

    # Enabling the add-on with an Audio Nodes file already open counts too
    # (load_post fired before this module existed).
    bpy.app.timers.register(_post_load_housekeeping, first_interval=0.5)

    print("Audio Nodes: Add-on registered successfully")


def unregister():
    # Remove file-load handlers
    if on_file_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(on_file_load_pre)
    if on_file_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_file_load_post)
    for fn in (_maybe_auto_start, _post_load_housekeeping):
        try:
            bpy.app.timers.unregister(fn)
        except Exception:
            pass
    
    # Stop visualization timer
    from . import shared_memory_reader
    shared_memory_reader.unregister_timer()

    # Drop any queued connection sync (it would fire into a dead module)
    node_tree.cancel_pending_sync()

    # Remove the header draw function
    editor.unregister_header()

    # Remove the spectrum overlay draw handler
    spectrum_overlay.unregister_overlay()
    node_graphics.unregister_overlay()

    # Remove driver namespace functions
    audio_drivers.unregister_driver_namespace()

    # Stop engine if running
    engine_manager.stop_engine()
    
    # Unregister node categories
    menus.unregister_node_categories()
    
    # Unregister property (tolerate it already being gone - a failure here
    # used to abort the rest of unregister and leave classes half-removed)
    if hasattr(bpy.types.Scene, "audio_nodes_data"):
        try:
            del bpy.types.Scene.audio_nodes_data
        except Exception as e:
            print(f"Audio Nodes: could not remove Scene.audio_nodes_data: {e!r}")
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    print("Audio Nodes add-on unregistered")


if __name__ == "__main__":
    register()
