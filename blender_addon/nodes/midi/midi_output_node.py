import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty
from ..core.node_base import AudioNodeBase, find_node


def get_midi_output_devices(self, context):
    """Get available MIDI output devices from the engine."""
    from ... import command_client
    from ... import engine_manager
    
    items = [('NONE', "None", "No MIDI output device")]
    
    if engine_manager.is_engine_running():
        devices = command_client.get_midi_devices()
        if devices and 'output_devices' in devices:
            for device in devices['output_devices']:
                items.append((device, device, device))
    
    return items


class AudioMIDIOutputNode(AudioNodeBase):
    """MIDI Output node - sends MIDI to external devices."""
    bl_idname = "AudioMIDIOutputNode"
    bl_label = "MIDI Output"
    bl_icon = 'EXPORT'
    
    midi_device: EnumProperty(
        name="Device",
        description="MIDI output device to use",
        items=get_midi_output_devices,
        update=lambda self, ctx: self._on_device_changed()
    )
    
    midi_channel: IntProperty(
        name="Channel",
        description="MIDI channel to send on (1-16)",
        default=1,
        min=1,
        max=16,
        update=lambda self, ctx: self.send_param("channel", float(self.midi_channel))
    )
    
    @property
    def node_type(self) -> str:
        return "midi_output"
    
    def _on_device_changed(self):
        """Handle MIDI device selection change."""
        from ... import command_client
        if self.midi_device and self.midi_device != 'NONE':
            command_client.set_midi_device('output', self.midi_device)
    
    def init(self, context):
        # Control inputs for MIDI data
        self.inputs.new('ControlSocketType', "Note")
        self.inputs.new('ControlSocketType', "Velocity")
        self.inputs.new('ControlSocketType', "Gate")
        
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {
            "channel": float(self.midi_channel)
        }
    
    def draw_buttons(self, context, layout):
        layout.prop(self, "midi_device")
        layout.prop(self, "midi_channel")
        
        layout.separator()
        
        # Manual note send controls
        box = layout.box()
        box.label(text="Manual Send:", icon='EXPORT')
        
        row = box.row(align=True)
        op = row.operator("audionodes.send_midi_note", text="Test Note")
        op.node_name = self.name
        op.tree_name = self.id_data.name
        op.note = 60
        op.velocity = 100


class AUDIONODES_OT_send_midi_note(bpy.types.Operator):
    """Send a short test note out of this MIDI Output node.

    This operator was referenced by the node's Test Note button but never
    existed - drawing an operator Blender cannot resolve raises inside the
    draw callback, which broke the whole node body.
    """
    bl_idname = "audionodes.send_midi_note"
    bl_label = "Send MIDI Note"
    bl_options = {'INTERNAL'}

    node_name: StringProperty()
    tree_name: StringProperty()
    note: IntProperty(default=60, min=0, max=127)
    velocity: IntProperty(default=100, min=0, max=127)
    duration: bpy.props.FloatProperty(default=0.25, min=0.01, max=5.0)

    def execute(self, context):
        from ... import command_client
        from ... import engine_manager

        node = find_node(context, self.node_name, bl_idname='AudioMIDIOutputNode',
                         tree_name=self.tree_name)
        if node is None:
            self.report({'WARNING'}, "MIDI Output node not found")
            return {'CANCELLED'}

        if not engine_manager.is_engine_running():
            self.report({'WARNING'}, "Engine not running")
            return {'CANCELLED'}

        if getattr(node, "midi_device", 'NONE') in ('', 'NONE'):
            self.report({'WARNING'}, "Pick a MIDI output device first")
            return {'CANCELLED'}

        engine_id = node.engine_node_id
        command_client.set_parameter(engine_id, "note", float(self.note))
        command_client.set_parameter(engine_id, "velocity", float(self.velocity))
        command_client.set_parameter(engine_id, "gate", 1.0)

        def note_off():
            if engine_manager.is_engine_running():
                command_client.set_parameter(engine_id, "gate", 0.0)
            return None  # one shot

        bpy.app.timers.register(note_off, first_interval=self.duration)
        self.report({'INFO'}, f"Sent note {self.note}")
        return {'FINISHED'}

