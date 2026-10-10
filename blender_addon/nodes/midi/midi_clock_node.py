import bpy
from bpy.props import BoolProperty
from ..core.node_base import AudioNodeBase


class AudioMIDIClockNode(AudioNodeBase):
    """MIDI Clock node - outputs tempo and transport info from MIDI clock."""
    bl_idname = "AudioMIDIClockNode"
    bl_label = "MIDI Clock"
    bl_icon = 'TIME'
    
    show_beat_indicator: BoolProperty(
        name="Show Beat",
        description="Show beat indicator in node",
        default=True
    )
    
    @property
    def node_type(self) -> str:
        # Clock node is Blender-only, reads from shared memory
        return "midi_clock"
    
    def init(self, context):
        # Control outputs for clock data
        self.outputs.new('ControlSocketType', "BPM")
        self.outputs.new('ControlSocketType', "Beat")       # Current beat (0-3 for 4/4)
        self.outputs.new('ControlSocketType', "Bar")        # Current bar number
        self.outputs.new('ControlSocketType', "Playing")    # Transport state
        self.outputs.new('ControlSocketType', "Pulse")      # Beat pulse (1 on beat, 0 otherwise)
        
        # Don't register with C++ engine - this is Blender-only
        import uuid
        self.engine_node_id = f"midi_clock_{uuid.uuid4().hex[:8]}"
    
    def register_with_engine(self):
        # Clock node doesn't register with C++ engine
        pass
    
    def unregister_from_engine(self):
        # Clock node doesn't register with C++ engine
        pass
    
    def draw_buttons(self, context, layout):
        layout.prop(self, "show_beat_indicator")
        
        # Display current clock values
        try:
            scene = context.scene
            if hasattr(scene, 'audio_nodes_data'):
                audio_data = scene.audio_nodes_data
                
                box = layout.box()
                box.label(text="MIDI Clock:", icon='TIME')
                
                col = box.column(align=True)
                
                # BPM
                if 'midi_bpm' in audio_data.channels:
                    bpm = audio_data.channels['midi_bpm'].value
                    col.label(text=f"BPM: {bpm:.1f}")
                
                # Transport state
                if 'midi_transport_playing' in audio_data.channels:
                    playing = audio_data.channels['midi_transport_playing'].value > 0.5
                    state = "PLAYING" if playing else "STOPPED"
                    row = col.row()
                    row.alert = playing
                    row.label(text=f"Transport: {state}", icon='PLAY' if playing else 'PAUSE')
                
                # Beat indicator
                if self.show_beat_indicator:
                    # Calculate beat from tick position
                    # 24 ticks per quarter note
                    tick = 0
                    if 'midi_tick_position' in audio_data.channels:
                        tick = int(audio_data.channels['midi_tick_position'].value)
                    
                    beat_in_bar = (tick // 24) % 4  # Assuming 4/4 time
                    bar = tick // (24 * 4) + 1
                    
                    row = box.row(align=True)
                    row.scale_y = 1.5
                    for i in range(4):
                        sub = row.row(align=True)
                        sub.alert = (i == beat_in_bar)
                        sub.operator("audionodes.dummy", text=str(i + 1), emboss=(i == beat_in_bar))
                    
                    col.label(text=f"Bar: {bar}")
        except:
            pass
    
    def get_output_channel_name(self, output_name: str) -> str:
        """Get the channel name for an output socket."""
        channel_map = {
            "BPM": "midi_bpm",
            "Playing": "midi_transport_playing",
        }
        return channel_map.get(output_name, "")


class AUDIONODES_OT_dummy(bpy.types.Operator):
    """Dummy operator for beat indicator buttons."""
    bl_idname = "audionodes.dummy"
    bl_label = ""
    bl_options = {'INTERNAL'}
    
    def execute(self, context):
        return {'FINISHED'}

