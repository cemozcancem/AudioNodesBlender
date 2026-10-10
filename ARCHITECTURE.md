# Audio Nodes - Architecture Guide

This document provides detailed technical documentation for AI agents and developers working on the Audio Nodes project. It explains the system architecture, code organization, data flows, and extension patterns.

## Table of Contents

1. [System Overview](#system-overview)
2. [Blender Add-on Structure](#blender-add-on-structure)
3. [C++ Engine Structure](#c-engine-structure)
4. [Data Flow](#data-flow)
5. [Key Implementation Details](#key-implementation-details)
6. [Extension Guide](#extension-guide)
7. [ZMQ Command Protocol Reference](#zmq-command-protocol-reference)
8. [Shared Memory Reference](#shared-memory-reference)

---

## System Overview

Audio Nodes uses a **two-process architecture**:

```
┌─────────────────────────────────────┐     ┌─────────────────────────────────────┐
│         BLENDER PROCESS             │     │         ENGINE PROCESS              │
│         (Python 3.11+)              │     │         (C++17 / JUCE)              │
│                                     │     │                                     │
│  ┌─────────────────────────────┐    │     │    ┌─────────────────────────────┐  │
│  │     Blender Add-on          │    │     │    │    AudioNodesEngine.app     │  │
│  │  - Node Editor UI           │    │     │    │  - Real-time DSP            │  │
│  │  - Property panels          │    │     │    │  - Audio I/O                │  │
│  │  - Driver integration       │    │     │    │  - MIDI handling            │  │
│  └──────────┬──────────────────┘    │     │    └──────────┬──────────────────┘  │
│             │                       │     │               │                     │
│             │ ZeroMQ REQ            │     │               │ ZeroMQ REP          │
│             └───────────────────────┼─────┼───────────────┘                     │
│                                     │     │                                     │
│             ┌───────────────────────┼─────┼───────────────┐                     │
│             │ Shared Memory (mmap)  │     │               │                     │
│             │ (read-only)           │     │               │ (write-only)        │
│             └───────────────────────┘     └───────────────┘                     │
└─────────────────────────────────────┘     └─────────────────────────────────────┘
```

### Why Two Processes?

1. **Real-time Safety**: Audio processing requires deterministic timing. Python's GIL and garbage collector would cause audio glitches.
2. **Stability**: If the audio engine crashes, Blender remains stable.
3. **Performance**: C++ with JUCE provides low-latency audio processing.

### Communication Channels

| Channel | Direction | Purpose | Latency |
|---------|-----------|---------|---------|
| ZeroMQ (TCP 5555) | Bidirectional | Commands, responses | ~1-10ms |
| Shared Memory | Engine → Blender | Visualization data | ~0ms (direct memory access) |

### ZMQ Socket Architecture (v2.5.0+)

The Blender add-on uses **two separate ZMQ sockets** to avoid protocol violations:

```
┌─────────────────────────────────────────────────────────────┐
│                    BLENDER PROCESS                          │
│                                                             │
│  ┌─────────────────┐          ┌─────────────────┐          │
│  │  Main Thread    │          │  Param Thread   │          │
│  │  (UI, critical) │          │  (background)   │          │
│  └────────┬────────┘          └────────┬────────┘          │
│           │                            │                    │
│  ┌────────▼────────┐          ┌────────▼────────┐          │
│  │  Main Socket    │          │  Param Socket   │          │
│  │  (REQ, 3s TO)   │          │  (REQ, 500ms TO)│          │
│  └────────┬────────┘          └────────┬────────┘          │
│           │                            │                    │
└───────────┼────────────────────────────┼────────────────────┘
            │                            │
            │     TCP Port 5555          │
            │                            │
┌───────────▼────────────────────────────▼────────────────────┐
│                    C++ ENGINE                               │
│                                                             │
│           ┌─────────────────────────────┐                   │
│           │      CommandServer          │                   │
│           │      (ZMQ REP socket)       │                   │
│           │  Handles both connections   │                   │
│           └─────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

**Why two sockets?**
- ZMQ REQ sockets require strict send-recv alternation
- A single socket shared between threads causes protocol violations
- Main socket: synchronous device queries/settings and ping.
- Sender socket: ordered create/delete/connect/disconnect and sample loads,
  followed by coalesced parameter updates (~30/sec). It never accesses Blender
  data; the main thread captures the port before starting the worker.

### Command acknowledgements and sample loading (4.14.1)

`command_client` keeps pending creates separate from engine-confirmed nodes.
The sender parses each response and records errors for the Engine panel.
Reconciliation retries unconfirmed creates and failed ports against the current
Blender graph. An uncertain create is deleted before recreating the same ID,
then its sample/pattern/device state is replayed. This handles lost create
replies without claiming a duplicate-ID error is success. Python-only nodes'
links are excluded from the engine graph.

`load_sample` and `load_drum_sample` now return queue acceptance, **not load
success**. Consumers read `get_sample_load_state(node_id, slot=-1)` for Loading,
Loaded, or Error. Requests share the graph FIFO, so creates precede loads without
main-thread flushing. Runtime status carries a request ID and resolved path;
a late response cannot overwrite a newer file selection. The worker polls for
sample responses with a bounded wait and checks shutdown between polls.

### Control evaluation (4.14.1)

Gain and Output use the engine's ramped `mute`; other engine nodes use bypass.
Parameter routing tracks active modulation separately from its last sent value.
When a cable is removed, routing restores the property's base value and clears
that cache; manual property edits invalidate it too.

Math nodes use `_resolve_control_source_value` to resolve tap sockets by stable
ID and are evaluated in topological order within each tree. Cycles and Math
nodes depending on them publish zero with a visible warning. These controls
still run at the Blender timer rate, not the audio sample rate.

---

## Blender Add-on Structure

### Module Responsibilities

```
blender_addon/
├── __init__.py              # Registration, class list, handlers
├── command_client.py        # ZMQ communication with engine
├── engine_manager.py        # Engine process lifecycle
├── shared_memory_reader.py  # mmap reading, timer callback
├── dependencies.py          # pyzmq auto-installation
├── preferences.py           # Add-on preferences UI
├── permissions.py           # macOS audio permission request
├── nodes/                   # Node definitions
│   ├── core/
│   │   ├── node_base.py     # AudioNodeBase + find_node() helper
│   │   ├── node_tree.py     # AudioNodeTree, socket types, link validation
│   │   └── *.py             # Input/Output/Value Output/NLA Scrub
│   ├── effects/             # Filter, Gain, Delay, Mixer, Math, FFT + taps
│   ├── midi/                # MIDI In/Out/CC/Clock/Monitor
│   ├── samplers/            # Sampler, Drum Sampler, ADSR
│   ├── timing/              # Oscillator, LFO, Sequencer, Bang
│   └── control/             # Trigger, Accumulator, Decay, Camera Cutter
│                            #   (Blender-only; logic in control_logic.py)
└── ui/                      # UI components
    ├── editor.py            # Header draw fn appended to NODE_HT_header
    ├── panels.py            # Side panel (N-panel)
    ├── menus.py             # Add menu (Shift+A)
    └── spectrum_overlay.py  # GPU spectrum + tap picker
```

### Class Hierarchy

```
bpy.types.Node
    └── AudioNodeBase (node_base.py)
            │
            ├── AudioInputNode
            ├── AudioOutputNode
            ├── AudioFilterNode
            ├── AudioGainNode
            ├── AudioDelayNode
            ├── AudioOscillatorNode
            ├── AudioFFTAnalyzerNode
            ├── AudioSamplerNode
            ├── AudioDrumSamplerNode
            ├── AudioSequencerNode
            ├── AudioBangNode
            ├── AudioADSRNode
            ├── AudioMixerNode
            ├── AudioMathNode
            ├── AudioMIDIInputNode
            ├── AudioMIDIOutputNode
            ├── AudioMIDICCNode
            ├── AudioMIDIClockNode
            ├── AudioLFONode
            └── AudioValueOutputNode
```

### Key Classes

#### `AudioNodeBase` (node_base.py)
Base class for all audio nodes. Provides:
- `engine_node_id`: Unique identifier for C++ engine
- `node_type`: Property returning C++ node type string
- `register_with_engine()`: Sends `create_node` command
- `unregister_from_engine()`: Sends `delete_node` command
- `send_param(name, value)`: Sends `set_param` command
- `get_initial_params()`: Override to provide default parameters
- `copy(node)`: New id + `register_with_engine()`, **then**
  `_restore_engine_state()`. `create_node` params carry no sample path,
  MIDI device or drum slots, so without the replay a duplicated Sampler
  shows its path and plays silence until the engine restarts.

`node_base.py` also exports **`find_node(context, node_name, bl_idname,
tree_name)`** — the single node resolver every operator uses. Node names are
unique *per tree*, not globally, so scanning `bpy.data.node_groups` and taking
the first match acts on the wrong node once a second tree exists. The editor
the button was drawn in is authoritative; `tree_name` overrides it, which is
required for the ImportHelper operators (Load Sample, Load Drum Sample) whose
`execute()` runs with the file browser as context.

**Gotcha:** a bpy Node instance is a throwaway Python wrapper — Blender builds
a new one on every `tree.nodes` access. Attributes written onto `self` are
gone by the next frame. Per-node runtime state must live in a module-level
dict keyed by `engine_node_id` (see `lfo_node._RANDOM_STATE`).

```python
class AudioNodeBase(bpy.types.Node):
    engine_node_id: StringProperty()
    
    def init(self, context):
        self.engine_node_id = f"{self.node_type}_{uuid.uuid4().hex[:8]}"
        self.register_with_engine()
    
    def free(self):
        self.unregister_from_engine()
    
    @property
    def node_type(self) -> str:
        return "base"  # Override in subclasses
```

#### `AudioNodeTree` (node_tree.py)
Custom node tree type for Audio Nodes. Handles:
- Connection change detection via `update()` method
- Syncing all connections to engine via `sync_all_connections()`

#### Socket Types (node_tree.py)
- `AudioSocket` (green): Audio signal connections routed through C++ engine
- `ControlSocket` (orange): Control/modulation signals, has `default_value`
  - Control connections are processed in Python (`shared_memory_reader.py`)
  - Values from LFO, MIDI CC (optional Min/Max remap), FFT bands can modulate node parameters
  - Nodes with `get_modulatable_params()` method support control input modulation

### Registration Flow

1. `register()` in `__init__.py` is called when add-on is enabled
2. All classes in `classes` list are registered with `bpy.utils.register_class()`
3. `audio_nodes_data` property is added to `bpy.types.Scene`
4. Node categories are registered for Shift+A menu
5. Shared memory timer is started (60fps)
6. macOS audio permission is requested
7. Quit handler is registered to stop engine on exit

### Frequency Taps and the Spectrum Overlay

The FFT Analyzer's live spectrum and frequency-tap picking are pure
Blender-side features layered on data that already crosses the shared
memory bridge:

- `shared_memory_reader` caches the latest `(fft_left, fft_right,
  sample_rate)` each frame (`get_latest_spectrum()`).
- `ui/spectrum_overlay.py` registers a `SpaceNodeEditor` draw handler
  (`POST_PIXEL`) that paints the spectrum into an area `draw_buttons`
  reserves with empty rows at the top of each analyzer node. Node-space
  geometry is calibrated by constants (HEADER_H, SOCKET_H, ROW_H) and
  converted to region pixels via `view2d.view_to_region`, so it follows
  pan/zoom.
- Taps live in a `CollectionProperty` on the analyzer node
  (`nodes/effects/fft_taps.py`). `sync_tap_sockets()` mirrors them as
  control **output** sockets and per-tap **parameter inputs**
  (Hz / Oct / Gain / Smooth / Min / Max / Prob), keyed by stable
  `tap_id` + `tap_param`. Socket **names** are unique per tap
  (`"{tap} · Gain"`) so Blender does not migrate links across taps when
  visibility changes. Only the **active** tap’s param inputs are shown
  (`socket.hide`); hidden sockets keep their cables.
- Linked param inputs absolute-drive tap computation in parameter units
  (clamped to each property's min..max). LFO 0..1 is mapped onto that
  range in the reader; MIDI CC Min/Max should match the target range.
  MIDI CC channels update before tap compute so cables work same-frame.
- Per-tap band energy is computed in
  `shared_memory_reader._update_fft_tap_outputs` (bin range from center
  Hz + width in octaves, using the engine-reported sample rate) and
  published as `{engine_node_id}_tap_{tap_id}` channels, which the
  existing control-connection resolver and Value Output nodes consume
  unchanged.
- The per-tap value pipeline (4.5) is: band energy → gain → optional
  random trigger gate (`use_random_trigger` / `trigger_probability`; an
  onset above 0.05 rolls the dice once and holds the verdict until the
  band falls silent) → smoothing → clamp 0-1 → map to the tap's
  `out_min`/`out_max` range. Everything downstream (tap sockets, Value
  Output, `audio_tap()` drivers) sees the mapped value; the UIList level
  bar normalizes back to 0-1 for display.
- Driver expressions address **both** the analyzer and the tap by a
  **stable reference**, not by name or position:
  `audio_tap("FFT Analyzer#7ac41f02", "Lights#61f03ca0")`. The node half
  (4.13) is `fft_taps.node_ref` / `resolve_analyzer`, keyed on the trailing
  hex of `engine_node_id`; without it, renaming the analyzer silently broke
  every driver pointing at it. Both halves follow the same rule: when a ref
  spells out an id there is no name fallback. Bare node names still resolve
  when unambiguous, so pre-4.13 files keep working. The part after
  `#` is the tap's permanent `tap_id`; the name in front is a human hint
  only. `fft_taps.resolve_tap_index` accepts a tap_id, a `Name#id` pair, a
  bare (unambiguous) tap name, or a legacy integer position. When a ref
  spells out an id, the id is the authority — if that tap was deleted the
  ref resolves to nothing and the driver returns its default, so a later
  tap that lands in the freed slot can never inherit it. Bare integers
  still resolve by position for old files, and are the thing this exists
  to replace.
- The analyzer node shows what each tap currently drives
  (`fft_taps.get_tap_targets`, cached scan every 2s): node-graph links
  are followed to Value Output nodes (object › modifier input), and all
  driver expressions mentioning `audio_tap("<node>", "<ref>")` are
  resolved through the same helper to human-readable labels (object
  property with axis, pose bone, shape key, or `owner › node name` for
  shader/compositor/geometry node trees); a ref pointing at a deleted tap
  lists nothing.
- `audionodes.add_tap` is a modal operator (PICK and LEARN modes); the
  engine and ZMQ protocol are untouched by the whole feature.
- **Link validation (4.13).** Audio (`AudioSocketType`) and Control
  (`ControlSocketType`) sockets cannot be cross-connected.
  `node_tree._link_type_is_valid` / `prune_invalid_links` run from the
  debounced sync timer, whether or not the engine is up; the connection diff
  and `engine_manager.sync_all_nodes_to_engine` both skip cross-type links
  independently, so a bogus pair can never reach the engine. Sockets the
  add-on does not own are left alone.
- **Mute routing (4.13).** `shared_memory_reader._sync_mute_states` mirrors
  Blender's node mute to the engine: `mute` for output nodes (a real param
  with a 20 ms `SmoothedValue` gain ramp) and `bypass` for every other node.
  Do **not** add a custom `mute` property to a node class — `bpy.types.Node`
  already defines one and Blender silently discards the override, so the
  `update=` callback never fires.
- **Per-sequencer slots (4.14).** Every live Step Sequencer publishes its own
  transport into `AudioVisualizationData.sequencer_slots[]`, keyed by engine
  node id - the same shape as `fft_slots[]`. The top-level `sequencer` field
  mirrors slot 0 (the oldest) for backward compatibility only; nothing in the
  add-on reads it for a node's own state any more.
  `AudioSequencerNode.get_output_channel_name` returns
  `{engine_node_id}_trigger|step|playing`, published by
  `shared_memory_reader._update_sequencer_outputs`. **The slots were APPENDED
  to the struct**, so every offset that existed before 4.14 is unchanged -
  follow that rule for any future field, and add a matching `static_assert`.
- **Control-source slots (4.15).** Bang and ADSR publish their control lanes
  into `AudioVisualizationData.control_slots[]`, keyed by engine node id, and
  `shared_memory_reader._update_control_node_outputs` turns them into
  `{engine_node_id}_{socket_name}` channels - the names
  `_resolve_control_source_value` already looks for. Any future engine node
  whose output is control-only needs `NodeKind` registration in
  `NodeManager::addAudioNode` to appear here. **APPENDED to the struct**, so
  every pre-4.15 offset is unchanged; the Python reader maps whatever size
  the region actually is (`KNOWN_MEMORY_SIZES`) instead of insisting on its
  own, and `tests/test_meter_and_layout.py` compares the C++ `static_assert`
  offsets against the reader's constants.
- **Triggers are events, not parameters (4.15).** `command_client.set_parameter`
  coalesces per (node, param) at ~30Hz, which is right for a knob and wrong
  for a trigger - two drum pad hits in the same window became one. Use
  `AudioNodeBase.send_event()` / `command_client.send_event()` for `fire`,
  `trigger_N`, `gate`, `play` and `stop`: one ordered message each. Engine
  side, `BangNode` counts fire requests rather than latching a flag, and a
  retrigger arriving while the pulse is still high forces one block at zero
  so downstream edge detectors see a real falling edge.
- **In-node graphics (4.15).** `ui/node_graphics.py` paints Sampler, ADSR,
  Delay and Filter node bodies with the same mechanism as
  `ui/spectrum_overlay.py`: the node reserves empty rows at the TOP of
  `draw_buttons` (`GRAPH_ROWS`, gated by `show_graph`) and a
  `SpaceNodeEditor` draw handler locates that rectangle by assuming it starts
  directly below the last visible output socket. **The reserved rows must be
  the first thing the node draws** - anything above them shifts the real rows
  down while the painting stays put. Sample waveforms are decoded through
  Blender's own audaspace and cached by (path, mtime).
- **Collapsible sockets (4.14).** The 8ch Mixer hides a channel's Level/Pan
  control inputs unless that channel is expanded or already linked
  (`update_channel_socket_visibility`). Socket `hide` is saved in the .blend,
  so `__init__._refresh_collapsible_sockets()` re-applies the rule on file
  load for nodes authored before the change. As with the FFT Analyzer's tap
  parameter inputs, **never write `sock.hide` from `draw_buttons`** - it is an
  ID write and Blender forbids it during draw.
- **Engine lifecycle (4.13).** `__init__.on_file_load_pre` stops the engine
  before a .blend is replaced; `on_file_load_post` schedules
  `_post_load_housekeeping`, which prunes stale scene channels and then
  starts the engine if `auto_start_engine` is on and the incoming file
  contains an `AudioNodeTree`. Deferred onto a timer because `start_engine()`
  blocks up to 15 s waiting for ZMQ.
- As of 4.2 taps are the ONLY band mechanism: the fixed Low/Mid/High
  band sockets, per-band gains, and the global `band_*` channels were
  removed. Pre-4.2 files migrate automatically
  (`migrate_legacy_band_sockets`): linked band sockets become taps with
  the same frequency range and gain, links are rewired, and the legacy
  sockets are deleted.

### Timer Callback (shared_memory_reader.py)

`_update_audio_data()` runs at 60fps and:
1. Reads visualization data from shared memory
2. Updates `scene.audio_nodes_data.channels` collection
3. Detects connection changes and syncs to engine
4. Evaluates Math, then Control nodes (`_update_control_nodes`), then Math
   again so Math fed by a control node is fresh the same tick
5. Advances NLA Scrub nodes (`_update_nla_scrub_nodes`)
6. Triggers node editor redraw

### Control nodes (nodes/control/, 5.0.0)

Blender-only nodes (`node_type` in `BLENDER_ONLY_NODE_TYPES`, engine
registration is a no-op) that carry state from tick to tick - the thing a
driver expression cannot do:

| Node | Inputs | Outputs | What it does |
|------|--------|---------|--------------|
| Trigger | Signal | Trigger, Gate, Count | Rising edge over Threshold, re-arms below Re-arm (hysteresis), Cooldown, Probability, Pulse length (>= 1 tick) |
| Accumulator | Rate, Reset | Value, Wrap | `value += (base + gain*rate) * dt`; Wrap / Ping-Pong / Clamp / Unbounded |
| Decay | Trigger, Signal | Value | Jump to Peak on a trigger edge, follow Signal upward, fall to 10 % in Decay seconds |
| Camera Cutter | Speed, Cut | Progress, Shot, Cut, Time | Shot list of cameras (+ optional extra object each); Progress advances at `(base + gain*Speed)/length`; Cut edge switches shot after Min Shot; sets `scene.camera`; scrubs each shot's keyframed move via the NLA strip_time mechanism below |

* The state machines are plain Python in `control_logic.py` (no bpy) and are
  covered by `tests/test_control_logic.py`. Node classes are thin wrappers.
* Runtime state lives in `control_nodes._STATE` keyed by engine_node_id and is
  pruned by `_prune_stale_caches()`; it is never saved.
* `dt` is real time between ticks (clamped to 0.25 s), so behaviour is
  frame-rate independent. The pass is skipped entirely when a file has no
  control nodes.
* Every output is a channel `<engine_node_id>_<socket>`, so they feed Math,
  engine params, Value Output, NLA Scrub or `audio_channel("...")` drivers
  (sidebar lists a Copy Driver Expression button per output).
* Camera Cutter's **Set Up Shots** moves a shot object's active action onto an
  NLA track named `Audio Nodes Shot` (strip extrapolation Hold, animated
  strip time). **Record** bakes the live cut into a separate camera: one key
  per tick at `frame_start + elapsed * fps`, LINEAR within a shot and CONSTANT
  across every cut, then sets `frame_end` to the take.

### NLA Scrub (nodes/core/nla_scrub_node.py)

Drives an NLA strip's playhead from a control signal **without using the
Blender timeline**. `scene.frame_current` is never touched.

The node writes exactly **one float per tick** and lets Blender's own NLA
system evaluate the pose in C. On a 468-channel Mixamo rig that measured
0.03 ms/tick, against 2.57 ms/tick for the previous approach of evaluating
every F-Curve in Python and writing each channel onto the target. Quaternion
blending, `blend_type`, strip influence and NLA layering all come for free
because Blender does the evaluation.

**The non-obvious part.** `strip.strip_time` looks like the knob but
assigning to it does nothing: with `use_animated_time` enabled, Blender
recomputes `strip_time` during animation evaluation from the strip's own
F-Curve and discards whatever was written. A *driver* on `strip_time` is
ignored for the same reason — both were measured returning byte-identical
pose matrices across a full 1..213 sweep.

The value that actually moves the rig is the keyframe inside that strip
F-Curve:

```python
strip.fcurves['strip_time'].keyframe_points[0].co[1] = playhead
```

The F-Curve is evaluated at the scene frame, so with a single keyframe it
acts as a constant — and rewriting that constant each tick is the whole
mechanism. Handles are pinned to the same value so the curve cannot bend
away from it, then `fcurve.update()` and `object.update_tag()`.

`ensure_animated_time()` sets `use_animated_time` and creates the F-Curve
and its keyframe if the strip has never been set up (exposed as the node's
**Set Up Strip** button).

**Scope:** scrub only. The playhead is wherever the Time input says it is.
There is no transport — no one-shot triggering, no voices, no loop modes —
because that requires per-frame state this design deliberately does not
carry. Looping is available on the strip itself via
`use_animated_time_cyclic`.

### Audio Device Persistence

`AudioNodesData` (on `bpy.types.Scene`) stores two `StringProperty`s that
are saved with the `.blend` file:

- `input_device_name`  — last-applied audio input device
- `output_device_name` — last-applied audio output device

When the user picks a device in the Audio Devices panel
(`AUDIONODES_OT_set_device`), both the engine and the scene properties are
updated. When the engine starts (`engine_manager.start_engine()`), the
helper `restore_saved_audio_devices()` runs immediately after ZMQ is up —
before node sync — and re-applies the saved device names via two
`set_device` ZMQ commands. This avoids the long feedback loop that would
otherwise occur on macOS when the system default device is a virtual
loopback (e.g. BlackHole) and JUCE auto-picks it for both input and
output.

### Live Device Switching (AudioEngine::applyDeviceChange)

`set_device` commands run on the JUCE message thread (CoreAudio must be
reconfigured there) and perform a full teardown + reopen:
`removeAudioCallback` → `closeAudioDevice` → `scanForDevices` (so
hot-plugged devices are switchable) → `setAudioDeviceSetup` (with sample
rate / buffer size / channel masks zeroed so JUCE re-negotiates for the
new device) → `addAudioCallback`. On failure the previous device is
reopened so audio never stays dead.

The historical "switching needs an engine restart" bug was NOT CoreAudio:
every device start re-runs `audioDeviceAboutToStart` →
`NodeManager::prepare`, and `FFTAnalyzerNode::prepare` used to call
`reset()`, whose `BaseNode::reset()` clears the node's graph input
pointers. The analyzer (and everything downstream) silently disconnected
on every device switch, and Blender never re-sent the links because its
connection cache still considered them present — only a full engine
restart (fresh sync) rebuilt them. `prepare()` now clears only the
FFT/waveform staging state (`clearAnalysisState()`) and never touches
graph edges. Node `prepare()` implementations must never call
`BaseNode::reset()` for this reason.

---

## C++ Engine Structure

### Component Ownership

```cpp
// Main.cpp - AudioNodesApplication
class AudioNodesApplication : public juce::JUCEApplication {
    std::unique_ptr<NodeManager> nodeManager_;
    std::unique_ptr<SharedMemoryBridge> sharedMemory_;
    std::unique_ptr<AudioEngine> audioEngine_;
    std::unique_ptr<MIDIManager> midiManager_;
    std::unique_ptr<CommandServer> commandServer_;
};
```

### Initialization Order

1. `NodeManager` - DSP graph management
2. `SharedMemoryBridge` - mmap setup
3. `AudioEngine` - JUCE AudioDeviceManager
4. `MIDIManager` - MIDI device handling
5. `CommandServer` - ZMQ server

### Wiring

```cpp
audioEngine_->initialize(nodeManager_.get(), sharedMemory_.get());
audioEngine_->setMIDIManager(midiManager_.get());
nodeManager_->setMIDIManager(midiManager_.get());
commandServer_->setAudioEngine(audioEngine_.get());
commandServer_->setNodeManager(nodeManager_.get());
commandServer_->setMIDIManager(midiManager_.get());
```

### Key Classes

#### `AudioEngine` (AudioEngine.cpp)
- Manages JUCE `AudioDeviceManager`
- Implements `juce::AudioIODeviceCallback`
- Routes audio through `NodeManager` in `audioDeviceIOCallbackWithContext()`
- Updates `SharedMemoryBridge` after each audio block

#### `NodeManager` (NodeManager.cpp)
- Maintains map of `std::unique_ptr<BaseNode>` by ID
- Processes command queue from main thread
- Calls `node->process()` in audio thread
- Tracks special nodes: `inputNodes_[]` / `outputNodes_[]` (creation-ordered;
  every Output is mixed into the device buffer), `fftAnalyzers_[]`, `sequencers_[]`
- Uses destination-port-aware connection tracking (`destNode:destPort`)

#### `CommandServer` (CommandServer.cpp)
- Runs ZMQ REP socket in separate thread
- Parses JSON commands and dispatches to handlers
- All `handle*` methods return JSON responses

#### `BaseNode` (nodes/BaseNode.h)
Base class for all DSP nodes:
```cpp
class BaseNode {
public:
    virtual void process(juce::AudioBuffer<float>& buffer, int numSamples) = 0;
    virtual void prepare(double sampleRate, int maxBlockSize);
    virtual void setParameter(const std::string& name, float value) = 0;
    
    void setInputNode(BaseNode* node);  // Legacy/default "input" connection
    void setInputNodeForPort(const std::string& port, BaseNode* node);
    BaseNode* getInputNodeForPort(const std::string& port) const;
    void startFadeOut();                 // Click-free deletion
    
protected:
    BaseNode* inputNode_ = nullptr;
    std::unordered_map<std::string, BaseNode*> inputNodesByPort_;
    double sampleRate_ = 44100.0;
    std::atomic<bool> fadingOut_{false};
};
```

### Audio Thread Safety Rules

**NEVER do these in `process()`:**
- Allocate memory (`new`, `malloc`, `std::vector::push_back`)
- Lock mutexes (use lock-free alternatives)
- File I/O
- Network I/O
- Call non-realtime-safe functions

**Safe patterns:**
- Use `std::atomic<>` for parameters
- Use lock-free queues for commands
- Pre-allocate buffers in `prepare()`
- Use `juce::SmoothedValue` for parameter smoothing

### Threading Model (v3.1.0)

Graph mutations live on the **command thread** (the ZMQ server thread); the
audio thread only ever performs pointer swaps:

- **Command thread** owns `nodes_` (id → `unique_ptr`) and `connections_`.
  It constructs nodes, calls `prepare()`, resolves port names to integer
  indices (`BaseNode::resolvePortIndex`), and pushes POD `GraphCommand`s
  (pointers + indices, no strings) into a lock-free SPSC queue.
- **Audio thread** pops those commands at block start: `AddNode` appends to
  a pre-reserved vector, `Connect`/`Disconnect` are array stores,
  `RemoveNode` starts a fade. When a fade completes, the node is unlinked
  from every remaining node (`clearInputsReferencing` — no dangling input
  pointers), removed from audio-side structures, and its pointer is pushed
  into a garbage queue.
- **Command thread** drains the garbage queue and runs destructors there
  (freeing sample buffers, joining the FFT worker) — never in the callback.
- `setParameter`/`getParameter` run on the command thread and touch atomics
  only. Anything not safe to mutate concurrently with `process()` (juce::ADSR
  parameters, sequencer timing, gate edges, oscillator waveform) is staged
  in atomics + a dirty flag and applied inside `process()`.

### Node Processing Flow

```cpp
void NodeManager::process(const AudioBuffer& input, AudioBuffer& output, int numSamples) {
    BaseNode::advanceEpoch();          // new per-block epoch

    applyPendingCommands();            // POD pointer swaps only

    // Bang/MIDI first, then Sequencer (so Play←Bang edges land same block)
    for (entry : audioNodes_ where Bang|MIDIInput)
        entry.node->pullOutput(controlScratch_, n);
    for (entry : audioNodes_ where Sequencer)
        entry.node->pullOutput(controlScratch_, n);

    // Feed every Input node the device buffer; mix every Output into
    // the device buffer (a lone Output is a direct pull — no extra copy)
    for (i : inputNodes_) inputNodes_[i]->setInputBuffer(input);
    output.clear();
    if (numOutputNodes_ == 1)
        outputNodes_[0]->pullOutput(output, numSamples);
    else
        for (i : outputNodes_) { pull → scratch; output.addFrom(scratch); }

    // Pump control sinks not reached through the audio graph
    for (entry : audioNodes_ where MIDIOutput|ADSR|FFTAnalyzer, not processed this epoch)
        entry.node->pullOutput(controlScratch_, n);

    reclaimFadedNodes(numSamples);     // unlink + hand to garbage queue
}
```

Connections are resolved to **integer port indices** on the command thread
(`input` → 0, `trig_N` → N, `input_N` → N-1, `audio_a`/`audio_b` → 0/1,
`gate` → 1 on ADSR). The audio thread indexes a fixed
`std::array<BaseNode*, MAX_PORTS>` — no strings, no hash maps, no allocation.

### Epoch-Cached Pull Model

Each node renders **at most once per block** through `pullOutput()`:

```cpp
void BaseNode::pullOutput(AudioBuffer& dest, int numSamples) {
    if (lastEpoch_ != currentEpoch_) {   // not yet rendered this block
        lastEpoch_ = currentEpoch_;      // marked BEFORE recursing
        cache_.clear();
        process(cache_, numSamples);     // node pulls ITS inputs the same way
    }
    copy(cache_ -> dest);
}
```

Consequences:
- **Diamond fan-outs** (one source feeding two inputs) get a copy of the
  cached output instead of processing the source twice (which used to double
  oscillator phase advance and delay-line consumption).
- **Feedback cycles** terminate: when a cycle reaches a node already marked
  for this epoch, it receives the previous block's output — a one-block
  feedback delay instead of infinite recursion and a stack overflow.

---

## Data Flow

### Node Creation Lifecycle

```
User adds node in Blender
         │
         ▼
AudioNodeBase.init()
         │
         ├─► Generate engine_node_id
         │
         ▼
register_with_engine()
         │
         ▼
command_client.create_node()
         │
         ▼
ZMQ send: {"type": "create_node", "node_id": "...", "node_type": "..."}
         │
         ▼
CommandServer.handleCreateNode()
         │
         ▼
NodeManager.queueCommand(CreateNode)
         │
         ▼
[Audio Thread] processPendingCommands()
         │
         ▼
NodeManager.createNode() → new FilterNode(id)
         │
         ▼
node->prepare(sampleRate, blockSize)
```

### Parameter Update Flow

```
User changes property in Blender UI
         │
         ▼
Property update callback: self.send_param("cutoff", value)
         │
         ▼
command_client.set_parameter()
         │
         ▼
ZMQ send: {"type": "set_param", "node_id": "...", "param": "cutoff", "value": 1000}
         │
         ▼
CommandServer.handleSetParam()
         │
         ▼
NodeManager.setParameter() [direct call, no queue]
         │
         ▼
node->setParameter("cutoff", 1000)  [atomic store]
```

### Audio Signal Flow

```
System Audio Input
         │
         ▼
AudioEngine.audioDeviceIOCallbackWithContext()
         │
         ▼
NodeManager.process(inputBuffer, outputBuffer, numSamples)
         │
         ├─► InputNode.setInputBuffer(inputBuffer)
         │
         ▼
OutputNode.process(outputBuffer)
         │
         ├─► FilterNode.process()  [recursive pull]
         │         │
         │         └─► GainNode.process()
         │                   │
         │                   └─► InputNode.process()
         │
         ▼
System Audio Output
```

### Visualization Data Flow

```
[Audio Thread]
AudioEngine.audioDeviceIOCallbackWithContext()
         │
         ▼
SharedMemoryBridge.update(nodeManager)
         │
         ├─► Get FFT data from FFTAnalyzerNode
         │
         ├─► Copy to writeBuffer_
         │
         └─► memcpy to shared memory (atomic via sequence counter)

─────────────────────────────────────────────────────────────────

[Blender Main Thread - 60fps timer]
_update_audio_data()
         │
         ▼
read_visualization_data()
         │
         ├─► Read sequence counter
         ├─► Read FFT, peaks, RMS, waveform
         ├─► Read sequence counter again (torn read detection)
         │
         ▼
Update scene.audio_nodes_data.channels
         │
         ▼
Trigger node editor redraw
```

---

## Key Implementation Details

### Lock-Free Command Queue

Commands from main thread to audio thread use `LockFreeQueue`:

```cpp
// common/LockFreeQueue.h
template<typename T, size_t Capacity>
class LockFreeQueue {
    std::array<T, Capacity> buffer_;
    std::atomic<size_t> writePos_{0};
    std::atomic<size_t> readPos_{0};
    
public:
    bool push(const T& item);      // Main thread
    std::optional<T> pop();        // Audio thread
};
```

### Click-Free Node Deletion

Nodes fade out over 50ms before removal to prevent clicks:

```cpp
void BaseNode::startFadeOut() {
    fadingOut_.store(true);
    fadeGain_.setTargetValue(0.0f);  // SmoothedValue
}

void BaseNode::applyFade(AudioBuffer& buffer, int numSamples) {
    if (fadingOut_) {
        for (int i = 0; i < numSamples; ++i) {
            float gain = fadeGain_.getNextValue();
            for (int ch = 0; ch < buffer.getNumChannels(); ++ch) {
                buffer.setSample(ch, i, buffer.getSample(ch, i) * gain);
            }
        }
        if (fadeGain_.getCurrentValue() < 0.001f) {
            fadeComplete_.store(true);
        }
    }
}
```

### FFT Frequency Band Calculations

```python
# shared_memory_reader.py
FREQUENCY_BANDS = {
    'sub_bass': (20, 60),
    'bass': (60, 250),
    'low_mid': (250, 500),
    'mid_range': (500, 2000),
    'high_mid': (2000, 4000),
    'presence': (4000, 6000),
    'brilliance': (6000, 20000),
}

def _freq_to_bin(freq: float) -> int:
    bin_resolution = SAMPLE_RATE / (2 * FFT_SIZE)  # ~21.5 Hz
    return int(freq / bin_resolution)

def _calculate_band_energy(fft_data, low_hz, high_hz) -> float:
    low_bin = _freq_to_bin(low_hz)
    high_bin = _freq_to_bin(high_hz)
    return sum(fft_data[low_bin:high_bin + 1]) / (high_bin - low_bin + 1)
```

### Sequencer Timing

The time signature is real timing, not a label. BPM is quarter-note based;
one BEAT is the note value named by the denominator; steps subdivide that
beat; and the LOOP LENGTH is derived from the meter rather than set
independently.

```cpp
// SequencerNode.cpp
int SequencerNode::derivedStepCount() const {
    return clamp(beatsPerBar_ * stepsPerBeat_ * bars_, 1, MAX_STEPS);
}

void SequencerNode::calculateTiming() {
    double samplesPerQuarter = (sampleRate_ * 60.0) / bpm_;
    double samplesPerBeat    = samplesPerQuarter * (4.0 / beatUnit_);
    samplesPerStep_          = samplesPerBeat / stepsPerBeat_;

    stepCount_.store(derivedStepCount());          // loop follows the meter
    if (currentStep_ >= stepCount_) currentStep_ = 0;
}
```

|   Meter | Grid | Steps | Step value | Bar @120 BPM |
|--------:|-----:|------:|------------|-------------:|
| 4/4     |    4 |    16 | 16th       |        2.00s |
| 7/8     |    2 |    14 | 16th       |        1.75s |
| 6/8     |    3 |    18 | 16th-ish   |        1.50s |

Before 4.15 `beatsPerBar_` was stored and never read: 7/8 looped over the
same 16 steps as 4/4 and only the denominator did anything (and what it did
was change tempo, not meter). `steps` / `step_count` are still accepted over
ZMQ and ignored, so an older add-on does not get an error per sequencer.
`AudioSequencerNode` exposes `Grid` (steps per beat) and `Bars` instead of a
fixed step count; `__init__._migrate_sequencer_meters()` converts existing
files on `load_post` via `choose_grid_and_bars()`.

**Polymeter vs polyrhythm (4.16.0).** Meter alone gives POLYMETER: two
sequencers at 7/4 and 4/4 on one BPM share the quarter-note pulse and differ
only in bar length, so with every step filled they play in unison. A
POLYRHYTHM needs the two to divide the SAME span differently. `fit_beats` is
that control - 0 leaves the loop following its meter, above 0 the whole loop
spans that many quarter notes:

```cpp
if (fitBeats > 0.0)
    samplesPerStep_ = samplesPerQuarter * fitBeats / count;   // 7 steps in 4 beats
else
    samplesPerStep_ = samplesPerBeat / stepsPerBeat;          // follows the pulse
```

Note it divides by `count` (the derived step total), not by `stepsPerBeat`:
the span is fixed and the steps share it out, which is the whole point. The
Blender side exposes it as a **Polyrhythm** toggle plus **Fit to beats**, and
prints the reduced ratio so `7 steps across 4 beats` reads as `7:4`.

**The loop length is derived, so steps can be stranded (4.15.1).** Lowering
the numerator or the Grid shrinks the loop. Steps past the new end are KEPT in
the stored pattern - switch the meter back and they play again - but the engine
never reads a step index >= `stepCount_`, so they are silent meanwhile. That is
invisible unless it is reported, and a sequencer whose only hit is stranded
goes completely quiet while looking fine. `AudioSequencerNode.steps_beyond_loop()`
drives a warning row in the step grid, with `fold_steps_into_loop()` (wrap
modulo the loop length) and `clear_steps_beyond_loop()` behind two operators.
Any future change that can shrink the loop must keep that warning honest.

**Transport is edge-triggered and re-triggerable.** A Play edge restarts from
step 0 even while playing (it used to be ignored unless stopped, so a Bang
wired to Play fired exactly once). `Play`/`Stop` links between two ENGINE
nodes are carried by the C++ graph alone -
`shared_memory_reader._update_control_connections` skips them (see the
`engine_wired` flag) so the same event is not also pushed as a parameter a
few milliseconds later, which would restart the sequencer twice.

---

## Extension Guide

### Adding a New Node Type

#### Step 1: C++ Engine Node

Create `engine/src/nodes/MyNewNode.h`:
```cpp
#pragma once
#include "BaseNode.h"

class MyNewNode : public BaseNode {
public:
    explicit MyNewNode(const std::string& id);
    
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;
    void prepare(double sampleRate, int maxBlockSize) override;
    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;
    
private:
    std::atomic<float> myParam_{0.5f};
};
```

Create `engine/src/nodes/MyNewNode.cpp`:
```cpp
#include "MyNewNode.h"

MyNewNode::MyNewNode(const std::string& id) : BaseNode(id) {}

void MyNewNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    // Initialize DSP state
}

void MyNewNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Get input from upstream
    if (inputNode_) inputNode_->process(buffer, numSamples);
    
    // Apply processing
    float param = myParam_.load();
    for (int ch = 0; ch < buffer.getNumChannels(); ++ch) {
        auto* data = buffer.getWritePointer(ch);
        for (int i = 0; i < numSamples; ++i) {
            data[i] *= param;  // Example: simple gain
        }
    }
}

void MyNewNode::setParameter(const std::string& name, float value) {
    if (name == "my_param") myParam_.store(value);
}

float MyNewNode::getParameter(const std::string& name) const {
    if (name == "my_param") return myParam_.load();
    return 0.0f;
}
```

#### Step 2: Register in NodeManager

In `NodeManager.cpp`:
```cpp
#include "nodes/MyNewNode.h"

std::unique_ptr<BaseNode> NodeManager::createNode(const std::string& nodeType, const std::string& nodeId) {
    // ... existing types ...
    else if (nodeType == "my_new") {
        return std::make_unique<MyNewNode>(nodeId);
    }
    return nullptr;
}
```

#### Step 3: Python Blender Node

Create `blender_addon/nodes/my_new_node.py`:
```python
import bpy
from bpy.props import FloatProperty
from .node_base import AudioNodeBase

class AudioMyNewNode(AudioNodeBase):
    bl_idname = "AudioMyNewNode"
    bl_label = "My New Node"
    bl_icon = 'NODE'
    
    my_param: FloatProperty(
        name="My Param",
        default=0.5,
        min=0.0,
        max=1.0,
        update=lambda self, ctx: self.send_param("my_param", self.my_param)
    )
    
    @property
    def node_type(self) -> str:
        return "my_new"  # Must match C++ createNode type
    
    def init(self, context):
        self.inputs.new('AudioSocketType', "Input")
        self.outputs.new('AudioSocketType', "Output")
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {"my_param": self.my_param}
    
    def draw_buttons(self, context, layout):
        layout.prop(self, "my_param")
```

#### Step 4: Register in __init__.py

```python
from .nodes import my_new_node

classes = [
    # ... existing classes ...
    my_new_node.AudioMyNewNode,
]
```

#### Step 5: Add to Menu

In `ui/menus.py`:
```python
class AUDIONODES_MT_add_effects(bpy.types.Menu):
    def draw(self, context):
        layout = self.layout
        # ... existing items ...
        layout.operator("node.add_node", text="My New Node").type = "AudioMyNewNode"
```

---

## ZMQ Command Protocol Reference

### Request Format
All requests are JSON objects with a `type` field.

### Response Format
All responses have a `status` field ("ok" or "error").

### Commands

| Command | Fields | Response |
|---------|--------|----------|
| `ping` | none | `{"status": "ok", "message": "pong"}` |
| `shutdown` | none | `{"status": "ok"}` |
| `create_node` | `node_id`, `node_type`, `params` (optional) | `{"status": "ok"}` |
| `delete_node` | `node_id` | `{"status": "ok"}` |
| `connect` | `source_node`, `dest_node`, `source_port`, `dest_port` | `{"status": "ok"}` |
| `disconnect` | `dest_node`, `dest_port` | `{"status": "ok"}` |
| `set_param` | `node_id`, `param`, `value` | `{"status": "ok"}` |
| `set_pattern` | `node_id`, `lane` (optional, default 0), `pattern` (JSON **integer**) | `{"status": "ok"}` — sequencer step masks are 64-bit and must NOT go through `set_param` (float32 corrupts steps ≥ 25) |
| `get_devices` | none | `{"status": "ok", "data": {"input_devices": [...], "output_devices": [...], "current_input": "...", "current_output": "...", "input_channels": [...], "sample_rate": 44100.0, "buffer_size": 128, "min_buffer_size": 32}}` — `input_channels` lists the ACTIVE input channel names of the current device in callback order: index N is channel N for the input node's `left_channel`/`right_channel` params and for `input_peaks[N]` in shared memory; `sample_rate`/`buffer_size` are what the device actually negotiated |
| `set_audio_settings` | `sample_rate` (optional), `buffer_size` (optional) | `{"status": "ok", "data": {"sample_rate": ..., "buffer_size": ..., "min_buffer_size": ...}}` — becomes the engine's PREFERRED settings, re-applied across device switches; snapped to the nearest value the device supports; applied on the JUCE message thread; response reports the actually-negotiated values |
| `set_device` | `device_type` (`"input"` or `"output"`), `device_name` | `{"status": "ok"}` — engine resets sample rate / buffer size / channel mask before applying so JUCE can re-negotiate values valid for the new device; failure mode is logged to stderr |
| `get_midi_devices` | none | `{"status": "ok", "data": {"input_devices": [...], "output_devices": [...]}}` |
| `set_midi_device` | `device_type`, `device_name` | `{"status": "ok"}` |
| `load_sample` | `node_id`, `file_path` | `{"status": "ok"}` |
| `load_drum_sample` | `node_id`, `slot`, `file_path` | `{"status": "ok"}` |

---

## Shared Memory Reference

### Struct Layout (SharedTypes.h)

```cpp
struct AudioVisualizationData {
    uint32_t sequence;           // Offset 0
    float fft_left[1024];        // Offset 4
    float fft_right[1024];       // Offset 4100
    float peak_left;             // Offset 8196
    float peak_right;            // Offset 8200
    float rms_left;              // Offset 8204
    float rms_right;             // Offset 8208
    float waveform[2048];        // Offset 8212 (interleaved L/R, written from FFT analyzer ring)
    MIDIData midi;               // Offset 16404
    SequencerData sequencer;     // Offset 16936
    float sample_rate;           // Offset 16960 (engine rate in Hz, 0 = unknown)
    uint32_t num_input_channels; // Offset 16964 (active input channel count)
    float input_peaks[32];       // Offset 16968 (per-input-channel peak, max-held between writes)
    uint8_t padding[24];         // Offset 17096
    uint32_t num_fft_analyzers;  // Offset 17120 (used fft_slots)
    uint8_t padding2[28];        // Offset 17124
    FFTAnalyzerSlot fft_slots[4];// Offset 17152 (8240 bytes each, see below)
    uint32_t num_sequencers;     // Offset 50112 (used sequencer_slots)   [4.14]
    uint8_t padding3[28];        // Offset 50116 (aligns the 8-byte slots)
    SequencerSlot sequencer_slots[4]; // Offset 50144 (56 bytes each)
    uint32_t num_control_nodes;  // Offset 50368 (used control_slots)    [4.15]
    uint8_t padding4[28];        // Offset 50372
    ControlSlot control_slots[16]; // Offset 50400 (56 bytes each)
};  // Total: 51296 bytes (every offset pinned by offsetof static_asserts)

// Per-control-source slot: engine nodes whose OUTPUT is a control signal
// with no audio path (Bang, ADSR). Nothing else in shared memory carries
// them, so before 4.15 every Blender-side consumer downstream of a Bang or
// an ADSR - Value Output, Math, NLA Scrub, a driver - resolved to a
// hardcoded 0.0 no matter how often the node fired.
//
// pulse_count is what makes a REPEAT visible: a 20ms Bang pulse can start
// and end between two 60fps reads, so a level alone would show nothing
// happened. The reader compares counts and holds the channel high for one
// frame per pulse (shared_memory_reader._update_control_node_outputs).
struct ControlSlot {
    char node_id[32];            // "" = unused slot
    float values[4];             // Offset 32: control lanes 0..3
    uint32_t pulse_count;        // Offset 48: monotonic discrete fires
    uint32_t flags;              // Offset 52: bit 0 = output currently high
};  // 56 bytes

// Per-sequencer slot: every live Step Sequencer publishes its own transport
// (first 4 in creation order), keyed by engine node id. The legacy
// top-level `sequencer` field mirrors slot 0 (oldest sequencer).
//
// NOTE these were APPENDED in 4.14 so every offset above is unchanged - an
// older reader still finds what it knows and never looks this far. Keep
// that rule for anything added later.
struct SequencerSlot {
    char node_id[32];            // "" = unused slot
    uint8_t current_step;        // Offset 32
    uint8_t is_playing;          // Offset 33
    uint8_t trigger_active;      // Offset 34
    uint8_t padding;
    float bpm;                   // Offset 36
    uint32_t step_count;         // Offset 40
    // 4 bytes implicit padding - uint64_t is 8-byte aligned
    uint64_t step_pattern;       // Offset 48
};  // 56 bytes

// Per-analyzer slot: every live FFT Analyzer node publishes its own
// spectrum (first 4 in creation order), keyed by engine node id. The
// legacy top-level fft/peak/rms fields mirror slot 0 (oldest analyzer).
struct FFTAnalyzerSlot {
    char node_id[32];            // "" = unused slot
    float fft_left[1024];        // Offset 32 within slot
    float fft_right[1024];       // Offset 4128
    float peak_left, peak_right, rms_left, rms_right;  // Offset 8224
};  // 8240 bytes
```

### MIDIData (528 bytes)
```cpp
struct MIDIData {
    uint8_t last_note;           // 0-127
    uint8_t last_velocity;       // 0-127
    uint8_t note_gate;           // 0 or 1
    uint8_t padding1;
    float cc_values[128];        // Normalized 0-1
    float bpm;                   // From MIDI clock
    uint32_t tick_position;      // Song position
    uint8_t transport_playing;   // 0 or 1
    uint8_t padding2[3];
};
```

### SequencerData (24 bytes)
```cpp
struct SequencerData {
    uint8_t current_step;        // Offset 0: 0-63
    uint8_t is_playing;          // Offset 1: 0 or 1
    uint8_t trigger_active;      // Offset 2: 0 or 1
    uint8_t padding;
    float bpm;                   // Offset 4
    uint32_t step_count;         // Offset 8
    // 4 bytes implicit padding: uint64_t is 8-byte aligned
    uint64_t step_pattern;       // Offset 16 (NOT 12!) - bit pattern
};
```

Field offsets are pinned by `static_assert`s in `SharedTypes.h`; the Python
reader (`shared_memory_reader.py`) must read `step_pattern` at
`OFFSET_SEQUENCER + 16`.

### Torn Read Detection (seqlock, v3.1.0)

The writer uses an odd/even seqlock: it stores an **odd** sequence, memcpys
the payload (everything after the sequence field), then stores the next
**even** sequence. Writes are throttled to ~120/sec.

```python
snapshot = read_entire_struct()          # one bulk copy
seq1 = snapshot.sequence
if seq1 & 1:  retry                      # writer was mid-copy
seq2 = read_sequence_again()
if seq1 != seq2:  retry                  # writer ran during our copy
accept(snapshot)                         # ALL fields (incl. MIDI/sequencer) consistent
```

The previous scheme (increment once, single memcpy including the sequence
field) could not actually detect tears; the odd/even protocol can. The
Python reader also skips all per-frame work when the sequence hasn't
changed since the last timer tick.

---

## Debugging Tips

### Engine Debug Output
Add `std::cout` statements in C++ code - they appear in Blender's terminal when run from command line.

### ZMQ Debugging
```python
# In Blender Python console
from audio_nodes import command_client
response = command_client.send_command({"type": "ping"})
print(response)
```

### Shared Memory Debugging
```python
from audio_nodes import shared_memory_reader
data = shared_memory_reader.read_visualization_data()
print(f"Sequence: {data['sequence']}, Peak L: {data['peak_left']}")
```

### Node Registration Issues
Check Blender's System Console for registration errors:
```
Audio Nodes: Registering classes...
  Registered: AudioFilterNode
  FAILED to register AudioBrokenNode: ...
```

