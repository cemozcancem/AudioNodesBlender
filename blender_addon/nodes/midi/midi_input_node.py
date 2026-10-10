import bpy
from bpy.props import IntProperty, StringProperty
from ..core.node_base import AudioNodeBase


# Cache for MIDI devices to avoid blocking ZMQ calls on every UI redraw
_midi_device_cache = {
    'input_devices': [],
    'output_devices': [],
    'last_refresh': 0
}


def refresh_midi_device_cache():
    """Refresh the MIDI device cache from the engine."""
    import time
    from ... import command_client
    from ... import engine_manager
    
    if not engine_manager.is_engine_running():
        return
    
    devices = command_client.get_midi_devices()
    if devices:
        _midi_device_cache['input_devices'] = devices.get('input_devices', [])
        _midi_device_cache['output_devices'] = devices.get('output_devices', [])
        _midi_device_cache['last_refresh'] = time.time()


class AudioMIDIInputNode(AudioNodeBase):
    """MIDI Input node - receives MIDI from external devices."""
    bl_idname = "AudioMIDIInputNode"
    bl_label = "MIDI Input"
    bl_icon = 'FILE_SOUND'
    
    # Store device name as string (persists correctly across sessions)
    midi_device_name: StringProperty(
        name="Device Name",
        description="Selected MIDI input device name",
        default=""
    )
    
    midi_channel: IntProperty(
        name="Channel",
        description="MIDI channel to listen to (0 = all)",
        default=0,
        min=0,
        max=16,
        update=lambda self, ctx: self.send_param("channel", float(self.midi_channel))
    )
    
    @property
    def node_type(self) -> str:
        return "midi_input"
    
    def _set_device(self, device_name: str):
        """Set the MIDI device and send to engine."""
        self.midi_device_name = device_name
        from ... import command_client
        from ... import engine_manager
        if engine_manager.is_engine_running() and device_name:
            command_client.set_midi_device('input', device_name)
    
    def init(self, context):
        # Control outputs for MIDI data
        self.outputs.new('ControlSocketType', "Note")
        self.outputs.new('ControlSocketType', "Velocity")
        self.outputs.new('ControlSocketType', "Gate")
        self.outputs.new('ControlSocketType', "Channel")
        
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {
            "channel": float(self.midi_channel)
        }
    
    def draw_buttons(self, context, layout):
        # Device selection dropdown
        row = layout.row(align=True)
        
        # Create a menu for device selection
        devices = _midi_device_cache.get('input_devices', [])
        current = self.midi_device_name if self.midi_device_name else "None"
        
        # Show current device with dropdown menu
        row.menu("AUDIONODES_MT_midi_input_devices", text=current, icon='FILE_SOUND')
        row.operator("audionodes.refresh_midi_devices", text="", icon='FILE_REFRESH')
        
        layout.prop(self, "midi_channel")
        
        layout.separator()
        
        # Display current MIDI values
        try:
            scene = context.scene
            if hasattr(scene, 'audio_nodes_data'):
                audio_data = scene.audio_nodes_data
                
                box = layout.box()
                box.label(text="MIDI Input:", icon='FILE_SOUND')
                
                col = box.column(align=True)
                
                if 'midi_last_note' in audio_data.channels:
                    note = int(audio_data.channels['midi_last_note'].value)
                    note_name = self._note_to_name(note)
                    col.label(text=f"Note: {note} ({note_name})")
                
                if 'midi_last_velocity' in audio_data.channels:
                    vel = audio_data.channels['midi_last_velocity'].value
                    col.label(text=f"Velocity: {vel:.2f}")
                
                if 'midi_note_gate' in audio_data.channels:
                    gate = audio_data.channels['midi_note_gate'].value
                    gate_text = "ON" if gate > 0.5 else "OFF"
                    col.label(text=f"Gate: {gate_text}")
        except:
            pass
    
    def _note_to_name(self, note: int) -> str:
        """Convert MIDI note number to note name."""
        notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        octave = (note // 12) - 1
        note_name = notes[note % 12]
        return f"{note_name}{octave}"
    
    def get_output_channel_name(self, output_name: str) -> str:
        """Get the channel name for an output socket."""
        channel_map = {
            "Note": "midi_last_note",
            "Velocity": "midi_last_velocity",
            "Gate": "midi_note_gate",
        }
        return channel_map.get(output_name, "")


class AUDIONODES_MT_midi_input_devices(bpy.types.Menu):
    """Menu for selecting MIDI input devices."""
    bl_idname = "AUDIONODES_MT_midi_input_devices"
    bl_label = "Select MIDI Device"
    
    def draw(self, context):
        layout = self.layout
        
        # None option
        op = layout.operator("audionodes.select_midi_device", text="None")
        op.device_name = ""
        
        layout.separator()
        
        # List available devices
        devices = _midi_device_cache.get('input_devices', [])
        if devices:
            for device in devices:
                op = layout.operator("audionodes.select_midi_device", text=device)
                op.device_name = device
        else:
            layout.label(text="No devices found")
            layout.label(text="Click refresh button")


class AUDIONODES_OT_select_midi_device(bpy.types.Operator):
    """Select a MIDI input device."""
    bl_idname = "audionodes.select_midi_device"
    bl_label = "Select MIDI Device"
    bl_options = {'INTERNAL'}
    
    device_name: StringProperty()
    
    def execute(self, context):
        # Find the active MIDI Input node
        node = context.active_node
        if node and node.bl_idname == 'AudioMIDIInputNode':
            node._set_device(self.device_name)
            if self.device_name:
                self.report({'INFO'}, f"Selected: {self.device_name}")
            else:
                self.report({'INFO'}, "MIDI device disconnected")
        return {'FINISHED'}


class AUDIONODES_OT_refresh_midi_devices(bpy.types.Operator):
    """Refresh the list of available MIDI devices."""
    bl_idname = "audionodes.refresh_midi_devices"
    bl_label = "Refresh MIDI Devices"
    bl_description = "Refresh the list of available MIDI input devices"
    
    def execute(self, context):
        from ... import engine_manager
        
        if not engine_manager.is_engine_running():
            self.report({'WARNING'}, "Engine not running")
            return {'CANCELLED'}
        
        refresh_midi_device_cache()
        
        count = len(_midi_device_cache.get('input_devices', []))
        self.report({'INFO'}, f"Found {count} MIDI input device(s)")
        
        # Force UI update in every window, not just the active one
        for window in context.window_manager.windows:
            if window.screen is None:
                continue
            for area in window.screen.areas:
                area.tag_redraw()
        
        return {'FINISHED'}
