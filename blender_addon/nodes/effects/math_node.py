import bpy
from bpy.props import EnumProperty, FloatProperty
from ..core.node_base import AudioNodeBase


class AudioMathNode(AudioNodeBase):
    """Adaptive math node for signal and control operations."""
    bl_idname = "AudioMathNode"
    bl_label = "Math"
    bl_icon = 'DRIVER'

    operation: EnumProperty(
        name="Operation",
        items=[
            ('ADD', "Add", ""),
            ('SUB', "Subtract", ""),
            ('MUL', "Multiply", ""),
            ('DIV', "Divide", ""),
            ('MIN', "Min", ""),
            ('MAX', "Max", ""),
            ('ABS', "Abs(A)", ""),
            ('CLAMP', "Clamp(A)", ""),
        ],
        default='ADD',
        update=lambda self, ctx: self.send_param("operation", float(self._operation_index()))
    )

    input_a: FloatProperty(
        name="A",
        default=0.0,
        update=lambda self, ctx: self.send_param("control_a", self.input_a)
    )

    input_b: FloatProperty(
        name="B",
        default=0.0,
        update=lambda self, ctx: self.send_param("control_b", self.input_b)
    )

    clamp_min: FloatProperty(
        name="Clamp Min",
        default=0.0,
        update=lambda self, ctx: self.send_param("clamp_min", self.clamp_min)
    )

    clamp_max: FloatProperty(
        name="Clamp Max",
        default=1.0,
        update=lambda self, ctx: self.send_param("clamp_max", self.clamp_max)
    )

    @property
    def node_type(self) -> str:
        return "math"

    def _operation_index(self) -> int:
        ops = ['ADD', 'SUB', 'MUL', 'DIV', 'MIN', 'MAX', 'ABS', 'CLAMP']
        return ops.index(self.operation) if self.operation in ops else 0

    def init(self, context):
        self.inputs.new('AudioSocketType', "Audio A")
        self.inputs.new('AudioSocketType', "Audio B")
        self.inputs.new('ControlSocketType', "A")
        self.inputs.new('ControlSocketType', "B")
        self.outputs.new('AudioSocketType', "Audio Out")
        self.outputs.new('ControlSocketType', "Value")
        super().init(context)

    def get_initial_params(self) -> dict:
        return {
            "operation": float(self._operation_index()),
            "control_a": self.input_a,
            "control_b": self.input_b,
            "clamp_min": self.clamp_min,
            "clamp_max": self.clamp_max,
        }

    def get_control_input_param_map(self) -> dict:
        return {
            "A": "control_a",
            "B": "control_b",
        }

    def get_channel_name(self) -> str:
        return f"{self.engine_node_id}_value"

    def get_output_channel_name(self, output_name: str) -> str:
        if output_name == "Value":
            return self.get_channel_name()
        return ""

    def draw_buttons(self, context, layout):
        from ... import shared_memory_reader
        error = shared_memory_reader._control_errors.get(self.engine_node_id)
        if error:
            row = layout.row()
            row.alert = True
            row.label(text=error, icon='ERROR')
        layout.prop(self, "operation", text="")
        row = layout.row(align=True)
        row.prop(self, "input_a")
        row.prop(self, "input_b")
        if self.operation == 'CLAMP':
            row = layout.row(align=True)
            row.prop(self, "clamp_min")
            row.prop(self, "clamp_max")


