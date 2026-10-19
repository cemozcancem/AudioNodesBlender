import bpy
import struct
import mmap
import os
import time as _time_module
from typing import Optional, Dict, List, Tuple

# Shared memory constants - must match C++ SharedTypes.h
SHARED_MEMORY_NAME = "AudioNodesVisualization"
SHARED_MEMORY_SIZE = 51296  # + per-control-source slots (appended in 4.15)

# Layout sizes this reader has shipped with, newest first. Every section is
# APPENDED, so an older engine's smaller region still contains every field
# an older reader knew about - we map whatever is actually there and simply
# stop short of the sections it does not have.
KNOWN_MEMORY_SIZES = (51296, 50368)
_mapped_size = SHARED_MEMORY_SIZE

# Struct offsets for AudioVisualizationData
OFFSET_SEQUENCE = 0
OFFSET_FFT_LEFT = 4
OFFSET_FFT_RIGHT = 4100
OFFSET_PEAK_LEFT = 8196
OFFSET_PEAK_RIGHT = 8200
OFFSET_RMS_LEFT = 8204
OFFSET_RMS_RIGHT = 8208
OFFSET_WAVEFORM = 8212

# MIDI Data offsets (at offset 16404)
OFFSET_MIDI = 16404
OFFSET_MIDI_LAST_NOTE = OFFSET_MIDI + 0
OFFSET_MIDI_LAST_VELOCITY = OFFSET_MIDI + 1
OFFSET_MIDI_NOTE_GATE = OFFSET_MIDI + 2
OFFSET_MIDI_CC_VALUES = OFFSET_MIDI + 4  # 128 floats
OFFSET_MIDI_BPM = OFFSET_MIDI + 4 + (128 * 4)
OFFSET_MIDI_TICK_POSITION = OFFSET_MIDI + 4 + (128 * 4) + 4
OFFSET_MIDI_TRANSPORT_PLAYING = OFFSET_MIDI + 4 + (128 * 4) + 8

# Sequencer Data offsets (at offset 16936 - after MIDIData with padding)
OFFSET_SEQUENCER = 16936
OFFSET_SEQ_CURRENT_STEP = OFFSET_SEQUENCER + 0
OFFSET_SEQ_IS_PLAYING = OFFSET_SEQUENCER + 1
OFFSET_SEQ_TRIGGER_ACTIVE = OFFSET_SEQUENCER + 2
OFFSET_SEQ_BPM = OFFSET_SEQUENCER + 4
OFFSET_SEQ_STEP_COUNT = OFFSET_SEQUENCER + 8
# uint64_t step_pattern is 8-byte aligned, so the compiler inserts 4 bytes
# of padding after step_count - the pattern lives at +16, NOT +12.
OFFSET_SEQ_STEP_PATTERN = OFFSET_SEQUENCER + 16

# Engine sample rate (written by the engine at offset 16960; 0 = unknown)
OFFSET_SAMPLE_RATE = 16960

# Per-input-channel peak meters (device input channels, callback order)
OFFSET_NUM_INPUT_CHANNELS = 16964
OFFSET_INPUT_PEAKS = 16968
MAX_INPUT_CHANNELS = 32

# Per-FFT-analyzer slots: every live FFT Analyzer node publishes its own
# spectrum, keyed by engine node id. The legacy top-level fft/peak/rms
# fields mirror slot 0 (the oldest analyzer).
OFFSET_NUM_FFT_ANALYZERS = 17120
OFFSET_FFT_SLOTS = 17152
FFT_SLOT_SIZE = 8240
FFT_SLOT_ID_LEN = 32
MAX_FFT_ANALYZERS = 4

# Per-sequencer slots: every live Step Sequencer publishes its own
# transport, keyed by engine node id. The legacy top-level `sequencer`
# fields mirror slot 0 (the oldest sequencer). Appended after the FFT
# slots, so every offset above is unchanged.
OFFSET_NUM_SEQUENCERS = 50112
OFFSET_SEQUENCER_SLOTS = 50144
SEQ_SLOT_SIZE = 56
SEQ_SLOT_ID_LEN = 32
MAX_SEQUENCERS = 4

# Per-control-source slots: engine nodes whose output is a control signal
# with no audio path (Bang triggers, ADSR envelopes). Nothing else in
# shared memory carries these, so before this section every Blender-side
# consumer downstream of a Bang or an ADSR read a hardcoded 0.0.
OFFSET_NUM_CONTROL_NODES = 50368
OFFSET_CONTROL_SLOTS = 50400
CONTROL_SLOT_SIZE = 56
CONTROL_SLOT_ID_LEN = 32
CONTROL_SLOT_LANES = 4
MAX_CONTROL_NODES = 16

FFT_SIZE = 1024
WAVEFORM_SIZE = 2048
SAMPLE_RATE = 44100  # Fallback when the engine hasn't reported a rate yet

# Actual sample rate reported by the engine (updated on every read)
_engine_sample_rate = float(SAMPLE_RATE)

# Global shared memory handle
_shm = None
_mmap = None
# Path/name the open mapping belongs to. When the port in the preferences
# changes, the cached mapping is for the old engine and gets reopened.
_mapped_path = None

# Fixed frequency bands were removed in 4.2: user-picked frequency taps
# (see nodes/effects/fft_taps.py and _update_fft_tap_outputs) replace them.


DEFAULT_ZMQ_PORT = 5555


def shared_memory_name(port: int) -> str:
    """Name of the visualization mapping published by the engine on `port`.

    Each engine writes its own mapping, so two Blenders (each with its own
    engine on its own port) never read each other's meters. The default
    port keeps the old unsuffixed name so nothing changes for most users.

    The SAME rule lives in engine/src/common/SharedTypes.h
    (sharedMemoryNameForPort) and tests/harness.py - keep all three in step.
    Never fall back to the unsuffixed name for another port: that is
    another instance's data.
    """
    port = int(port)
    if port == DEFAULT_ZMQ_PORT:
        return SHARED_MEMORY_NAME
    return f"{SHARED_MEMORY_NAME}_{port}"


def _current_port() -> int:
    """The ZMQ port from the add-on preferences (5555 if unavailable)."""
    try:
        from . import engine_manager
        return int(engine_manager.get_zmq_port())
    except Exception:
        return DEFAULT_ZMQ_PORT


def _get_shm_path(port: Optional[int] = None):
    """Shared memory path (file on macOS/Linux, mapping name on Windows)
    for `port`, or for the port set in the preferences when omitted."""
    import sys
    name = shared_memory_name(_current_port() if port is None else port)
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        # macOS and Linux: a file-backed mapping in /tmp
        return f"/tmp/{name}"
    elif sys.platform == "win32":
        # Windows uses named shared memory directly
        return name
    return None


def _windows_mapping_exists(name: str) -> bool:
    """Does a named file mapping already exist? (Windows only.)

    Python's mmap cannot open-existing-only, so ask Win32 directly. On any
    unexpected failure return True and let mmap try - degrading to the old
    create-on-miss behaviour is better than refusing to read at all.
    """
    try:
        import ctypes
        from ctypes import wintypes

        FILE_MAP_READ = 0x0004
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                              wintypes.LPCWSTR]
        kernel32.OpenFileMappingW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, name)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return True


def published_size() -> Optional[int]:
    """Size in bytes of the mapping for the current port, or None if the
    engine has not published it. A leftover file from an engine that has
    exited also counts, so this is not proof an engine is running."""
    import sys
    shm_path = _get_shm_path()
    if shm_path is None:
        return None
    try:
        if sys.platform == "win32":
            # Mappings have no size to query; report the layout we read.
            return _mapped_size if _windows_mapping_exists(shm_path) else None
        if os.path.exists(shm_path):
            return os.path.getsize(shm_path)
    except Exception:
        pass
    return None


def _open_shared_memory():
    """Open the shared memory region."""
    global _shm, _mmap, _mapped_size, _mapped_path
    
    shm_path = _get_shm_path()
    if _mmap is not None:
        if shm_path == _mapped_path:
            return True
        # The port changed: stop reading the old engine's mapping.
        _close_shared_memory()
    
    if shm_path is None:
        return False
    
    import sys
    
    try:
        if sys.platform == "darwin" or sys.platform.startswith("linux"):
            # macOS and Linux: open the file-backed mapping in /tmp
            if not os.path.exists(shm_path):
                return False
            
            # Map what the engine actually created, not what this reader
            # was built for. An engine one version behind publishes a
            # smaller region; mapping our own (larger) size would fail
            # outright and take every meter, tap and overlay down with it.
            actual = os.path.getsize(shm_path)
            size = next((s for s in KNOWN_MEMORY_SIZES if s <= actual),
                        min(KNOWN_MEMORY_SIZES))
            if actual < min(KNOWN_MEMORY_SIZES):
                return False  # still being created

            _shm = open(shm_path, "r+b")
            _mmap = mmap.mmap(_shm.fileno(), size)
            _mapped_size = size
            _mapped_path = shm_path
            return True
            
        elif sys.platform == "win32":
            # Windows: attach to the engine's named mapping.
            #
            # `mmap.mmap(-1, size, tagname=...)` CREATES the mapping when it
            # does not exist, so this used to succeed with the engine dead
            # and then read zeros forever - meters flat, taps silent, no
            # error anywhere. The macOS branch guards with os.path.exists;
            # this is the equivalent probe.
            if not _windows_mapping_exists(shm_path):
                return False
            # Largest known layout first; an older engine's mapping is
            # smaller and refuses the bigger view, so fall back through the
            # sizes this reader understands.
            for size in KNOWN_MEMORY_SIZES:
                try:
                    _mmap = mmap.mmap(-1, size, tagname=shm_path)
                    _mapped_size = size
                    _mapped_path = shm_path
                    return True
                except Exception:
                    continue
            return False
            
    except Exception as e:
        print(f"Audio Nodes: Failed to open shared memory: {e}")
        _close_shared_memory()
        return False
    
    return False


def _close_shared_memory():
    """Close the shared memory region."""
    global _shm, _mmap, _mapped_path
    _mapped_path = None
    
    if _mmap is not None:
        try:
            _mmap.close()
        except:
            pass
        _mmap = None
    
    if _shm is not None:
        try:
            _shm.close()
        except:
            pass
        _shm = None


def read_visualization_data() -> Optional[dict]:
    """Read visualization data from shared memory.

    Seqlock protocol (matches SharedMemoryBridge): the writer stores an ODD
    sequence, memcpys the payload, then stores the next EVEN sequence. We
    take a bulk snapshot and accept it only if the sequence was even and
    unchanged across the copy - ALL fields (including MIDI/sequencer) are
    covered by the check.
    """
    global _mmap, _engine_sample_rate

    # Cheap when already open; reopens if the port in the preferences
    # changed since the mapping was opened.
    if not _open_shared_memory():
        return None

    try:
        snapshot = None
        seq1 = 0
        for _attempt in range(4):
            _mmap.seek(0)
            candidate = _mmap.read(_mapped_size)
            seq1 = struct.unpack_from('I', candidate, OFFSET_SEQUENCE)[0]
            if seq1 & 1:
                continue  # writer mid-copy when the snapshot started
            _mmap.seek(OFFSET_SEQUENCE)
            seq2 = struct.unpack_from('I', _mmap.read(4), 0)[0]
            if seq1 == seq2:
                snapshot = candidate
                break

        if snapshot is None:
            return None  # torn every attempt; try again next frame

        peak_left, peak_right, rms_left, rms_right = struct.unpack_from(
            '4f', snapshot, OFFSET_PEAK_LEFT)

        fft_left = struct.unpack_from(f'{FFT_SIZE}f', snapshot, OFFSET_FFT_LEFT)
        fft_right = struct.unpack_from(f'{FFT_SIZE}f', snapshot, OFFSET_FFT_RIGHT)
        waveform = struct.unpack_from(f'{WAVEFORM_SIZE}f', snapshot, OFFSET_WAVEFORM)

        # MIDI data
        midi_last_note, midi_last_velocity, midi_note_gate = struct.unpack_from(
            '3B', snapshot, OFFSET_MIDI_LAST_NOTE)
        midi_cc_values = struct.unpack_from('128f', snapshot, OFFSET_MIDI_CC_VALUES)
        midi_bpm = struct.unpack_from('f', snapshot, OFFSET_MIDI_BPM)[0]
        midi_tick_position = struct.unpack_from('I', snapshot, OFFSET_MIDI_TICK_POSITION)[0]
        midi_transport_playing = struct.unpack_from('B', snapshot, OFFSET_MIDI_TRANSPORT_PLAYING)[0]

        # Sequencer data
        seq_current_step, seq_is_playing, seq_trigger_active = struct.unpack_from(
            '3B', snapshot, OFFSET_SEQ_CURRENT_STEP)
        seq_bpm = struct.unpack_from('f', snapshot, OFFSET_SEQ_BPM)[0]
        seq_step_count = struct.unpack_from('I', snapshot, OFFSET_SEQ_STEP_COUNT)[0]
        seq_step_pattern = struct.unpack_from('Q', snapshot, OFFSET_SEQ_STEP_PATTERN)[0]

        # Engine sample rate (0 until the engine writes its first frame)
        reported_rate = struct.unpack_from('f', snapshot, OFFSET_SAMPLE_RATE)[0]
        if reported_rate > 0:
            _engine_sample_rate = reported_rate

        # Per-input-channel peak meters
        num_input_channels = struct.unpack_from('I', snapshot, OFFSET_NUM_INPUT_CHANNELS)[0]
        num_input_channels = min(num_input_channels, MAX_INPUT_CHANNELS)
        input_peaks = struct.unpack_from(
            f'{MAX_INPUT_CHANNELS}f', snapshot, OFFSET_INPUT_PEAKS)[:num_input_channels]

        # Per-FFT-analyzer slots -> {engine_node_id: {...}}
        num_fft = struct.unpack_from('I', snapshot, OFFSET_NUM_FFT_ANALYZERS)[0]
        num_fft = min(num_fft, MAX_FFT_ANALYZERS)
        fft_nodes = {}
        for i in range(num_fft):
            base = OFFSET_FFT_SLOTS + i * FFT_SLOT_SIZE
            raw_id = struct.unpack_from(f'{FFT_SLOT_ID_LEN}s', snapshot, base)[0]
            node_id = raw_id.split(b'\0', 1)[0].decode('utf-8', errors='replace')
            if not node_id:
                continue
            slot_fft_left = struct.unpack_from(f'{FFT_SIZE}f', snapshot, base + 32)
            slot_fft_right = struct.unpack_from(f'{FFT_SIZE}f', snapshot, base + 4128)
            pl, pr, rl, rr = struct.unpack_from('4f', snapshot, base + 8224)
            fft_nodes[node_id] = {
                'fft_left': slot_fft_left,
                'fft_right': slot_fft_right,
                'peak_left': pl,
                'peak_right': pr,
                'rms_left': rl,
                'rms_right': rr,
            }

        # Per-sequencer slots -> {engine_node_id: {...}}
        num_seq = struct.unpack_from('I', snapshot, OFFSET_NUM_SEQUENCERS)[0]
        num_seq = min(num_seq, MAX_SEQUENCERS)
        sequencer_nodes = {}
        for i in range(num_seq):
            base = OFFSET_SEQUENCER_SLOTS + i * SEQ_SLOT_SIZE
            raw_id = struct.unpack_from(f'{SEQ_SLOT_ID_LEN}s', snapshot, base)[0]
            node_id = raw_id.split(b'\0', 1)[0].decode('utf-8', errors='replace')
            if not node_id:
                continue
            cur, playing, trig = struct.unpack_from('3B', snapshot, base + 32)
            slot_bpm = struct.unpack_from('f', snapshot, base + 36)[0]
            slot_steps = struct.unpack_from('I', snapshot, base + 40)[0]
            slot_pattern = struct.unpack_from('Q', snapshot, base + 48)[0]
            sequencer_nodes[node_id] = {
                'current_step': cur,
                'is_playing': playing,
                'trigger_active': trig,
                'bpm': slot_bpm,
                'step_count': slot_steps,
                'step_pattern': slot_pattern,
            }

        # Per-control-source slots -> {engine_node_id: {...}}. Absent when
        # the running engine predates this section.
        control_nodes = {}
        if len(snapshot) >= OFFSET_CONTROL_SLOTS + CONTROL_SLOT_SIZE * MAX_CONTROL_NODES:
            num_control = struct.unpack_from('I', snapshot, OFFSET_NUM_CONTROL_NODES)[0]
            num_control = min(num_control, MAX_CONTROL_NODES)
            for i in range(num_control):
                base = OFFSET_CONTROL_SLOTS + i * CONTROL_SLOT_SIZE
                raw_id = struct.unpack_from(f'{CONTROL_SLOT_ID_LEN}s', snapshot, base)[0]
                node_id = raw_id.split(b'\0', 1)[0].decode('utf-8', errors='replace')
                if not node_id:
                    continue
                values = struct.unpack_from(f'{CONTROL_SLOT_LANES}f', snapshot, base + 32)
                pulse_count, flags = struct.unpack_from('2I', snapshot, base + 48)
                control_nodes[node_id] = {
                    'values': values,
                    'pulse_count': pulse_count,
                    'high': bool(flags & 1),
                }

        return {
            'sequence': seq1,
            'control_nodes': control_nodes,
            'peak_left': peak_left,
            'peak_right': peak_right,
            'rms_left': rms_left,
            'rms_right': rms_right,
            'input_channel_count': num_input_channels,
            'input_peaks': input_peaks,
            'fft_nodes': fft_nodes,
            'fft_left': fft_left,
            'fft_right': fft_right,
            'waveform': waveform,
            'midi': {
                'last_note': midi_last_note,
                'last_velocity': midi_last_velocity,
                'note_gate': midi_note_gate,
                'cc_values': midi_cc_values,
                'bpm': midi_bpm,
                'tick_position': midi_tick_position,
                'transport_playing': midi_transport_playing,
            },
            'sequencer_nodes': sequencer_nodes,
            'sequencer': {
                'current_step': seq_current_step,
                'is_playing': seq_is_playing,
                'trigger_active': seq_trigger_active,
                'bpm': seq_bpm,
                'step_count': seq_step_count,
                'step_pattern': seq_step_pattern,
            }
        }
        
    except Exception as e:
        print(f"Audio Nodes: Error reading shared memory: {e}")
        _close_shared_memory()
        return None


# Connection change detection removed - handled by node_tree.py with debouncing

# Smoothed FFT values storage
_fft_smoothed_values = {}  # channel_name -> smoothed_value

# Random-trigger gate state per tap channel:
# channel_name -> (was_above_threshold, gate_open)
_tap_gate_states = {}
_TAP_ONSET_THRESHOLD = 0.05

# Latest full spectrum, cached for the node-editor overlay and tap picking.
# (fft_left_tuple, fft_right_tuple, sample_rate)
_latest_spectrum = None

# Per-analyzer spectra: engine_node_id -> (fft_left, fft_right, sample_rate)
_latest_spectra_by_node = {}

# Last bypass state sent to the engine per node (Blender mute -> bypass)
_mute_states = {}


def _sync_mute_states():
    """Mirror Blender's node mute (M key) into engine bypass, live.

    Sends only on change; first sighting of an unmuted node sends nothing
    (freshly created engine nodes are unbypassed). Entries are dropped
    when a node loses engine registration so a re-created node gets its
    mute state resent.
    """
    from . import command_client
    from .nodes.core.node_tree import BLENDER_ONLY_NODE_TYPES

    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            node_id = getattr(node, 'engine_node_id', '')
            node_type = getattr(node, 'node_type', '')
            if not node_id or not node_type or node_type in BLENDER_ONLY_NODE_TYPES:
                continue
            if not command_client.is_node_registered(node_id):
                _mute_states.pop(node_id, None)
                continue

            muted = bool(getattr(node, 'mute', False))
            last = _mute_states.get(node_id)
            if last == muted:
                continue
            if last is None and not muted:
                _mute_states[node_id] = False
                continue
            # Output nodes have a real, gain-ramped `mute` param (20ms) -
            # `bypass` there just stops pulling input, which cuts the
            # signal abruptly and can click. Everything else bypasses.
            param = "mute" if node_type in ("output", "gain") else "bypass"
            if node_type == "gain":
                # Gain owns a silence control, not effect bypass. Clear a
                # bypass left by an older add-on before applying its mute.
                command_client.set_parameter(node_id, "bypass", 0.0)
            command_client.set_parameter(node_id, param, 1.0 if muted else 0.0)
            _mute_states[node_id] = muted


def get_latest_spectrum(node_id: str = None):
    """Return the most recent (fft_left, fft_right, sample_rate) or None.

    With a node_id, returns THAT analyzer's own spectrum (each FFT
    Analyzer node publishes its own slot). Falls back to the global
    spectrum (slot 0 / oldest analyzer) for engines that predate
    per-node slots or when the id has no slot (e.g. >4 analyzers).
    """
    if node_id:
        spectrum = _latest_spectra_by_node.get(node_id)
        if spectrum is not None:
            return spectrum
    return _latest_spectrum


_TAP_ACC_PARAM = "accumulated"   # == fft_taps.ACC_PARAM (no import per call)


def _tap_output_channel(from_node, from_socket):
    """Channel behind an FFT tap *output* socket, or None if it is not one.

    Plain tap outputs (empty tap_param) read '<id>_tap_<tap_id>'; the
    Accumulate output (tap_param == "accumulated") reads the '_acc'
    channel. Tap param *inputs* carry other tap_param values and are never
    link sources, so they return None here.
    """
    tap_id = getattr(from_socket, 'tap_id', '')
    if not tap_id:
        return None
    tap_param = getattr(from_socket, 'tap_param', '')
    if not tap_param:
        return f"{from_node.engine_node_id}_tap_{tap_id}"
    if tap_param == _TAP_ACC_PARAM:
        return f"{from_node.engine_node_id}_tap_{tap_id}_acc"
    return None


def _resolve_control_source_value(audio_data, from_node, from_socket) -> float:
    """Resolve a control cable source to a float (shared by modulation paths)."""
    # Frequency tap outputs: stable tap_id channel (normal or accumulated)
    source_channel = _tap_output_channel(from_node, from_socket)
    if source_channel is not None:
        if source_channel in audio_data.channels:
            return audio_data.channels[source_channel].value
        return 0.0
    if hasattr(from_node, 'get_output_channel_name'):
        source_channel = from_node.get_output_channel_name(from_socket.name)
        if source_channel and source_channel in audio_data.channels:
            return audio_data.channels[source_channel].value
    socket_channel = f"{from_node.engine_node_id}_{from_socket.name.lower().replace(' ', '_')}"
    if socket_channel in audio_data.channels:
        return audio_data.channels[socket_channel].value
    if hasattr(from_socket, 'default_value'):
        return float(from_socket.default_value)
    return 0.0


def _linked_tap_param_control(audio_data, node, tap_id: str, prop: str):
    """Return absolute control value if the tap param input is linked, else None.

    LFO outputs are 0..1 and are mapped onto the param's hard range here.
    MIDI CC (and other) sources are already in parameter units (use the
    CC node's Min/Max to set that range) and are passed through as-is.
    """
    if not hasattr(node, 'get_tap_param_input'):
        return None
    sock = node.get_tap_param_input(tap_id, prop)
    if sock is None or not sock.is_linked:
        return None
    link = sock.links[0]
    from_node = link.from_node
    value = _resolve_control_source_value(audio_data, from_node, link.from_socket)

    if getattr(from_node, 'bl_idname', '') == 'AudioLFONode':
        from .nodes.effects import fft_taps
        for key, _label, lo, hi in fft_taps.TAP_CONTROL_PARAMS:
            if key == prop:
                return fft_taps.map_control_to_param(value, lo, hi)
    return value


def _update_fft_tap_outputs(audio_data, data: dict):
    """Compute per-tap band energies for every FFT Analyzer node.

    Taps are user-picked frequency bands (see nodes/effects/fft_taps.py).
    Each enabled tap publishes a channel '{engine_node_id}_tap_{tap_id}'
    that control links and Value Output nodes consume.

    Per-tap parameter inputs (Hz/Oct/Gain/…) override RNA when linked —
    absolute parameter units (LFO 0..1 is mapped in _linked_tap_param_control)
    without writing the UI sliders.
    """
    global _fft_smoothed_values

    from .nodes.effects import fft_taps

    sample_rate = _engine_sample_rate
    fft_nodes = data.get('fft_nodes', {})

    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue

        for node in tree.nodes:
            if node.bl_idname != 'AudioFFTAnalyzerNode':
                continue

            # This analyzer's OWN spectrum (every FFT node has its own
            # slot); fall back to the global/oldest spectrum if the node
            # has no slot yet (just created, or >4 analyzers).
            slot = fft_nodes.get(node.engine_node_id)
            if slot is not None:
                fft_left = slot['fft_left']
                fft_right = slot['fft_right']
            else:
                fft_left = data['fft_left']
                fft_right = data['fft_right']

            # Pre-4.2 files: convert linked legacy band sockets to taps.
            # Cheap no-op once migrated; guarantees migration even when
            # the tree's update() never fires after file load.
            if hasattr(node, 'migrate_legacy_band_sockets'):
                try:
                    node.migrate_legacy_band_sockets()
                except Exception:
                    pass

            # Keep param input sockets mirrored to the taps collection
            # (file load / external edits may leave them stale).
            if hasattr(node, 'sync_tap_sockets'):
                try:
                    node.sync_tap_sockets()
                except Exception:
                    pass

            taps = getattr(node, 'taps', None)
            if not taps:
                continue

            node_muted = bool(getattr(node, 'mute', False))

            for tap in taps:
                if not tap.tap_id:
                    continue
                channel_name = f"{node.engine_node_id}_tap_{tap.tap_id}"

                # Effective params: linked control inputs absolute-drive.
                ctrl = {
                    prop: _linked_tap_param_control(
                        audio_data, node, tap.tap_id, prop)
                    for prop, _lbl, _lo, _hi in fft_taps.TAP_CONTROL_PARAMS
                }
                center_hz = fft_taps.effective_tap_attr(
                    tap, "center_hz", ctrl["center_hz"])
                width_oct = fft_taps.effective_tap_attr(
                    tap, "width_oct", ctrl["width_oct"])
                gain = fft_taps.effective_tap_attr(tap, "gain", ctrl["gain"])
                smoothing = fft_taps.effective_tap_attr(
                    tap, "smoothing", ctrl["smoothing"])
                out_min = fft_taps.effective_tap_attr(
                    tap, "out_min", ctrl["out_min"])
                out_max = fft_taps.effective_tap_attr(
                    tap, "out_max", ctrl["out_max"])
                trigger_prob = fft_taps.effective_tap_attr(
                    tap, "trigger_probability", ctrl["trigger_probability"])

                if node_muted or not tap.enabled:
                    value = 0.0
                else:
                    left = fft_taps.tap_band_energy(
                        fft_left, center_hz, width_oct, sample_rate)
                    right = fft_taps.tap_band_energy(
                        fft_right, center_hz, width_oct, sample_rate)
                    value = max(left, right) * gain

                    # Occasional triggering: each time the band rises from
                    # silence ("onset"), roll the dice - the tap only fires
                    # with the configured probability. The decision holds
                    # until the band falls silent again, so a passing hit
                    # plays out fully instead of flickering.
                    if getattr(tap, 'use_random_trigger', False):
                        import random
                        was_above, gate_open = _tap_gate_states.get(
                            channel_name, (False, True))
                        now_above = value >= _TAP_ONSET_THRESHOLD
                        if now_above and not was_above:
                            gate_open = (random.random() < trigger_prob)
                        _tap_gate_states[channel_name] = (now_above, gate_open)
                        if not gate_open:
                            value = 0.0
                    else:
                        _tap_gate_states.pop(channel_name, None)

                    smoothing = max(0.0, min(0.99, smoothing))
                    if smoothing > 0:
                        prev = _fft_smoothed_values.get(channel_name, value)
                        value = prev * smoothing + value * (1.0 - smoothing)
                    _fft_smoothed_values[channel_name] = value

                    value = max(0.0, min(1.0, value))

                # Map the normalized 0-1 level to the tap's output range.
                # Disabled/muted taps rest at out_min (the "silent" value).
                value = out_min + (out_max - out_min) * value

                if channel_name not in audio_data.channels:
                    channel = audio_data.channels.add()
                    channel.name = channel_name
                audio_data.channels[channel_name].value = value


def _update_tap_accumulators(audio_data, dt: float):
    """Advance every tap's Accumulate output by one tick.

    Integrates the tap's FINAL output - the value just published on
    '<id>_tap_<tap_id>' (after gain, smoothing and Min/Max mapping) - with
    control_logic.AccumulatorLogic, and publishes '<id>_tap_<tap_id>_acc'.

    Runs right after _update_fft_tap_outputs and before Math, the control
    nodes, control connections, Value Output and drivers, with the same dt
    as the control nodes - so everything downstream sees this tick's value
    (no one-tick lag).

    A muted analyzer or a disabled tap holds its count instead of
    integrating its resting value. State for taps that are gone or have
    Accumulate off is dropped here every tick, whatever removed them
    (delete, toggle, undo, file load).
    """
    from .nodes.effects import fft_taps
    from .nodes.control.control_nodes import publish

    live = set()
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            if node.bl_idname != 'AudioFFTAnalyzerNode':
                continue
            node_muted = bool(getattr(node, 'mute', False))
            for tap in getattr(node, 'taps', ()):
                if not tap.tap_id or not getattr(tap, 'accumulate', False):
                    continue
                channel = fft_taps.acc_channel_name(node, tap)
                live.add(channel)
                if node_muted or not tap.enabled:
                    value = fft_taps.acc_current(channel, tap)
                else:
                    source = audio_data.channels.get(
                        fft_taps.tap_channel_name(node, tap))
                    rate = source.value if source is not None else 0.0
                    value = fft_taps.acc_step(channel, tap, rate, dt)
                publish(audio_data, channel, value)
    fft_taps.acc_keep_only(live)


def _update_fft_analyzer_outputs(audio_data, data: dict):
    """Update FFT analyzer Peak/RMS output channels with per-node processing.

    Fixed Low/Mid/High band outputs were removed in 4.2 - user-picked
    frequency taps (_update_fft_tap_outputs) replace them.
    """
    global _fft_smoothed_values

    socket_to_band = {
        "Peak L": "peak_left",
        "Peak R": "peak_right",
        "RMS L": "rms_left",
        "RMS R": "rms_right",
    }

    fft_nodes = data.get('fft_nodes', {})

    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue

        for node in tree.nodes:
            if node.bl_idname != 'AudioFFTAnalyzerNode':
                continue

            # Per-node peak/RMS from this analyzer's own slot; global
            # fallback when the node has no slot.
            slot = fft_nodes.get(node.engine_node_id)

            # Fixed pipeline (the per-node threshold/range settings were
            # removed from the UI in 4.3)
            threshold = 0.0
            smoothing = 0.3
            output_min = 0.0
            output_max = 1.0

            for socket_name, raw_channel in socket_to_band.items():
                # Get raw value from this node's slot, else the base channel
                raw_value = 0.0
                if slot is not None:
                    raw_value = max(0.0, slot[raw_channel])
                elif raw_channel in audio_data.channels:
                    raw_value = max(0.0, audio_data.channels[raw_channel].value)

                value = raw_value

                # Apply threshold (noise gate)
                if value < threshold:
                    value = 0.0
                
                # Apply smoothing
                output_channel = f"{node.engine_node_id}_{raw_channel}"
                if smoothing > 0:
                    prev_value = _fft_smoothed_values.get(output_channel, value)
                    value = prev_value * smoothing + value * (1.0 - smoothing)
                _fft_smoothed_values[output_channel] = value
                
                # Clamp to 0-1 before mapping
                value = max(0.0, min(1.0, value))
                
                # Map to output range
                value = output_min + (output_max - output_min) * value
                
                # Final clamp to ensure no negative values when min=0
                if output_min >= 0:
                    value = max(0.0, value)
                
                # Store in channel
                if output_channel not in audio_data.channels:
                    channel = audio_data.channels.add()
                    channel.name = output_channel
                audio_data.channels[output_channel].value = value


def _update_sequencer_outputs(audio_data, data: dict):
    """Publish each Step Sequencer's own transport channels.

    Every sequencer used to map Trigger/Step/Playing onto the SINGLE global
    `seq_*` channels, which the engine filled from the primary (oldest)
    sequencer - so a second Step Sequencer's control outputs and its step
    highlight both showed the first one's state. Each node now reads its
    own slot.

    The global channels are still published (mirroring the primary
    sequencer) for any file whose drivers reference them by name.
    """
    seq_nodes = data.get('sequencer_nodes', {})

    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            if node.bl_idname != 'AudioSequencerNode':
                continue

            node_id = getattr(node, 'engine_node_id', '')
            if not node_id:
                continue

            slot = seq_nodes.get(node_id)
            if slot is None:
                # No slot: engine down, or more sequencers than the shared
                # memory publishes. Rest at zero rather than borrowing
                # another sequencer's transport.
                values = {'step': 0.0, 'playing': 0.0, 'trigger': 0.0, 'bpm': 0.0}
            else:
                values = {
                    'step': float(slot['current_step']),
                    'playing': float(slot['is_playing']),
                    'trigger': float(slot['trigger_active']),
                    'bpm': slot['bpm'],
                }

            for suffix, value in values.items():
                channel_name = f"{node_id}_{suffix}"
                if channel_name not in audio_data.channels:
                    channel = audio_data.channels.add()
                    channel.name = channel_name
                audio_data.channels[channel_name].value = value

            # Mirror the engine's transport into the node's own property.
            # The Play/Stop BUTTONS set it, but a Bang wired to the Play
            # input starts the sequencer entirely inside the engine - so
            # the node went on showing a Play icon and a dead step
            # highlight while it was audibly running.
            if slot is not None:
                playing = bool(slot['is_playing'])
                if bool(getattr(node, 'is_playing', False)) != playing:
                    node.is_playing = playing


# Bang pulses last ~20ms, i.e. roughly one 60fps frame, and two presses in
# quick succession can both land between two reads. The engine therefore
# publishes a monotonic pulse COUNT; this remembers the last count seen per
# node so no press is missed, and holds the channel high for one frame so
# everything downstream gets a clean 0 -> 1 -> 0 to react to.
_control_pulse_counts = {}   # node_id -> last pulse_count seen
_control_pulse_hold = set()  # node_ids holding high for this frame


def _control_output_channel_names(node):
    """Control output sockets of a node -> (lane index, channel name)."""
    names = []
    lane = 0
    for socket in node.outputs:
        if socket.bl_idname != 'ControlSocketType':
            continue
        suffix = socket.name.lower().replace(" ", "_")
        names.append((lane, f"{node.engine_node_id}_{suffix}"))
        lane += 1
    return names


def _update_control_node_outputs(audio_data, data: dict):
    """Publish Bang / ADSR control outputs as channels.

    These nodes live in the C++ engine and their output never touched an
    audio cable, so nothing put them in shared memory and nothing on the
    Blender side could read them. A Bang wired to a Value Output, a Math
    node or a driver resolved to a constant 0.0 no matter how often it
    fired. Now every control lane the engine publishes becomes a channel
    named after its socket, which is exactly what
    _resolve_control_source_value already looks for.
    """
    control_nodes = data.get('control_nodes', {})
    if not control_nodes:
        return

    fired_now = set()
    for node_id, slot in control_nodes.items():
        previous = _control_pulse_counts.get(node_id)
        count = slot['pulse_count']
        if previous is not None and count != previous:
            fired_now.add(node_id)
        _control_pulse_counts[node_id] = count

    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            node_id = getattr(node, 'engine_node_id', '')
            if not node_id:
                continue
            slot = control_nodes.get(node_id)
            if slot is None:
                continue

            values = slot['values']
            for lane, channel_name in _control_output_channel_names(node):
                if lane >= len(values):
                    break
                value = values[lane]
                # A pulse seen only through the counter still reads high for
                # one frame, so a fast double press is two visible events.
                if lane == 0 and (node_id in fired_now or node_id in _control_pulse_hold):
                    value = 1.0
                if channel_name not in audio_data.channels:
                    channel = audio_data.channels.add()
                    channel.name = channel_name
                audio_data.channels[channel_name].value = value

    # Hold this frame's fires high for exactly one more frame, so a
    # consumer polling at 60fps cannot step over the rising edge.
    _control_pulse_hold.clear()
    _control_pulse_hold.update(fired_now)


def _update_midi_node_outputs(audio_data, data: dict):
    """Update MIDI CC and MIDI Input node output socket channels."""
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        
        for node in tree.nodes:
            # Handle MIDI CC nodes
            if node.bl_idname == 'AudioMIDICCNode':
                raw_channel = f"midi_cc_{getattr(node, 'cc_number', 1)}"
                if raw_channel not in audio_data.channels:
                    continue

                value = float(audio_data.channels[raw_channel].value)

                # Smooth on raw 0..1, then map to output_min..output_max
                smoothing = getattr(node, 'smoothing', 0.0)
                raw_smooth_ch = f"{node.engine_node_id}_raw_smooth"
                if smoothing > 0 and raw_smooth_ch in audio_data.channels:
                    old_raw = audio_data.channels[raw_smooth_ch].value
                    value = old_raw * smoothing + value * (1.0 - smoothing)
                if raw_smooth_ch not in audio_data.channels:
                    ch = audio_data.channels.add()
                    ch.name = raw_smooth_ch
                audio_data.channels[raw_smooth_ch].value = value

                out_min = getattr(node, 'output_min', 0.0)
                out_max = getattr(node, 'output_max', 1.0)
                mapped = out_min + (out_max - out_min) * value

                socket_channel = f"{node.engine_node_id}_value"
                if socket_channel not in audio_data.channels:
                    channel = audio_data.channels.add()
                    channel.name = socket_channel
                audio_data.channels[socket_channel].value = mapped

            # Handle MIDI Input nodes
            elif node.bl_idname == 'AudioMIDIInputNode':
                for output_socket in node.outputs:
                    if output_socket.bl_idname != 'ControlSocketType':
                        continue
                    
                    # Get the source channel name
                    if hasattr(node, 'get_output_channel_name'):
                        source_channel = node.get_output_channel_name(output_socket.name)
                        if source_channel and source_channel in audio_data.channels:
                            value = audio_data.channels[source_channel].value
                            
                            # Create a unique channel for this socket
                            socket_channel = f"{node.engine_node_id}_{output_socket.name.lower().replace(' ', '_')}"
                            
                            if socket_channel not in audio_data.channels:
                                channel = audio_data.channels.add()
                                channel.name = socket_channel
                            audio_data.channels[socket_channel].value = value


# Global time counter for LFO.
# Advanced from the wall clock, not by a fixed 1/60 step: the timer is not
# guaranteed to run at 60fps (heavy scenes, background renders) and it is
# skipped entirely on frames where the engine published nothing, which made
# LFO rate depend on Blender's framerate and on audio activity.
_lfo_time = 0.0
_lfo_last_wall = None

# Rate limiting for parameter updates
_last_param_values = {}  # node_id_param -> last_sent_value
_param_update_threshold = 0.001  # Only send if value changed by more than this


def _update_lfo_nodes(audio_data, data: dict):
    """Update LFO node outputs - calculate current LFO values."""
    global _lfo_time, _lfo_last_wall

    import time as _time
    now = _time.monotonic()
    if _lfo_last_wall is None:
        delta = 1.0 / 60.0
    else:
        # Clamp so a stall (file dialog, render) does not jump the phase.
        delta = max(0.0, min(0.25, now - _lfo_last_wall))
    _lfo_last_wall = now
    _lfo_time += delta
    
    # Get BPM from MIDI data for tempo sync
    bpm = data['midi']['bpm'] if data['midi']['bpm'] > 0 else 120.0
    
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        
        for node in tree.nodes:
            if node.bl_idname != 'AudioLFONode':
                continue
            
            # Calculate LFO output value
            value = node.get_output_value(_lfo_time, bpm)
            
            # Normalize to 0-1 range for control signals
            # LFO outputs -1 to 1, we map to 0 to 1 for modulation
            normalized_value = (value + 1.0) / 2.0
            
            # Store in channel
            channel_name = node.get_output_channel_name()
            if channel_name:
                if channel_name not in audio_data.channels:
                    channel = audio_data.channels.add()
                    channel.name = channel_name
                audio_data.channels[channel_name].value = normalized_value


_active_modulations = set()
_control_errors = {}


def reset_control_state():
    _last_param_values.clear()
    _active_modulations.clear()
    _mute_states.clear()
    _control_errors.clear()
    _control_pulse_counts.clear()
    _control_pulse_hold.clear()


def _update_control_connections(audio_data, data: dict):
    """Send linked controls and restore base values on disconnection."""
    from . import command_client, engine_manager
    from .nodes.core.node_tree import BLENDER_ONLY_NODE_TYPES
    if not engine_manager.is_engine_running():
        return
    active = set()
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            if not hasattr(node, 'engine_node_id'):
                continue
            initial = node.get_initial_params() if hasattr(node, 'get_initial_params') else {}
            specs = {}
            # Transport ports the ENGINE also wires up itself (it resolves
            # "play"/"stop" to real input ports and edge-detects them on
            # the audio thread). Marked so we do not send a second,
            # slightly later copy of the same event - two Play events a few
            # milliseconds apart restart the sequencer twice and stutter.
            for socket_name, param in {"Play": "play", "Stop": "stop"}.items():
                specs[socket_name] = {"param": param, "base": 0.0,
                                      "engine_wired": True}
            if hasattr(node, 'get_control_input_param_map'):
                for socket_name, param in node.get_control_input_param_map().items():
                    specs[socket_name] = {"param": param, "base": initial.get(param, 0.0)}
            if hasattr(node, 'get_modulatable_params'):
                specs.update(node.get_modulatable_params())
            for socket_name, info in specs.items():
                socket = node.inputs.get(socket_name)
                if socket is None:
                    continue
                key = f"{node.engine_node_id}_{info['param']}"
                if not socket.is_linked:
                    if key in _active_modulations:
                        command_client.set_parameter(node.engine_node_id, info['param'], info['base'])
                        _last_param_values.pop(key, None)
                    continue
                link = socket.links[0]
                if info.get('engine_wired') and getattr(
                        link.from_node, 'node_type', '') not in BLENDER_ONLY_NODE_TYPES:
                    # Engine node -> engine port: the C++ graph already
                    # carries this cable. Only Blender-only sources (LFO,
                    # Math, MIDI CC) need us to push the value across.
                    continue
                value = _resolve_control_source_value(audio_data, link.from_node, link.from_socket)
                if 'mod_depth' in info:
                    value = info['base'] + (value - 0.5) * 2.0 * info['mod_depth']
                    value = max(info['min'], min(info['max'], value))
                last = _last_param_values.get(key)
                if key not in _active_modulations or last is None or abs(value - last) > _param_update_threshold:
                    command_client.set_parameter(node.engine_node_id, info['param'], value)
                    _last_param_values[key] = value
                active.add(key)
    _active_modulations.clear()
    _active_modulations.update(active)


def _apply_math_operation(op: str, a: float, b: float, clamp_min: float, clamp_max: float) -> float:
    if op == 'ADD':
        return a + b
    if op == 'SUB':
        return a - b
    if op == 'MUL':
        return a * b
    if op == 'DIV':
        return a / b if abs(b) > 1.0e-6 else 0.0
    if op == 'MIN':
        return min(a, b)
    if op == 'MAX':
        return max(a, b)
    if op == 'ABS':
        return abs(a)
    if op == 'CLAMP':
        mn = min(clamp_min, clamp_max)
        mx = max(clamp_min, clamp_max)
        return max(mn, min(mx, a))
    return 0.0


def _ordered_math_nodes(tree):
    """Topological order; cycles and their dependent nodes are rejected."""
    from collections import deque
    nodes = {n.engine_node_id: n for n in tree.nodes if n.bl_idname == 'AudioMathNode'}
    deps = {key: set() for key in nodes}
    downstream = {key: set() for key in nodes}
    for key, node in nodes.items():
        for name in ("A", "B"):
            socket = node.inputs.get(name)
            if socket and socket.is_linked:
                source = socket.links[0].from_node.engine_node_id
                if source in nodes:
                    deps[key].add(source)
                    downstream[source].add(key)
    ready = deque(key for key in nodes if not deps[key])
    ordered = []
    while ready:
        key = ready.popleft()
        ordered.append(nodes[key])
        for child in downstream[key]:
            deps[child].discard(key)
            if not deps[child]:
                ready.append(child)
    return ordered, [nodes[key] for key in nodes if deps[key]]


def _update_math_nodes(audio_data):
    _control_errors.clear()
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        ordered, blocked = _ordered_math_nodes(tree)
        for node in blocked:
            _control_errors[node.engine_node_id] = "Control feedback loop: remove a Math cable"
        for node in blocked + ordered:
            values = []
            for name, fallback in (("A", node.input_a), ("B", node.input_b)):
                socket = node.inputs.get(name)
                if socket and socket.is_linked:
                    link = socket.links[0]
                    fallback = _resolve_control_source_value(audio_data, link.from_node, link.from_socket)
                values.append(fallback)
            value = 0.0 if node.engine_node_id in _control_errors else _apply_math_operation(
                node.operation, *values, node.clamp_min, node.clamp_max)
            channel_name = node.get_channel_name()
            if channel_name not in audio_data.channels:
                channel = audio_data.channels.add()
                channel.name = channel_name
            audio_data.channels[channel_name].value = value


# Smoothed values storage for Value Output nodes
_value_output_smoothed = {}  # node_id -> smoothed_value


# --- Geometry Nodes modifier input writing -------------------------------
#
# Blender 5.0 moved Geometry Nodes modifier inputs off IDProperties. On 5.x
# `mod["Socket_9"] = v` and even `mod.keys()` raise
#     TypeError: bpy_struct.keys(): this type doesn't support IDProperties
# and the values live behind `mod.properties.inputs.<identifier>.value`.
# Both spellings are kept so 4.x files keep working.

_gn_write_reported = set()


def _report_gn_write_problem(target_obj, prop_name, exc):
    """Print once per (object, property) so a dead link is visible."""
    key = (getattr(target_obj, 'name', '?'), prop_name)
    if key in _gn_write_reported:
        return
    _gn_write_reported.add(key)
    if exc is not None:
        print(f"Audio Nodes: could not write '{prop_name}' on "
              f"'{key[0]}': {type(exc).__name__}: {exc}")
    else:
        print(f"Audio Nodes: Value Output target '{prop_name}' matches no "
              f"Geometry Nodes input on '{key[0]}' - check the name")


def _gn_input_identifier(mod, prop_name: str) -> str:
    """Resolve a socket's display name ("Energy") to its id ("Socket_9").

    Falls through to prop_name so an identifier can be given directly.
    """
    interface = getattr(mod.node_group, 'interface', None)
    if interface is not None:
        wanted = prop_name.lower()
        for item in interface.items_tree:
            if getattr(item, 'item_type', '') != 'SOCKET':
                continue
            if getattr(item, 'in_out', '') != 'INPUT':
                continue
            if getattr(item, 'name', '').lower() == wanted:
                return item.identifier
    return prop_name


def _write_geometry_nodes_input(mod, prop_name: str, value: float) -> bool:
    """Set one Geometry Nodes modifier input. True if the write landed."""
    identifier = _gn_input_identifier(mod, prop_name)

    # Blender 5.x
    modifier_props = getattr(mod, 'properties', None)
    inputs = getattr(modifier_props, 'inputs', None) if modifier_props is not None else None
    if inputs is not None:
        socket = getattr(inputs, identifier, None)
        if socket is None or not hasattr(socket, 'value'):
            return False
        try:
            socket.value = value
        except (TypeError, AttributeError) as exc:
            _report_gn_write_problem(mod.id_data, prop_name, exc)
            return False
        return True

    # Blender 4.x and earlier
    try:
        keys = list(mod.keys())
    except TypeError:
        return False
    if identifier in keys:
        mod[identifier] = value
        return True
    lowered = prop_name.lower()
    for key in keys:
        if key.lower() == lowered or key.endswith(f"_{prop_name}"):
            mod[key] = value
            return True
    return False


def _update_value_output_nodes(audio_data, data: dict):
    """Update Value Output nodes - write control values to Geometry Nodes modifier inputs.
    
    This function:
    1. Iterates all Value Output nodes
    2. Reads the value from connected control socket
    3. Applies smoothing and range mapping
    4. Writes to matching Geometry Nodes modifier inputs
    """
    global _value_output_smoothed
    
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        
        for node in tree.nodes:
            if node.bl_idname != 'AudioValueOutputNode':
                continue

            # Muted Value Output: stop driving the target
            if getattr(node, 'mute', False):
                continue

            # Get the input socket
            input_socket = node.inputs.get("Value")
            if not input_socket:
                continue
            
            # Get source value
            source_value = 0.0
            
            if input_socket.is_linked:
                # Get value from connected node
                from_link = input_socket.links[0]
                from_node = from_link.from_node
                from_socket = from_link.from_socket

                # Frequency tap sockets resolve via their stable tap_id -
                # never by name (all taps may be named "Tap"). The
                # Accumulate output resolves to the tap's '_acc' channel.
                tap_id = getattr(from_socket, 'tap_id', '')
                if tap_id:
                    source_channel = _tap_output_channel(from_node, from_socket)
                    if source_channel and source_channel in audio_data.channels:
                        source_value = audio_data.channels[source_channel].value
                # Check if source node has get_output_channel_name (LFO, MIDI CC, FFT, etc.)
                elif hasattr(from_node, 'get_output_channel_name'):
                    # Handle both single-arg and multi-arg versions
                    try:
                        source_channel = from_node.get_output_channel_name(from_socket.name)
                    except TypeError:
                        source_channel = from_node.get_output_channel_name()

                    if source_channel and source_channel in audio_data.channels:
                        source_value = audio_data.channels[source_channel].value
                else:
                    # Fallback to engine_node_id pattern
                    socket_channel = f"{from_node.engine_node_id}_{from_socket.name.lower().replace(' ', '_')}"
                    if socket_channel in audio_data.channels:
                        source_value = audio_data.channels[socket_channel].value
                    elif hasattr(from_socket, 'default_value'):
                        source_value = from_socket.default_value
            else:
                # Use default value from socket if not connected
                if hasattr(input_socket, 'default_value'):
                    source_value = input_socket.default_value
            
            # Apply smoothing
            smoothing = getattr(node, 'smoothing', 0.0)
            node_id = node.engine_node_id
            
            if smoothing > 0:
                prev_value = _value_output_smoothed.get(node_id, source_value)
                source_value = prev_value * smoothing + source_value * (1.0 - smoothing)
            
            _value_output_smoothed[node_id] = source_value
            
            # Apply range mapping (0-1 input -> min-max output)
            min_val = getattr(node, 'min_value', 0.0)
            max_val = getattr(node, 'max_value', 1.0)
            mapped_value = min_val + (max_val - min_val) * source_value
            
            # Store value for node UI display
            channel_name = node.get_channel_name()
            if channel_name not in audio_data.channels:
                channel = audio_data.channels.add()
                channel.name = channel_name
            audio_data.channels[channel_name].value = mapped_value
            
            # Write to Geometry Nodes modifier inputs
            target_obj = getattr(node, 'target_object', None)
            prop_name = getattr(node, 'property_name', '')
            
            if target_obj and prop_name:
                value_written = False
                try:
                    for mod in target_obj.modifiers:
                        if mod.type != 'NODES' or not mod.node_group:
                            continue
                        if _write_geometry_nodes_input(mod, prop_name, mapped_value):
                            value_written = True
                            break
                except Exception as exc:
                    _report_gn_write_problem(target_obj, prop_name, exc)

                if value_written:
                    target_obj.update_tag(refresh={'DATA'})
                else:
                    # Never fail silently here again: a Value Output that
                    # writes nowhere is indistinguishable from a working one
                    # from inside Blender, which is exactly how the 5.x
                    # breakage went unnoticed.
                    _report_gn_write_problem(target_obj, prop_name, None)


def _update_nla_scrub_nodes(audio_data):
    """Advance NLA Scrub nodes - drive NLA strip playheads from audio.

    Writes one float per node (the strip's strip_time keyframe) and lets
    Blender's NLA system evaluate the pose. The timeline is never touched.
    """
    from .nodes.core import nla_scrub_node

    def resolve_input(node, socket_name, default=0.0):
        socket = node.inputs.get(socket_name)
        if socket is None:
            return default
        if socket.is_linked:
            link = socket.links[0]
            return _resolve_control_source_value(
                audio_data, link.from_node, link.from_socket)
        if hasattr(socket, 'default_value'):
            return float(socket.default_value)
        return default

    nla_scrub_node.update_nla_scrub_nodes(audio_data, resolve_input)


def _resolve_input(audio_data):
    """resolve_input(node, socket_name, default) bound to this tick's data."""
    def resolve_input(node, socket_name, default=0.0):
        socket = node.inputs.get(socket_name)
        if socket is None:
            return default
        if socket.is_linked:
            link = socket.links[0]
            return _resolve_control_source_value(
                audio_data, link.from_node, link.from_socket)
        if hasattr(socket, 'default_value'):
            return float(socket.default_value)
        return default
    return resolve_input


def _has_control_nodes() -> bool:
    from .nodes.control.control_nodes import CONTROL_NODE_IDNAMES
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            if node.bl_idname in CONTROL_NODE_IDNAMES:
                return True
    return False


def _update_control_nodes(audio_data, dt=None):
    """Trigger / Accumulator / Decay / Camera Cutter (Blender-only).

    Runs after the Math pass so they see this tick's Math values, then
    Math runs again so a Math node fed BY a control node is fresh too.
    `dt` is this tick's control_nodes.advance_tick(); None computes it
    from the node module's own clock (older callers, tests).
    """
    from .nodes.control import control_nodes
    control_nodes.update_control_nodes(audio_data, _resolve_input(audio_data),
                                       dt)


# ---------------------------------------------------------------------------
# Stale-state pruning
#
# Every per-node cache below is keyed by (or prefixed with) an
# engine_node_id. Nothing used to drop entries when a node was deleted, so
# a long editing session slowly accumulated dead smoothing values, gate
# states and last-sent params. The scene's `channels` collection is worse:
# it is real RNA data, so dead channels were saved into the .blend and grew
# it on every load-edit-save cycle.
# ---------------------------------------------------------------------------

import re as _re

# engine_node_id shape: "<type>_<8 hex>", e.g. "fft_analyzer_7ac41f02".
_NODE_CHANNEL_RE = _re.compile(r'^([a-z_]+_[0-9a-f]{8})_')

_PRUNE_INTERVAL = 10.0   # seconds
_last_prune = 0.0


def _live_engine_ids() -> set:
    live = set()
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            node_id = getattr(node, 'engine_node_id', '')
            if node_id:
                live.add(node_id)
    return live


def _is_dead_key(key: str, live: set) -> bool:
    """True when `key` is prefixed by an engine id that no longer exists."""
    for node_id in live:
        if key == node_id or key.startswith(node_id + "_"):
            return False
    return True


def _prune_stale_caches():
    """Drop in-memory per-node state for deleted nodes.

    Python dicts only - no RNA is touched, so this is safe to run from the
    visualization timer.
    """
    global _last_prune

    now = _time_module.monotonic()
    if now - _last_prune < _PRUNE_INTERVAL:
        return
    _last_prune = now

    live = _live_engine_ids()

    caches = [_fft_smoothed_values, _tap_gate_states, _last_param_values,
              _control_pulse_counts,
              _value_output_smoothed, _mute_states]
    try:
        # Sibling module, imported lazily like the rest of this file. Guard
        # it: a timer tick can land mid-reload (disabling/re-enabling the
        # add-on) when sys.modules is briefly inconsistent, and a pruning
        # pass is never worth raising over.
        from .nodes.timing.lfo_node import _RANDOM_STATE
        caches.append(_RANDOM_STATE)
    except Exception:
        pass
    try:
        from .nodes.control.control_nodes import _STATE as _CONTROL_STATE
        caches.append(_CONTROL_STATE)
    except Exception:
        pass
    try:
        from .nodes.effects.fft_taps import _ACC_STATE
        caches.append(_ACC_STATE)
    except Exception:
        pass

    for cache in caches:
        for key in [k for k in cache if _is_dead_key(k, live)]:
            del cache[key]


def prune_stale_channels():
    """Remove scene channels belonging to nodes that no longer exist.

    `channels` is real RNA data saved into the .blend, so dead entries grow
    the file on every load-edit-save cycle. Removing them is a data write,
    though, which has no business happening 60 times a second - this is
    called at deliberate moments (file load, engine start) instead.

    Only names matching an engine_node_id prefix are considered. Globals
    (peak_left, midi_cc_5, seq_bpm, input_peak_3, ...) never match and are
    left alone.
    """
    live = _live_engine_ids()
    removed = 0
    for scene in bpy.data.scenes:
        audio_data = getattr(scene, 'audio_nodes_data', None)
        if audio_data is None:
            continue
        doomed = []
        for i, channel in enumerate(audio_data.channels):
            match = _NODE_CHANNEL_RE.match(channel.name)
            # The regex can mis-split a name whose later parts also look
            # like an id (a tap Accumulate channel
            # '<id>_tap_<tap_id>_acc' with all-letter hex), so a channel
            # any live node id prefixes is always kept.
            if (match and match.group(1) not in live
                    and _is_dead_key(channel.name, live)):
                doomed.append(i)
        for i in reversed(doomed):
            audio_data.channels.remove(i)
        removed += len(doomed)
    if removed:
        print(f"Audio Nodes: removed {removed} stale data channel(s)")
    return removed


# Skip-work bookkeeping for the 60fps timer
_last_sequence = None
_redraw_counter = 0
_last_error_time = 0.0
_reconcile_counter = 0


def _update_audio_data():
    """Timer callback to update audio data properties."""
    global _last_sequence, _redraw_counter, _last_error_time, _reconcile_counter

    # Self-healing engine sync (~2Hz): repairs nodes/links whose Blender
    # add events were missed, so live edits never require an engine
    # restart. Cheap no-op when everything is in sync.
    _reconcile_counter += 1
    if _reconcile_counter >= 30:
        _reconcile_counter = 0
        try:
            from .nodes.core import node_tree
            node_tree.ensure_engine_sync()
        except Exception as e:
            import time as _time
            now = _time.monotonic()
            if now - _last_error_time > 5.0:
                _last_error_time = now
                print(f"Audio Nodes: engine sync error: {e!r}")

    data = read_visualization_data()

    if data is None:
        return 1/60  # Try again in ~16ms

    # Nothing new since last frame (engine throttles writes to ~120Hz, and
    # goes quiet when idle) - skip all per-frame work.
    if data['sequence'] == _last_sequence:
        return 1/60
    _last_sequence = data['sequence']

    # Cache the full spectrum for the node-editor overlay / tap picking
    global _latest_spectrum, _latest_spectra_by_node
    _latest_spectrum = (data['fft_left'], data['fft_right'], _engine_sample_rate)
    _latest_spectra_by_node = {
        node_id: (slot['fft_left'], slot['fft_right'], _engine_sample_rate)
        for node_id, slot in data.get('fft_nodes', {}).items()
    }

    # Update scene audio data
    try:
        scene = bpy.context.scene
        if hasattr(scene, 'audio_nodes_data'):
            audio_data = scene.audio_nodes_data
            
            # Update or create channels for basic values
            basic_channels = [
                ('peak_left', data['peak_left']),
                ('peak_right', data['peak_right']),
                ('rms_left', data['rms_left']),
                ('rms_right', data['rms_right']),
            ]

            # Add MIDI channels
            midi = data['midi']
            basic_channels.extend([
                ('midi_last_note', float(midi['last_note'])),
                ('midi_last_velocity', float(midi['last_velocity']) / 127.0),
                ('midi_note_gate', float(midi['note_gate'])),
                ('midi_bpm', midi['bpm']),
                ('midi_transport_playing', float(midi['transport_playing'])),
            ])
            
            # Add all CC values
            for i, cc_val in enumerate(midi['cc_values']):
                basic_channels.append((f"midi_cc_{i}", cc_val))

            # Per-device-input-channel peak meters (Audio Input node UI and
            # the Audio Devices panel channel list read these).
            basic_channels.append(
                ('input_channel_count', float(data['input_channel_count'])))
            for i, peak in enumerate(data['input_peaks']):
                basic_channels.append((f"input_peak_{i}", peak))
            
            # Add Sequencer channels
            seq = data['sequencer']
            basic_channels.extend([
                ('seq_current_step', float(seq['current_step'])),
                ('seq_is_playing', float(seq['is_playing'])),
                ('seq_trigger_active', float(seq['trigger_active'])),
                ('seq_bpm', seq['bpm']),
            ])
            
            for channel_name, value in basic_channels:
                if channel_name not in audio_data.channels:
                    channel = audio_data.channels.add()
                    channel.name = channel_name
                audio_data.channels[channel_name].value = value
            
            # Mirror Blender node mute into engine bypass (live)
            _sync_mute_states()

            # Update FFT analyzer Peak/RMS channels
            _update_fft_analyzer_outputs(audio_data, data)

            # LFO + MIDI CC first so tap param inputs read fresh channels
            _update_lfo_nodes(audio_data, data)
            _update_midi_node_outputs(audio_data, data)

            # Per-sequencer transport channels (must land before
            # _update_control_connections resolves Trigger/Step/Playing)
            _update_sequencer_outputs(audio_data, data)

            # Bang / ADSR control outputs, for the same reason: everything
            # downstream of them resolves against these channels.
            _update_control_node_outputs(audio_data, data)

            # One dt for this tick, shared by tap Accumulate outputs and
            # the control nodes so both integrate over the same time step.
            from .nodes.control import control_nodes as _control_nodes
            tick_dt = _control_nodes.advance_tick()

            # Frequency tap channels (user-picked bands + param overrides)
            _update_fft_tap_outputs(audio_data, data)

            # Tap Accumulate outputs integrate this tick's tap values, and
            # land before Math / control nodes / Value Output read them.
            _update_tap_accumulators(audio_data, tick_dt)

            # Update math node control outputs
            _update_math_nodes(audio_data)

            # Control nodes (Trigger, Accumulator, Decay, Camera Cutter),
            # then Math again for anything downstream of them
            if _has_control_nodes():
                _update_control_nodes(audio_data, tick_dt)
                _update_math_nodes(audio_data)

            # Update control socket connections (LFO -> Filter, Math -> others, etc.)
            _update_control_connections(audio_data, data)
            
            # Update Value Output nodes (write to modifier inputs for Geometry Nodes)
            _update_value_output_nodes(audio_data, data)

            # Update NLA Scrub nodes (drive NLA strip playheads)
            _update_nla_scrub_nodes(audio_data)

            # Forget in-memory state for nodes that no longer exist (~0.1Hz)
            _prune_stale_caches()

            # Re-evaluate any drivers using audio_tap()/audio_channel()
            from . import audio_drivers
            audio_drivers.refresh_driver_dependencies()
            
            # Trigger UI redraws. Node editors follow the data every frame;
            # 3D viewport / properties (driven by Value Output nodes) are
            # refreshed at 20Hz - redrawing everything at 60Hz burned CPU/GPU
            # for no visible benefit.
            #
            # Iterate every window rather than bpy.context.screen: that is
            # only the ACTIVE window's screen, so a node editor torn off
            # into a second window (Window > New Window) was never tagged.
            # Its spectrum then repainted only when that window happened to
            # redraw for another reason, which reads as lag/stutter even
            # though the underlying data was updating fine.
            # bpy.context.screen can also be None in some contexts; looping
            # the window manager avoids that too.
            _redraw_counter += 1
            redraw_heavy = (_redraw_counter % 3) == 0
            for window in bpy.context.window_manager.windows:
                screen = window.screen
                if screen is None:
                    continue
                for area in screen.areas:
                    if area.type == 'NODE_EDITOR':
                        area.tag_redraw()
                    elif redraw_heavy and area.type in ('VIEW_3D', 'PROPERTIES'):
                        area.tag_redraw()

    except Exception as e:
        # Never kill the timer, but don't silently swallow errors either -
        # log at most once every 5 seconds.
        import time as _time
        now = _time.monotonic()
        if now - _last_error_time > 5.0:
            _last_error_time = now
            print(f"Audio Nodes: update timer error: {e!r}")

    return 1/60  # Run at 60fps


_timer_registered = False


def register_timer():
    """Register the update timer."""
    global _timer_registered
    
    if not _timer_registered:
        bpy.app.timers.register(_update_audio_data, persistent=True)
        _timer_registered = True


def unregister_timer():
    """Unregister the update timer."""
    global _timer_registered
    
    if _timer_registered:
        try:
            bpy.app.timers.unregister(_update_audio_data)
        except:
            pass
        _timer_registered = False
    
    _close_shared_memory()
