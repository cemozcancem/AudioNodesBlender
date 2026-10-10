import bpy
from bpy.props import IntProperty, FloatProperty, StringProperty
from ..core.node_base import AudioNodeBase


# Common MIDI CC names for reference
COMMON_CC_NAMES = {
    1: "Mod Wheel",
    2: "Breath",
    7: "Volume",
    10: "Pan",
    11: "Expression",
    64: "Sustain",
    65: "Portamento",
    71: "Resonance",
    74: "Cutoff",
    91: "Reverb",
    93: "Chorus",
}


class AudioMIDICCNode(AudioNodeBase):
    """MIDI CC node - outputs a specific MIDI CC value (optionally remapped)."""
    bl_idname = "AudioMIDICCNode"
    bl_label = "MIDI CC"
    bl_icon = 'IPO_BEZIER'
    
    cc_number: IntProperty(
        name="CC Number",
        description="MIDI CC number to monitor (0-127)",
        default=1,
        min=0,
        max=127,
        update=lambda self, ctx: self._update_channel_name()
    )
    
    midi_channel: IntProperty(
        name="Channel",
        description="MIDI channel (1-16, 0 = all)",
        default=0,
        min=0,
        max=16
    )
    
    # Smoothing for the output value
    smoothing: FloatProperty(
        name="Smoothing",
        description="Smooth value changes (0=none, 1=max)",
        default=0.0,
        min=0.0,
        max=0.99
    )

    output_min: FloatProperty(
        name="Min",
        description="Output when CC is at 0. Set this to the target param's "
                    "minimum (e.g. 20 for Hz, 0.1 for Gain). Default 0..1 "
                    "is a normalized control signal.",
        default=0.0,
        soft_min=-10000.0,
        soft_max=10000.0
    )

    output_max: FloatProperty(
        name="Max",
        description="Output when CC is at 127 / full. Set this to the target "
                    "param's maximum (e.g. 20000 for Hz, 50 for Gain).",
        default=1.0,
        soft_min=-10000.0,
        soft_max=10000.0
    )
    
    # Channel name for this CC (raw SHM key; mapped output uses engine_node_id)
    channel_name: StringProperty(
        name="Channel Name",
        default=""
    )
    
    @property
    def node_type(self) -> str:
        # CC node is Blender-only, reads from shared memory
        return "midi_cc"
    
    def _update_channel_name(self):
        """Update the channel name based on CC number."""
        self.channel_name = f"midi_cc_{self.cc_number}"
    
    def init(self, context):
        # Single control output for CC value
        self.outputs.new('ControlSocketType', "Value")
        
        # Don't register with C++ engine - this is Blender-only
        import uuid
        self.engine_node_id = f"midi_cc_{uuid.uuid4().hex[:8]}"
        self._update_channel_name()
    
    def register_with_engine(self):
        # CC node doesn't register with C++ engine
        pass
    
    def unregister_from_engine(self):
        # CC node doesn't register with C++ engine
        pass
    
    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "cc_number")
        
        # Show common CC name if available
        if self.cc_number in COMMON_CC_NAMES:
            row.label(text=f"({COMMON_CC_NAMES[self.cc_number]})")
        
        layout.prop(self, "midi_channel")
        layout.prop(self, "smoothing")

        row = layout.row(align=True)
        row.prop(self, "output_min", text="Min")
        row.prop(self, "output_max", text="Max")
        
        layout.separator()
        
        # Display current value
        try:
            scene = context.scene
            if hasattr(scene, 'audio_nodes_data'):
                audio_data = scene.audio_nodes_data
                
                raw_channel = f"midi_cc_{self.cc_number}"
                mapped_channel = f"{self.engine_node_id}_value"
                if raw_channel in audio_data.channels:
                    raw = audio_data.channels[raw_channel].value
                    mapped = audio_data.channels[mapped_channel].value \
                        if mapped_channel in audio_data.channels else raw
                    
                    box = layout.box()
                    box.label(text=f"CC {self.cc_number}:", icon='IPO_BEZIER')
                    row = box.row()
                    row.prop(audio_data.channels[raw_channel], "value",
                             text="Raw", slider=True)
                    row.label(text=f"→ {mapped:.3f}")
        except Exception:
            pass
    
    def get_output_channel_name(self, output_name: str) -> str:
        """Mapped/smoothed per-node channel (not the global raw midi_cc_N)."""
        if output_name == "Value":
            return f"{self.engine_node_id}_value"
        return ""
