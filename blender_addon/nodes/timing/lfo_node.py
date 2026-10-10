import bpy
from bpy.props import FloatProperty, EnumProperty, BoolProperty
from ..core.node_base import AudioNodeBase
import math


# engine_node_id -> (cycle_index, held_value) for the RANDOM waveform.
# Module level because node instances do not survive between frames.
_RANDOM_STATE = {}


class AudioLFONode(AudioNodeBase):
    """LFO node - Low Frequency Oscillator for modulation.
    
    This is a Blender-only node that generates control signals
    for modulating other node parameters. It runs in Python
    and outputs values through the shared memory system.
    """
    bl_idname = "AudioLFONode"
    bl_label = "LFO"
    bl_icon = 'DRIVER'
    
    rate: FloatProperty(
        name="Rate",
        description="LFO rate in Hz",
        default=1.0,
        min=0.01,
        max=100.0,
        precision=2
    )
    
    depth: FloatProperty(
        name="Depth",
        description="Modulation depth (0-1)",
        default=1.0,
        min=0.0,
        max=1.0
    )
    
    offset: FloatProperty(
        name="Offset",
        description="DC offset added to output (-1 to 1)",
        default=0.0,
        min=-1.0,
        max=1.0
    )
    
    waveform: EnumProperty(
        name="Waveform",
        description="LFO waveform type",
        items=[
            ('SINE', "Sine", "Smooth sine wave", 0),
            ('TRIANGLE', "Triangle", "Triangle wave", 1),
            ('SAW', "Saw Up", "Sawtooth wave (rising)", 2),
            ('SAW_DOWN', "Saw Down", "Sawtooth wave (falling)", 3),
            ('SQUARE', "Square", "Square wave", 4),
            ('RANDOM', "Random", "Sample & hold random", 5),
        ],
        default='SINE'
    )
    
    sync_to_bpm: BoolProperty(
        name="Sync to BPM",
        description="Sync LFO rate to MIDI clock BPM",
        default=False
    )
    
    beat_division: EnumProperty(
        name="Division",
        description="Beat division when synced to BPM",
        items=[
            ('4', "4 Bars", "4 bar cycle", 0),
            ('2', "2 Bars", "2 bar cycle", 1),
            ('1', "1 Bar", "1 bar cycle", 2),
            ('0.5', "1/2", "Half note", 3),
            ('0.25', "1/4", "Quarter note", 4),
            ('0.125', "1/8", "Eighth note", 5),
            ('0.0625', "1/16", "Sixteenth note", 6),
        ],
        default='1'
    )
    
    # Internal state.
    #
    # NOTE: a bpy Node instance is a THROWAWAY Python wrapper - Blender
    # builds a new one on every `tree.nodes` access, so attributes written
    # onto `self` are gone by the next frame. Sample & hold state therefore
    # lives in a module-level dict keyed by engine_node_id.
    
    @property
    def node_type(self) -> str:
        # LFO is Blender-only, no C++ node
        return "lfo"
    
    def init(self, context):
        # Control output - this is what drives other nodes
        self.outputs.new('ControlSocketType', "Output")
        
        # We don't call super().init() because we don't create a C++ node
        # Just generate the ID for tracking
        import uuid
        self.engine_node_id = f"lfo_{uuid.uuid4().hex[:8]}"
    
    def register_with_engine(self):
        """LFO is Blender-only, no C++ registration needed."""
        pass
    
    def unregister_from_engine(self):
        """LFO is Blender-only, no C++ unregistration needed."""
        pass
    
    def get_output_value(self, time: float, bpm: float = 120.0) -> float:
        """Calculate the current LFO output value.
        
        Args:
            time: Current time in seconds
            bpm: Current BPM (used if sync_to_bpm is True)
            
        Returns:
            LFO value in range [-1, 1] * depth + offset
        """
        # Calculate effective rate
        if self.sync_to_bpm:
            # Convert beat division to Hz based on BPM
            beats_per_second = bpm / 60.0
            division = float(self.beat_division)
            rate = beats_per_second / division
        else:
            rate = self.rate
        
        # Calculate phase (0 to 1)
        phase = (time * rate) % 1.0
        
        # Generate waveform
        if self.waveform == 'SINE':
            value = math.sin(phase * 2.0 * math.pi)
        elif self.waveform == 'TRIANGLE':
            if phase < 0.5:
                value = phase * 4.0 - 1.0
            else:
                value = 3.0 - phase * 4.0
        elif self.waveform == 'SAW':
            value = phase * 2.0 - 1.0
        elif self.waveform == 'SAW_DOWN':
            value = 1.0 - phase * 2.0
        elif self.waveform == 'SQUARE':
            value = 1.0 if phase < 0.5 else -1.0
        elif self.waveform == 'RANDOM':
            # Sample & hold: change value only when the cycle index advances.
            current_cycle = int(time * rate)
            held_cycle, held_value = _RANDOM_STATE.get(
                self.engine_node_id, (None, 0.0))
            if held_cycle != current_cycle:
                import random
                held_value = random.uniform(-1.0, 1.0)
                _RANDOM_STATE[self.engine_node_id] = (current_cycle, held_value)
            value = held_value
        else:
            value = 0.0
        
        # Apply depth and offset
        return value * self.depth + self.offset
    
    def get_output_channel_name(self, socket_name: str = "Output") -> str:
        """Return the channel name for this LFO's output."""
        return f"{self.engine_node_id}_output"
    
    def draw_buttons(self, context, layout):
        layout.prop(self, "waveform", text="")
        
        row = layout.row(align=True)
        row.prop(self, "sync_to_bpm", text="", icon='TIME')
        
        if self.sync_to_bpm:
            row.prop(self, "beat_division", text="")
        else:
            row.prop(self, "rate")
        
        layout.prop(self, "depth")
        layout.prop(self, "offset")

