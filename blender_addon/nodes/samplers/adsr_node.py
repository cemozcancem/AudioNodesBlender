import bpy
from bpy.props import FloatProperty, BoolProperty, StringProperty
from ..core.node_base import AudioNodeBase, find_node


class AudioADSRNode(AudioNodeBase):
    """ADSR Envelope node - Attack/Decay/Sustain/Release envelope generator."""
    bl_idname = "AudioADSRNode"
    bl_label = "ADSR"
    bl_icon = 'IPO_EASE_IN_OUT'

    # --- In-node graphics (see ui/node_graphics.py) ---------------------
    GRAPH_ROWS = 4

    show_graph: BoolProperty(
        name="Envelope",
        description="Show the envelope shape in the node",
        default=True
    )
    
    attack: FloatProperty(
        name="Attack",
        description="Attack time in milliseconds",
        default=10.0,
        min=1.0,
        max=5000.0,
        subtype='TIME',
        update=lambda self, ctx: self.send_param("attack", self.attack)
    )
    
    decay: FloatProperty(
        name="Decay",
        description="Decay time in milliseconds",
        default=100.0,
        min=1.0,
        max=5000.0,
        subtype='TIME',
        update=lambda self, ctx: self.send_param("decay", self.decay)
    )
    
    sustain: FloatProperty(
        name="Sustain",
        description="Sustain level (0-100%)",
        default=70.0,
        min=0.0,
        max=100.0,
        subtype='PERCENTAGE',
        update=lambda self, ctx: self.send_param("sustain", self.sustain / 100.0)
    )
    
    release: FloatProperty(
        name="Release",
        description="Release time in milliseconds",
        default=300.0,
        min=1.0,
        max=10000.0,
        subtype='TIME',
        update=lambda self, ctx: self.send_param("release", self.release)
    )
    
    apply_to_audio: BoolProperty(
        name="Apply to Audio",
        description="Apply envelope to audio signal (vs. control output only)",
        default=True,
        update=lambda self, ctx: self.send_param("apply_to_audio", 1.0 if self.apply_to_audio else 0.0)
    )
    
    @property
    def node_type(self) -> str:
        return "adsr"
    
    def init(self, context):
        # Gate input (from sequencer, MIDI, etc.)
        self.inputs.new('ControlSocketType', "Gate")
        
        # Audio input/output
        self.inputs.new('AudioSocketType', "Audio In")
        self.outputs.new('AudioSocketType', "Audio Out")
        
        # Envelope value output (for driving other parameters)
        self.outputs.new('ControlSocketType', "Envelope")
        
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {
            "attack": self.attack,
            "decay": self.decay,
            "sustain": self.sustain / 100.0,
            "release": self.release,
            "apply_to_audio": 1.0 if self.apply_to_audio else 0.0,
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
        # ADSR sliders
        col = layout.column(align=True)
        col.prop(self, "attack")
        col.prop(self, "decay")
        col.prop(self, "sustain")
        col.prop(self, "release")
        
        layout.separator()
        layout.prop(self, "apply_to_audio")
        
        # Manual trigger
        layout.separator()
        row = layout.row(align=True)
        op = row.operator("audionodes.trigger_adsr", text="Trigger", icon='PLAY')
        op.node_name = self.name
        op.tree_name = self.id_data.name
        op = row.operator("audionodes.release_adsr", text="Release", icon='PAUSE')
        op.node_name = self.name
        op.tree_name = self.id_data.name


class AUDIONODES_OT_trigger_adsr(bpy.types.Operator):
    bl_idname = "audionodes.trigger_adsr"
    bl_label = "Trigger ADSR"
    bl_options = {'INTERNAL'}
    
    node_name: StringProperty()
    tree_name: StringProperty()
    
    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioADSRNode',
                         tree_name=self.tree_name)
        if node:
            node.send_event("gate", 1.0)
        return {'FINISHED'}
    


class AUDIONODES_OT_release_adsr(bpy.types.Operator):
    bl_idname = "audionodes.release_adsr"
    bl_label = "Release ADSR"
    bl_options = {'INTERNAL'}
    
    node_name: StringProperty()
    tree_name: StringProperty()
    
    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioADSRNode',
                         tree_name=self.tree_name)
        if node:
            node.send_event("gate", 0.0)
        return {'FINISHED'}
    

