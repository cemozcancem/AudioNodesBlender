import bpy
from bpy.props import FloatProperty, EnumProperty, BoolProperty
from ..core.node_base import AudioNodeBase


class AudioFilterNode(AudioNodeBase):
    """Filter node - state variable TPT filter."""
    bl_idname = "AudioFilterNode"
    bl_label = "Filter"
    bl_icon = 'FILTER'

    # --- In-node graphics (see ui/node_graphics.py) ---------------------
    GRAPH_ROWS = 4

    show_graph: BoolProperty(
        name="Response",
        description="Show the filter response curve in the node",
        default=True
    )
    
    cutoff: FloatProperty(
        name="Cutoff",
        description="Filter cutoff frequency in Hz",
        default=1000.0,
        min=20.0,
        max=20000.0,
        update=lambda self, ctx: self.send_param("cutoff", self.cutoff)
    )
    
    resonance: FloatProperty(
        name="Resonance",
        description="Filter resonance (Q factor)",
        default=0.707,
        min=0.1,
        max=10.0,
        update=lambda self, ctx: self.send_param("resonance", self.resonance)
    )
    
    filter_type: EnumProperty(
        name="Type",
        description="Filter type",
        items=[
            ('LOWPASS', "Low Pass", "Low pass filter", 0),
            ('HIGHPASS', "High Pass", "High pass filter", 1),
            ('BANDPASS', "Band Pass", "Band pass filter", 2),
        ],
        default='LOWPASS',
        update=lambda self, ctx: self.send_param("type", float(self.get_type_index()))
    )
    
    # Control input ranges (for mapping modulation)
    cutoff_mod_depth: FloatProperty(
        name="Cutoff Mod",
        description="How much the control input affects cutoff (in Hz)",
        default=5000.0,
        min=0.0,
        max=19980.0
    )
    
    resonance_mod_depth: FloatProperty(
        name="Res Mod",
        description="How much the control input affects resonance",
        default=5.0,
        min=0.0,
        max=9.9
    )
    
    def get_type_index(self) -> int:
        types = ['LOWPASS', 'HIGHPASS', 'BANDPASS']
        return types.index(self.filter_type) if self.filter_type in types else 0
    
    @property
    def node_type(self) -> str:
        return "filter"
    
    def init(self, context):
        # Audio I/O
        self.inputs.new('AudioSocketType', "Input")
        self.outputs.new('AudioSocketType', "Output")
        
        # Control inputs for modulation
        self.inputs.new('ControlSocketType', "Cutoff")
        self.inputs.new('ControlSocketType', "Resonance")
        
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {
            "cutoff": self.cutoff,
            "resonance": self.resonance,
            "type": float(self.get_type_index())
        }
    
    def get_modulatable_params(self) -> dict:
        """Return params that can be modulated via control inputs."""
        return {
            "Cutoff": {
                "param": "cutoff",
                "base": self.cutoff,
                "min": 20.0,
                "max": 20000.0,
                "mod_depth": self.cutoff_mod_depth
            },
            "Resonance": {
                "param": "resonance", 
                "base": self.resonance,
                "min": 0.1,
                "max": 10.0,
                "mod_depth": self.resonance_mod_depth
            }
        }
    
    def _draw_graph_area(self, layout):
        """Reserve the rows ui/node_graphics.py paints into.

        MUST be the first thing draw_buttons draws: the overlay finds this
        rectangle by assuming it starts directly below the last output
        socket, so anything drawn above it shifts the real rows down while
        the painting stays where it was.
        """
        if self.show_graph:
            col = layout.column(align=True)
            col.scale_y = 1.0
            for _ in range(self.GRAPH_ROWS):
                col.label(text="")
        row = layout.row(align=True)
        row.prop(self, "show_graph", text="", icon='FCURVE')
        return row

    def draw_buttons(self, context, layout):
        self._draw_graph_area(layout)
        layout.prop(self, "filter_type", text="")
        layout.prop(self, "cutoff")
        layout.prop(self, "resonance")
        
        # Show mod depth if control inputs are connected
        cutoff_socket = self.inputs.get("Cutoff")
        res_socket = self.inputs.get("Resonance")
        
        if cutoff_socket and cutoff_socket.is_linked:
            layout.prop(self, "cutoff_mod_depth")
        if res_socket and res_socket.is_linked:
            layout.prop(self, "resonance_mod_depth")
