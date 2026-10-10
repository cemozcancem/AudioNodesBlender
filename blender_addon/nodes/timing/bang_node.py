import bpy
from bpy.props import FloatProperty, StringProperty
from ..core.node_base import AudioNodeBase, find_node


class AudioBangNode(AudioNodeBase):
    """Bang node - fire a trigger pulse to many destinations at once."""
    bl_idname = "AudioBangNode"
    bl_label = "Bang"
    bl_icon = 'PLAY'
    bl_width_default = 160

    gate_ms: FloatProperty(
        name="Gate",
        description="How long the trigger stays high (milliseconds)",
        default=20.0,
        min=1.0,
        max=500.0,
        update=lambda self, ctx: self.send_param("gate_ms", self.gate_ms)
    )

    @property
    def node_type(self) -> str:
        return "bang"

    def init(self, context):
        self.inputs.new('ControlSocketType', "Trigger")
        self.outputs.new('ControlSocketType', "Trigger")
        super().init(context)

    def get_initial_params(self) -> dict:
        return {"gate_ms": self.gate_ms}

    def is_firing(self, context) -> bool:
        """Is the pulse high right now? (engine state, via shared memory)"""
        try:
            channel = context.scene.audio_nodes_data.channels.get(
                f"{self.engine_node_id}_trigger")
            return channel is not None and channel.value > 0.5
        except Exception:
            return False

    def draw_buttons(self, context, layout):
        firing = self.is_firing(context)

        row = layout.row(align=True)
        row.scale_y = 1.6
        # `depress` lights the button for the length of the pulse, so a
        # press you can see is a press the engine received.
        op = row.operator("audionodes.bang_fire", text="Fire", icon='PLAY',
                          depress=firing)
        op.node_name = self.name
        op.tree_name = self.id_data.name
        layout.prop(self, "gate_ms", text="Gate (ms)")


class AUDIONODES_OT_bang_fire(bpy.types.Operator):
    """Fire a bang pulse from this node."""
    bl_idname = "audionodes.bang_fire"
    bl_label = "Fire Bang"
    bl_options = {'INTERNAL'}

    node_name: StringProperty()
    tree_name: StringProperty()

    def execute(self, context):
        from ... import engine_manager
        from ... import command_client

        node = find_node(context, self.node_name, bl_idname='AudioBangNode',
                         tree_name=self.tree_name)
        if node is None:
            self.report({'WARNING'}, "Bang node not found")
            return {'CANCELLED'}

        if not engine_manager.is_engine_running():
            self.report({'WARNING'}, "Engine not running")
            return {'CANCELLED'}

        # send_event, not set_parameter: parameter updates are coalesced
        # per (node, param) inside a ~33ms window, so a second press
        # landing in the same window used to overwrite the first and only
        # one pulse ever reached the engine. Each press is its own message.
        command_client.send_event(node.engine_node_id, "fire", 1.0)
        return {'FINISHED'}
