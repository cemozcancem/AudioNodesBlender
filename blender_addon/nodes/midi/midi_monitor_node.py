import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty, FloatProperty
from ..core.node_base import AudioNodeBase


class AudioMIDIMonitorNode(AudioNodeBase):
    """MIDI Monitor node - displays all incoming MIDI data for debugging.
    
    Shows:
    - Last note on/off with velocity
    - Note gate state
    - All active CC values
    - MIDI clock BPM
    - Transport state
    """
    bl_idname = "AudioMIDIMonitorNode"
    bl_label = "MIDI Monitor"
    bl_icon = 'INFO'
    bl_width_default = 300
    
    # Display options
    show_notes: BoolProperty(
        name="Show Notes",
        description="Show note on/off events",
        default=True
    )
    
    show_ccs: BoolProperty(
        name="Show CCs",
        description="Show CC values (only non-zero)",
        default=True
    )
    
    show_clock: BoolProperty(
        name="Show Clock",
        description="Show MIDI clock/transport info",
        default=True
    )
    
    show_all_ccs: BoolProperty(
        name="Show All CCs",
        description="Show all 128 CCs (not just active ones)",
        default=False
    )
    
    cc_threshold: FloatProperty(
        name="CC Threshold",
        description="Only show CCs above this value",
        default=0.01,
        min=0.0,
        max=1.0
    )
    
    # Cached display values (updated by timer)
    _last_note: int = 0
    _last_velocity: int = 0
    _note_gate: bool = False
    _bpm: float = 0.0
    _transport_playing: bool = False
    _active_ccs: list = []  # List of (cc_num, value) tuples
    
    @property
    def node_type(self) -> str:
        # Monitor is Blender-only, no C++ node
        return "midi_monitor"
    
    def init(self, context):
        # No sockets needed - this just displays data
        # Don't call super().init() - we don't create a C++ node
        import uuid
        self.engine_node_id = f"midi_monitor_{uuid.uuid4().hex[:8]}"
    
    def register_with_engine(self):
        """MIDI Monitor is Blender-only, no C++ registration needed."""
        pass
    
    def unregister_from_engine(self):
        """MIDI Monitor is Blender-only, no C++ unregistration needed."""
        pass
    
    def draw_buttons(self, context, layout):
        # Display options
        row = layout.row(align=True)
        row.prop(self, "show_notes", toggle=True, text="Notes")
        row.prop(self, "show_ccs", toggle=True, text="CCs")
        row.prop(self, "show_clock", toggle=True, text="Clock")
        
        if self.show_ccs:
            row = layout.row(align=True)
            row.prop(self, "show_all_ccs", toggle=True, text="All CCs")
            row.prop(self, "cc_threshold", text="Thresh")
        
        layout.separator()
        
        # Get MIDI data from shared memory
        try:
            scene = context.scene
            if not hasattr(scene, 'audio_nodes_data'):
                layout.label(text="No audio data available", icon='ERROR')
                return
            
            audio_data = scene.audio_nodes_data
            
            # Check if we have any MIDI data
            has_midi_data = False
            for ch in audio_data.channels:
                if ch.name.startswith("midi_"):
                    has_midi_data = True
                    break
            
            if not has_midi_data:
                box = layout.box()
                box.label(text="No MIDI data in shared memory", icon='ERROR')
                box.label(text="Check:")
                col = box.column(align=True)
                col.scale_y = 0.8
                col.label(text="1. Engine is running")
                col.label(text="2. MIDI Input node exists")
                col.label(text="3. MIDI device is selected")
                return
            
            # Note information
            if self.show_notes:
                box = layout.box()
                box.label(text="Notes:", icon='FILE_SOUND')
                
                last_note = 0
                last_velocity = 0
                note_gate = 0
                
                if "midi_last_note" in audio_data.channels:
                    last_note = int(audio_data.channels["midi_last_note"].value)
                if "midi_last_velocity" in audio_data.channels:
                    last_velocity = int(audio_data.channels["midi_last_velocity"].value * 127)
                if "midi_note_gate" in audio_data.channels:
                    note_gate = int(audio_data.channels["midi_note_gate"].value)
                
                # Note name conversion
                note_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
                note_name = note_names[last_note % 12]
                octave = (last_note // 12) - 1
                
                row = box.row()
                row.label(text=f"Note: {note_name}{octave} ({last_note})")
                row.label(text=f"Vel: {last_velocity}")
                
                row = box.row()
                if note_gate:
                    row.alert = True
                    row.label(text="GATE: ON", icon='RADIOBUT_ON')
                else:
                    row.label(text="GATE: OFF", icon='RADIOBUT_OFF')
            
            # Clock/Transport information
            if self.show_clock:
                box = layout.box()
                box.label(text="Clock/Transport:", icon='TIME')
                
                bpm = 0.0
                transport = 0
                
                if "midi_bpm" in audio_data.channels:
                    bpm = audio_data.channels["midi_bpm"].value
                if "midi_transport_playing" in audio_data.channels:
                    transport = int(audio_data.channels["midi_transport_playing"].value)
                
                row = box.row()
                row.label(text=f"BPM: {bpm:.1f}")
                
                if transport:
                    row.alert = True
                    row.label(text="PLAYING", icon='PLAY')
                else:
                    row.label(text="STOPPED", icon='PAUSE')
            
            # CC values
            if self.show_ccs:
                box = layout.box()
                box.label(text="CC Values:", icon='DRIVER')
                
                # Collect active CCs
                active_ccs = []
                for i in range(128):
                    cc_channel = f"midi_cc_{i}"
                    if cc_channel in audio_data.channels:
                        value = audio_data.channels[cc_channel].value
                        if self.show_all_ccs or value > self.cc_threshold:
                            active_ccs.append((i, value))
                
                if not active_ccs:
                    box.label(text="No active CCs", icon='INFO')
                else:
                    # Common CC names
                    cc_names = {
                        0: "Bank Select",
                        1: "Mod Wheel",
                        2: "Breath",
                        7: "Volume",
                        10: "Pan",
                        11: "Expression",
                        64: "Sustain",
                        65: "Portamento",
                        66: "Sostenuto",
                        67: "Soft Pedal",
                        91: "Reverb",
                        93: "Chorus",
                    }
                    
                    # Show CCs in a grid
                    col = box.column(align=True)
                    for cc_num, value in active_ccs[:20]:  # Limit to 20 to avoid UI overload
                        row = col.row(align=True)
                        cc_name = cc_names.get(cc_num, "")
                        
                        # Highlight if value is significant
                        if value > 0.5:
                            row.alert = True
                        
                        row.label(text=f"CC{cc_num:3d}")
                        if cc_name:
                            row.label(text=f"({cc_name})")
                        
                        # Show value as percentage and bar
                        row.label(text=f"{value:.2f}")
                        
                        # Visual bar representation
                        bar_chars = int(value * 10)
                        bar = "█" * bar_chars + "░" * (10 - bar_chars)
                        row.label(text=bar)
                    
                    if len(active_ccs) > 20:
                        box.label(text=f"... and {len(active_ccs) - 20} more CCs")
            
            # Debug info
            layout.separator()
            box = layout.box()
            box.label(text="Debug Info:", icon='CONSOLE')
            
            col = box.column(align=True)
            col.scale_y = 0.8
            col.label(text=f"Total channels: {len(audio_data.channels)}")
            
            # Count MIDI channels
            midi_channels = [ch.name for ch in audio_data.channels if ch.name.startswith("midi_")]
            col.label(text=f"MIDI channels: {len(midi_channels)}")
            
            # Check engine status
            from ... import engine_manager
            if engine_manager.is_engine_running():
                col.label(text="Engine: RUNNING", icon='CHECKMARK')
            else:
                col.alert = True
                col.label(text="Engine: NOT RUNNING", icon='ERROR')
            
            # Check the shared memory for this Blender's engine port
            from ... import shared_memory_reader
            size = shared_memory_reader.published_size()
            if size is not None:
                col.label(text=f"Shared mem: {size} bytes", icon='CHECKMARK')
            else:
                col.alert = True
                col.label(text="Shared mem: NOT FOUND", icon='ERROR')
            
        except Exception as e:
            layout.label(text=f"Error: {str(e)}", icon='ERROR')
            import traceback
            traceback.print_exc()
    
    def draw_buttons_ext(self, context, layout):
        """Extended panel in sidebar - show all data."""
        self.draw_buttons(context, layout)
        
        layout.separator()
        layout.label(text="All MIDI Channels:", icon='LINENUMBERS_ON')
        
        try:
            scene = context.scene
            if hasattr(scene, 'audio_nodes_data'):
                audio_data = scene.audio_nodes_data
                
                box = layout.box()
                col = box.column(align=True)
                col.scale_y = 0.7
                
                for channel in audio_data.channels:
                    if channel.name.startswith("midi_"):
                        row = col.row()
                        row.label(text=channel.name)
                        row.label(text=f"{channel.value:.4f}")
        except:
            pass

