import bpy
from bpy.props import FloatProperty
from .node_base import AudioNodeBase


class AudioOutputNode(AudioNodeBase):
    """Audio output node - sends audio to the system's active output device.
    
    Note: The output device is selected globally in the Audio Devices panel,
    not per-node. This node sends audio to that device.
    """
    bl_idname = "AudioOutputNode"
    bl_label = "Audio Output"
    bl_icon = 'EXPORT'
    
    gain: FloatProperty(
        name="Gain",
        description="Output gain (0-2)",
        default=1.0,
        min=0.0,
        max=2.0,
        update=lambda self, ctx: self.send_param("gain", self.gain)
    )
    
    # NOTE: there is deliberately no custom `mute` property here.
    # bpy.types.Node already defines `mute` (the M key), and a same-named
    # BoolProperty on a subclass is silently discarded by Blender - the
    # base property wins, so the custom update= never fired and the
    # engine's ramped `mute` param was unreachable. Muting now travels the
    # standard path: shared_memory_reader._sync_mute_states mirrors the
    # node's mute state to the engine, sending `mute` for output nodes
    # (20ms ramp, no click) and `bypass` for everything else.

    @property
    def node_type(self) -> str:
        return "output"
    
    def init(self, context):
        self.inputs.new('AudioSocketType', "Input")
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {
            "gain": self.gain,
            "mute": 1.0 if self.mute else 0.0
        }
    
    def draw_buttons(self, context, layout):
        layout.label(text="Device: See Audio panel", icon='INFO')

        row = layout.row(align=True)
        row.prop(self, "gain")
        row.prop(self, "mute",
                 icon='MUTE_IPO_ON' if self.mute else 'MUTE_IPO_OFF',
                 text="", toggle=True)
