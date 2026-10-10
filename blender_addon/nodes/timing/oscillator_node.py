import bpy
from bpy.props import FloatProperty, EnumProperty
from ..core.node_base import AudioNodeBase


class AudioOscillatorNode(AudioNodeBase):
    """Oscillator node - generates waveforms."""
    bl_idname = "AudioOscillatorNode"
    bl_label = "Oscillator"
    bl_icon = 'FORCE_HARMONIC'
    
    frequency: FloatProperty(
        name="Frequency",
        description="Oscillator frequency in Hz",
        default=440.0,
        min=20.0,
        max=20000.0,
        update=lambda self, ctx: self.send_param("frequency", self.frequency)
    )
    
    gain: FloatProperty(
        name="Gain",
        description="Output level (0-1)",
        default=0.5,
        min=0.0,
        max=1.0,
        update=lambda self, ctx: self.send_param("gain", self.gain)
    )
    
    waveform: EnumProperty(
        name="Waveform",
        description="Oscillator waveform type",
        items=[
            ('SINE', "Sine", "Sine wave", 0),
            ('SAW', "Saw", "Sawtooth wave", 1),
            ('SQUARE', "Square", "Square wave", 2),
            ('TRIANGLE', "Triangle", "Triangle wave", 3),
        ],
        default='SINE',
        update=lambda self, ctx: self.send_param("waveform", float(self.get_waveform_index()))
    )
    
    # Modulation depths
    freq_mod_depth: FloatProperty(
        name="Freq Mod",
        description="How much the control input affects frequency (in Hz)",
        default=1000.0,
        min=0.0,
        max=19980.0
    )
    
    gain_mod_depth: FloatProperty(
        name="Gain Mod",
        description="How much the control input affects gain",
        default=0.5,
        min=0.0,
        max=1.0
    )
    
    def get_waveform_index(self) -> int:
        waveforms = ['SINE', 'SAW', 'SQUARE', 'TRIANGLE']
        return waveforms.index(self.waveform) if self.waveform in waveforms else 0
    
    @property
    def node_type(self) -> str:
        return "oscillator"
    
    def init(self, context):
        # Audio output
        self.outputs.new('AudioSocketType', "Output")
        
        # Control inputs for modulation
        self.inputs.new('ControlSocketType', "Frequency")
        self.inputs.new('ControlSocketType', "Gain")
        
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {
            "frequency": self.frequency,
            "gain": self.gain,
            "waveform": float(self.get_waveform_index())
        }
    
    def get_modulatable_params(self) -> dict:
        """Return params that can be modulated via control inputs."""
        return {
            "Frequency": {
                "param": "frequency",
                "base": self.frequency,
                "min": 20.0,
                "max": 20000.0,
                "mod_depth": self.freq_mod_depth
            },
            "Gain": {
                "param": "gain",
                "base": self.gain,
                "min": 0.0,
                "max": 1.0,
                "mod_depth": self.gain_mod_depth
            }
        }
    
    def draw_buttons(self, context, layout):
        layout.prop(self, "waveform", text="")
        layout.prop(self, "frequency")
        layout.prop(self, "gain")
        
        # Show mod depths for connected control inputs
        freq_socket = self.inputs.get("Frequency")
        gain_socket = self.inputs.get("Gain")
        
        if freq_socket and freq_socket.is_linked:
            layout.prop(self, "freq_mod_depth")
        if gain_socket and gain_socket.is_linked:
            layout.prop(self, "gain_mod_depth")
