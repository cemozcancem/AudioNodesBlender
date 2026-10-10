import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty
from .node_base import AudioNodeBase


# Blender's dynamic EnumProperty items callback does NOT keep the returned
# strings alive - they must stay referenced from Python or the UI shows
# garbage. This module-level list holds the last generated items.
_channel_items_ref = []


def _device_channel_names():
    """Active input channel names from the device cache (may be [])."""
    try:
        from ...ui.panels import get_cached_input_channels
        return get_cached_input_channels()
    except Exception:
        return []


def _make_channel_items(self, context):
    """Enum items for channel dropdowns: real device channel names when the
    engine has reported them, generic entries otherwise.

    Identifier is the channel index as a string; item order == channel
    index, so the enum's get/set can proxy the persisted IntProperties
    directly.
    """
    global _channel_items_ref

    names = _device_channel_names()

    # Always cover the currently saved selection, even if the cached
    # device has fewer channels (device unplugged / engine offline), so
    # saved .blend files never show a broken enum.
    needed = max(self.left_channel, self.right_channel) + 1
    count = max(len(names), needed, 2)

    items = []
    for i in range(count):
        if i < len(names):
            label = f"{i + 1}: {names[i]}"
        else:
            label = f"{i + 1}: Channel {i + 1}"
        items.append((str(i), label, f"Device input channel {i + 1}"))

    _channel_items_ref = items
    return items


class AudioInputNode(AudioNodeBase):
    """Audio input node - maps device input channels into the graph.

    The input device is selected globally in the Audio Devices panel; each
    Audio Input node picks which of that device's channels it outputs.
    Drop several Audio Input nodes to route different channels of a
    multi-channel interface (e.g. an 18-in USB mixer) to different chains.
    """
    bl_idname = "AudioInputNode"
    bl_label = "Audio Input"
    bl_icon = 'IMPORT'
    bl_width_default = 200

    gain: FloatProperty(
        name="Gain",
        description="Input gain (0-2)",
        default=1.0,
        min=0.0,
        max=2.0,
        update=lambda self, ctx: self.send_param("gain", self.gain)
    )

    # Persisted channel selection (kept as ints for .blend backward
    # compatibility - the enum dropdowns below proxy these).
    left_channel: IntProperty(
        name="Left Channel",
        description="Input channel for left output",
        default=0,
        min=0,
        max=31,
        update=lambda self, ctx: self.send_param("left_channel", float(self.left_channel))
    )

    right_channel: IntProperty(
        name="Right Channel",
        description="Input channel for right output",
        default=1,
        min=0,
        max=31,
        update=lambda self, ctx: self.send_param("right_channel", float(self.right_channel))
    )

    channel_mode: EnumProperty(
        name="Mode",
        description="Mono: one device channel to both outputs. "
                    "Stereo: independent left/right channel picks",
        items=[
            ('MONO', "Mono", "One device channel feeds both L and R"),
            ('STEREO', "Stereo", "Separate device channels for L and R"),
        ],
        default='STEREO',
        update=lambda self, ctx: self.send_param(
            "channel_mode", 1.0 if self.channel_mode == 'MONO' else 0.0)
    )

    # UI dropdowns showing real device channel names; item index == channel
    # number, so get/set proxy the persisted ints directly.
    left_channel_sel: EnumProperty(
        name="Channel",
        description="Device input channel (left output in Stereo mode)",
        items=_make_channel_items,
        get=lambda self: min(self.left_channel,
                             len(_make_channel_items(self, None)) - 1),
        set=lambda self, value: setattr(self, "left_channel", value),
    )

    right_channel_sel: EnumProperty(
        name="Channel R",
        description="Device input channel for the right output",
        items=_make_channel_items,
        get=lambda self: min(self.right_channel,
                             len(_make_channel_items(self, None)) - 1),
        set=lambda self, value: setattr(self, "right_channel", value),
    )

    @property
    def node_type(self) -> str:
        return "input"

    def init(self, context):
        self.outputs.new('AudioSocketType', "Output")
        super().init(context)

    def get_initial_params(self) -> dict:
        return {
            "gain": self.gain,
            "left_channel": float(self.left_channel),
            "right_channel": float(self.right_channel),
            "channel_mode": 1.0 if self.channel_mode == 'MONO' else 0.0,
        }

    def _draw_channel_meter(self, context, layout, channel: int, text: str):
        """Tiny live peak meter for a selected device channel."""
        audio_data = getattr(context.scene, 'audio_nodes_data', None)
        if audio_data is None:
            return
        chan = audio_data.channels.get(f"input_peak_{channel}")
        if chan is None:
            return
        layout.progress(factor=min(chan.value, 1.0), text=text)

    def draw_buttons(self, context, layout):
        row = layout.row()
        row.prop(self, "channel_mode", expand=True)

        if self.channel_mode == 'MONO':
            layout.prop(self, "left_channel_sel", text="Ch")
            self._draw_channel_meter(context, layout, self.left_channel, "In")
        else:
            layout.prop(self, "left_channel_sel", text="L")
            layout.prop(self, "right_channel_sel", text="R")
            row = layout.row(align=True)
            self._draw_channel_meter(context, row, self.left_channel, "L")
            self._draw_channel_meter(context, row, self.right_channel, "R")

        layout.prop(self, "gain")

        if not _device_channel_names():
            layout.label(text="Refresh devices for channel names", icon='INFO')

    def draw_buttons_ext(self, context, layout):
        self.draw_buttons(context, layout)
        layout.separator()
        layout.label(text="Device: see Audio Devices panel", icon='INFO')
