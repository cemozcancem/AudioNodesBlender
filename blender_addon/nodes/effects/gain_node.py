import bpy
from bpy.props import FloatProperty, BoolProperty
from ..core.node_base import AudioNodeBase


class AudioGainNode(AudioNodeBase):
    """Gain node - volume control with dB."""
    bl_idname = "AudioGainNode"
    bl_label = "Gain"
    bl_icon = 'SOUND'
    
    gain_db: FloatProperty(
        name="Gain (dB)",
        description="Gain in decibels",
        default=0.0,
        min=-60.0,
        max=24.0,
        update=lambda self, ctx: self.send_param("gain_db", self.gain_db)
    )
    
    mute: BoolProperty(
        name="Mute",
        description="Mute output",
        default=False,
        update=lambda self, ctx: self.send_param("mute", 1.0 if self.mute else 0.0)
    )
    
    # Modulation depth
    gain_mod_depth: FloatProperty(
        name="Gain Mod",
        description="How much the control input affects gain (in dB)",
        default=24.0,
        min=0.0,
        max=84.0
    )
    
    @property
    def node_type(self) -> str:
        return "gain"
    
    def init(self, context):
        # Audio I/O
        self.inputs.new('AudioSocketType', "Input")
        self.outputs.new('AudioSocketType', "Output")
        
        # Control input for gain modulation
        self.inputs.new('ControlSocketType', "Gain")
        
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {
            "gain_db": self.gain_db,
            "mute": 1.0 if self.mute else 0.0
        }
    
    def get_modulatable_params(self) -> dict:
        """Return params that can be modulated via control inputs."""
        return {
            "Gain": {
                "param": "gain_db",
                "base": self.gain_db,
                "min": -60.0,
                "max": 24.0,
                "mod_depth": self.gain_mod_depth
            }
        }
    
    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gain_db")
        row.prop(self, "mute", icon='MUTE_IPO_ON' if self.mute else 'MUTE_IPO_OFF', text="")
        
        # Show mod depth if control input is connected
        gain_socket = self.inputs.get("Gain")
        if gain_socket and gain_socket.is_linked:
            layout.prop(self, "gain_mod_depth")
