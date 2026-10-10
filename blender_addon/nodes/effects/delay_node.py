import bpy
from bpy.props import FloatProperty, BoolProperty
from ..core.node_base import AudioNodeBase


class AudioDelayNode(AudioNodeBase):
    """Delay node - time-based delay effect."""
    bl_idname = "AudioDelayNode"
    bl_label = "Delay"
    bl_icon = 'TIME'

    # --- In-node graphics (see ui/node_graphics.py) ---------------------
    GRAPH_ROWS = 3

    show_graph: BoolProperty(
        name="Repeats",
        description="Show the delay repeats in the node",
        default=True
    )
    
    delay_time: FloatProperty(
        name="Time (ms)",
        description="Delay time in milliseconds",
        default=250.0,
        min=1.0,
        max=2000.0,
        update=lambda self, ctx: self.send_param("time", self.delay_time)
    )
    
    feedback: FloatProperty(
        name="Feedback",
        description="Feedback amount (0-0.95)",
        default=0.3,
        min=0.0,
        max=0.95,
        update=lambda self, ctx: self.send_param("feedback", self.feedback)
    )
    
    mix: FloatProperty(
        name="Mix",
        description="Dry/Wet mix (0=dry, 1=wet)",
        default=0.5,
        min=0.0,
        max=1.0,
        update=lambda self, ctx: self.send_param("mix", self.mix)
    )
    
    # Modulation depths
    time_mod_depth: FloatProperty(
        name="Time Mod",
        description="How much the control input affects delay time (in ms)",
        default=500.0,
        min=0.0,
        max=1999.0
    )
    
    feedback_mod_depth: FloatProperty(
        name="FB Mod",
        description="How much the control input affects feedback",
        default=0.5,
        min=0.0,
        max=0.95
    )
    
    mix_mod_depth: FloatProperty(
        name="Mix Mod",
        description="How much the control input affects mix",
        default=0.5,
        min=0.0,
        max=1.0
    )
    
    @property
    def node_type(self) -> str:
        return "delay"
    
    def init(self, context):
        # Audio I/O
        self.inputs.new('AudioSocketType', "Input")
        self.outputs.new('AudioSocketType', "Output")
        
        # Control inputs for modulation
        self.inputs.new('ControlSocketType', "Time")
        self.inputs.new('ControlSocketType', "Feedback")
        self.inputs.new('ControlSocketType', "Mix")
        
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {
            "time": self.delay_time,
            "feedback": self.feedback,
            "mix": self.mix
        }
    
    def get_modulatable_params(self) -> dict:
        """Return params that can be modulated via control inputs."""
        return {
            "Time": {
                "param": "time",
                "base": self.delay_time,
                "min": 1.0,
                "max": 2000.0,
                "mod_depth": self.time_mod_depth
            },
            "Feedback": {
                "param": "feedback",
                "base": self.feedback,
                "min": 0.0,
                "max": 0.95,
                "mod_depth": self.feedback_mod_depth
            },
            "Mix": {
                "param": "mix",
                "base": self.mix,
                "min": 0.0,
                "max": 1.0,
                "mod_depth": self.mix_mod_depth
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
        layout.prop(self, "delay_time")
        layout.prop(self, "feedback")
        layout.prop(self, "mix")
        
        # Show mod depths for connected control inputs
        time_socket = self.inputs.get("Time")
        fb_socket = self.inputs.get("Feedback")
        mix_socket = self.inputs.get("Mix")
        
        if time_socket and time_socket.is_linked:
            layout.prop(self, "time_mod_depth")
        if fb_socket and fb_socket.is_linked:
            layout.prop(self, "feedback_mod_depth")
        if mix_socket and mix_socket.is_linked:
            layout.prop(self, "mix_mod_depth")
