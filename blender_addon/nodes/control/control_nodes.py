"""Control nodes - Trigger, Accumulator and Decay.

Blender-only nodes (no C++ counterpart) that turn raw audio control
signals into the building blocks of an edit:

  Trigger     - "a hit happened": edge detection with hysteresis,
                cooldown and a probability gate
  Accumulator - "how far have we got": integrates a rate over time
                (a phase that runs faster when the music is louder)
  Decay       - "flash, then fade": jumps on a trigger or follows a
                signal, then falls off exponentially

They are evaluated every visualization tick by update_control_nodes()
(called from shared_memory_reader), in dependency order, and publish
each output socket as a channel `<engine_node_id>_<socket>` - so they
can feed Math nodes, engine parameters, Value Output, NLA Scrub, the
Camera Cutter, or any driver via audio_channel("...").
"""

import time
import uuid

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty, StringProperty

from ..core.node_base import AudioNodeBase
from . import control_logic


# engine_node_id -> logic instance. Module level: node instances are
# recreated by Blender all the time and must not carry runtime state.
_STATE = {}

CONTROL_NODE_IDNAMES = (
    'AudioTriggerNode', 'AudioAccumulatorNode', 'AudioDecayNode',
    'AudioCameraCutterNode',
)


def publish(audio_data, name, value):
    channels = audio_data.channels
    channel = channels.get(name)
    if channel is None:
        channel = channels.add()
        channel.name = name
    if channel.value != value:
        channel.value = value


def read_channel(context, name, default=0.0):
    try:
        channel = context.scene.audio_nodes_data.channels.get(name)
        if channel is not None:
            return channel.value
    except Exception:
        pass
    return default


class ControlNodeBase(AudioNodeBase):
    """Shared plumbing for Blender-only control nodes."""

    bl_idname = "AudioControlNodeBase"
    bl_label = "Control Node"

    # Blender-only: the engine never hears about these nodes.
    def register_with_engine(self):
        pass

    def unregister_from_engine(self):
        pass

    def init(self, context):
        self.build_sockets()
        self.engine_node_id = f"{self.node_type}_{uuid.uuid4().hex[:8]}"

    def copy(self, node):
        self.engine_node_id = f"{self.node_type}_{uuid.uuid4().hex[:8]}"

    def free(self):
        _STATE.pop(self.engine_node_id, None)

    def build_sockets(self):
        raise NotImplementedError

    def get_output_channel_name(self, socket_name: str = "") -> str:
        if not socket_name:
            socket_name = self.outputs[0].name if len(self.outputs) else "value"
        return f"{self.engine_node_id}_{socket_name.lower().replace(' ', '_')}"

    def state(self, factory):
        st = _STATE.get(self.engine_node_id)
        if st is None:
            st = factory()
            _STATE[self.engine_node_id] = st
        return st

    def draw_level(self, context, layout, socket_name, lo=0.0, hi=1.0, fmt="{:.2f}"):
        value = read_channel(context, self.get_output_channel_name(socket_name))
        span = hi - lo
        factor = 0.0 if abs(span) < 1e-9 else max(0.0, min(1.0, (value - lo) / span))
        row = layout.row()
        row.scale_y = 0.7
        if hasattr(row, "progress"):
            row.progress(factor=factor, type='BAR', text=f"{socket_name}: " + fmt.format(value))
        else:
            row.label(text=f"{socket_name}: " + fmt.format(value))

    def draw_buttons_ext(self, context, layout):
        self.draw_buttons(context, layout)
        box = layout.box()
        box.label(text="Use in drivers", icon='DRIVER')
        for socket in self.outputs:
            row = box.row(align=True)
            row.label(text=socket.name)
            op = row.operator("audionodes.copy_channel_driver", text="", icon='COPYDOWN')
            op.channel = self.get_output_channel_name(socket.name)


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------

class AudioTriggerNode(ControlNodeBase):
    """Turn a level into clean hits: threshold + hysteresis + cooldown."""

    bl_idname = "AudioTriggerNode"
    bl_label = "Trigger"
    bl_icon = 'PLAY'
    bl_width_default = 190

    threshold: FloatProperty(
        name="Threshold", default=0.6, min=0.0, soft_max=1.0,
        description="Fire when the signal rises above this")
    rearm: FloatProperty(
        name="Re-arm", default=0.3, min=0.0, soft_max=1.0,
        description="The signal must fall below this before the next hit "
                    "can fire (hysteresis - stops one ringing kick firing "
                    "several times)")
    cooldown: FloatProperty(
        name="Cooldown", default=0.1, min=0.0, soft_max=10.0, subtype='TIME_ABSOLUTE',
        description="Minimum seconds between two hits")
    probability: FloatProperty(
        name="Probability", default=1.0, min=0.0, max=1.0, subtype='FACTOR',
        description="Chance that a detected hit passes through")
    pulse_length: FloatProperty(
        name="Pulse", default=0.05, min=0.0, soft_max=1.0, subtype='TIME_ABSOLUTE',
        description="How long the Trigger output stays at 1 after a hit")

    @property
    def node_type(self) -> str:
        return "trigger"

    def build_sockets(self):
        self.inputs.new('ControlSocketType', "Signal")
        self.outputs.new('ControlSocketType', "Trigger")
        self.outputs.new('ControlSocketType', "Gate")
        self.outputs.new('ControlSocketType', "Count")

    def draw_buttons(self, context, layout):
        col = layout.column(align=True)
        col.prop(self, "threshold")
        col.prop(self, "rearm")
        col = layout.column(align=True)
        col.prop(self, "cooldown")
        col.prop(self, "probability")
        col.prop(self, "pulse_length")
        self.draw_level(context, layout, "Trigger")

    def evaluate(self, audio_data, resolve, dt):
        logic = self.state(control_logic.TriggerLogic)
        pulse, gate, count = logic.step(
            resolve(self, "Signal", 0.0), dt, self.threshold, self.rearm,
            self.cooldown, self.probability, self.pulse_length)
        publish(audio_data, self.get_output_channel_name("Trigger"), pulse)
        publish(audio_data, self.get_output_channel_name("Gate"), gate)
        publish(audio_data, self.get_output_channel_name("Count"), count)


# ---------------------------------------------------------------------------
# Accumulator
# ---------------------------------------------------------------------------

class AudioAccumulatorNode(ControlNodeBase):
    """Integrate a rate over time - a phase that speeds up with the music."""

    bl_idname = "AudioAccumulatorNode"
    bl_label = "Accumulator"
    bl_icon = 'FORCE_VORTEX'
    bl_width_default = 190

    mode: EnumProperty(
        name="Mode",
        items=[
            ('WRAP', "Wrap", "Wrap around between Low and High (a looping phase)"),
            ('PINGPONG', "Ping-Pong", "Bounce back and forth between Low and High"),
            ('CLAMP', "Clamp", "Stop at Low / High"),
            ('NONE', "Unbounded", "Keep counting forever"),
        ],
        default='WRAP')
    base_rate: FloatProperty(
        name="Base Rate", default=0.0, soft_min=-10.0, soft_max=10.0,
        description="Units added per second even when the Rate input is 0")
    gain: FloatProperty(
        name="Gain", default=1.0, soft_min=-10.0, soft_max=10.0,
        description="Rate input is multiplied by this (units per second)")
    low: FloatProperty(name="Low", default=0.0)
    high: FloatProperty(name="High", default=1.0)
    reset_value: FloatProperty(
        name="Reset To", default=0.0,
        description="Value a rising edge on Reset jumps back to")

    @property
    def node_type(self) -> str:
        return "accumulator"

    def build_sockets(self):
        self.inputs.new('ControlSocketType', "Rate")
        self.inputs.new('ControlSocketType', "Reset")
        self.outputs.new('ControlSocketType', "Value")
        self.outputs.new('ControlSocketType', "Wrap")

    def draw_buttons(self, context, layout):
        layout.prop(self, "mode", text="")
        col = layout.column(align=True)
        col.prop(self, "base_rate")
        col.prop(self, "gain")
        if self.mode != 'NONE':
            row = layout.row(align=True)
            row.prop(self, "low")
            row.prop(self, "high")
        layout.prop(self, "reset_value")
        if self.mode == 'NONE':
            self.draw_level(context, layout, "Value", 0.0, 0.0)
        else:
            self.draw_level(context, layout, "Value", self.low, self.high)

    def evaluate(self, audio_data, resolve, dt):
        logic = self.state(control_logic.AccumulatorLogic)
        value, wrap = logic.step(
            resolve(self, "Rate", 0.0), dt, self.base_rate, self.gain,
            self.mode, self.low, self.high, resolve(self, "Reset", 0.0),
            self.reset_value)
        publish(audio_data, self.get_output_channel_name("Value"), value)
        publish(audio_data, self.get_output_channel_name("Wrap"), wrap)


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------

class AudioDecayNode(ControlNodeBase):
    """Flash-and-fade envelope, or a peak follower with a slow release."""

    bl_idname = "AudioDecayNode"
    bl_label = "Decay"
    bl_icon = 'IPO_EXPO'
    bl_width_default = 180

    peak: FloatProperty(
        name="Peak", default=1.0, soft_min=0.0, soft_max=2.0,
        description="Level a Trigger hit jumps to")
    decay_time: FloatProperty(
        name="Decay", default=0.3, min=0.0, soft_max=5.0,
        subtype='TIME_ABSOLUTE',
        description="Seconds to fall to 10 %")

    @property
    def node_type(self) -> str:
        return "decay"

    def build_sockets(self):
        self.inputs.new('ControlSocketType', "Trigger")
        self.inputs.new('ControlSocketType', "Signal")
        self.outputs.new('ControlSocketType', "Value")

    def draw_buttons(self, context, layout):
        col = layout.column(align=True)
        col.prop(self, "peak")
        col.prop(self, "decay_time")
        self.draw_level(context, layout, "Value", 0.0, max(self.peak, 1e-3))

    def evaluate(self, audio_data, resolve, dt):
        logic = self.state(control_logic.DecayLogic)
        value = logic.step(dt, resolve(self, "Trigger", 0.0),
                           resolve(self, "Signal", 0.0), self.peak,
                           self.decay_time)
        publish(audio_data, self.get_output_channel_name("Value"), value)


# ---------------------------------------------------------------------------
# Driver helper
# ---------------------------------------------------------------------------

class AUDIONODES_OT_copy_channel_driver(bpy.types.Operator):
    """Copy an audio_channel() driver expression for this output"""

    bl_idname = "audionodes.copy_channel_driver"
    bl_label = "Copy Driver Expression"
    bl_options = {'INTERNAL'}

    channel: StringProperty()

    def execute(self, context):
        expression = f'audio_channel("{self.channel}")'
        context.window_manager.clipboard = expression
        self.report({'INFO'}, f"Copied: {expression} - right-click any "
                              f"property > Add Driver > paste as expression")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Per-tick evaluation
# ---------------------------------------------------------------------------

_last_tick = None


def _ordered(tree):
    """Control nodes of `tree` in dependency order (cycles dropped last)."""
    nodes = [n for n in tree.nodes if n.bl_idname in CONTROL_NODE_IDNAMES]
    ids = {n.as_pointer(): n for n in nodes}
    deps = {}
    for n in nodes:
        d = set()
        for socket in n.inputs:
            if socket.is_linked:
                src = socket.links[0].from_node
                if src.as_pointer() in ids:
                    d.add(src.as_pointer())
        deps[n.as_pointer()] = d
    ordered, done = [], set()
    pending = list(nodes)
    while pending:
        progressed = False
        for n in list(pending):
            if deps[n.as_pointer()] <= done:
                ordered.append(n)
                done.add(n.as_pointer())
                pending.remove(n)
                progressed = True
        if not progressed:        # feedback loop: evaluate the rest anyway,
            ordered.extend(pending)  # they read last tick's values
            break
    return ordered


def update_control_nodes(audio_data, resolve_input):
    """Evaluate every control node once. Called ~60x/s by the reader."""
    global _last_tick
    now = time.monotonic()
    dt = 0.0 if _last_tick is None else min(0.25, max(0.0, now - _last_tick))
    _last_tick = now

    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in _ordered(tree):
            if node.mute:
                continue
            try:
                node.evaluate(audio_data, resolve_input, dt)
            except Exception as exc:  # never break the 60 fps timer
                _report(node, exc)


_reported = set()


def _report(node, exc):
    key = (node.engine_node_id, type(exc).__name__)
    if key not in _reported:
        _reported.add(key)
        print(f"Audio Nodes: {node.bl_label} '{node.name}' failed: {exc!r}")
