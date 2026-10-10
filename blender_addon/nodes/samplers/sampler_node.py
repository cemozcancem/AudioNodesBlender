import bpy
from bpy.props import (
    FloatProperty, StringProperty, BoolProperty,
    IntProperty
)
from bpy_extras.io_utils import ImportHelper
from ..core.node_base import AudioNodeBase, find_node


class AudioSamplerNode(AudioNodeBase):
    """Sampler node - plays audio samples triggered by input."""
    bl_idname = "AudioSamplerNode"
    bl_label = "Sampler"
    bl_icon = 'FILE_SOUND'
    bl_width_default = 220

    # --- In-node graphics (see ui/node_graphics.py) ---------------------
    GRAPH_ROWS = 4

    show_graph: BoolProperty(
        name="Waveform",
        description="Show the sample waveform in the node",
        default=True
    )
    
    sample_path: StringProperty(
        name="Sample",
        description="Path to audio sample file",
        default="",
        subtype='FILE_PATH',
        update=lambda self, ctx: self._on_sample_path_changed()
    )
    
    def _on_sample_path_changed(self):
        """Auto-load sample when path changes."""
        if self.sample_path:
            from ... import command_client
            from ... import engine_manager
            if engine_manager.is_engine_running():
                command_client.load_sample(self.engine_node_id, bpy.path.abspath(self.sample_path))
    
    volume: FloatProperty(
        name="Volume",
        description="Playback volume",
        default=1.0,
        min=0.0,
        max=2.0,
        update=lambda self, ctx: self.send_param("volume", self.volume)
    )
    
    pitch: FloatProperty(
        name="Pitch",
        description="Pitch shift in semitones",
        default=0.0,
        min=-24.0,
        max=24.0,
        update=lambda self, ctx: self.send_param("pitch", self.pitch)
    )
    
    start_point: FloatProperty(
        name="Start",
        description="Sample start point (0-100%)",
        default=0.0,
        min=0.0,
        max=100.0,
        subtype='PERCENTAGE',
        update=lambda self, ctx: self.send_param("start", self.start_point / 100.0)
    )
    
    end_point: FloatProperty(
        name="End",
        description="Sample end point (0-100%)",
        default=100.0,
        min=0.0,
        max=100.0,
        subtype='PERCENTAGE',
        update=lambda self, ctx: self.send_param("end", self.end_point / 100.0)
    )
    
    loop: BoolProperty(
        name="Loop",
        description="Loop the sample",
        default=False,
        update=lambda self, ctx: self.send_param("loop", 1.0 if self.loop else 0.0)
    )
    
    one_shot: BoolProperty(
        name="One Shot",
        description="Restart on retrigger (vs. ignore while playing)",
        default=True,
        update=lambda self, ctx: self.send_param("one_shot", 1.0 if self.one_shot else 0.0)
    )

    volume_mod_depth: FloatProperty(
        name="Vol Mod",
        description="How much control input affects volume",
        default=1.0,
        min=0.0,
        max=2.0
    )

    pitch_mod_depth: FloatProperty(
        name="Pitch Mod",
        description="How much control input affects pitch (semitones)",
        default=12.0,
        min=0.0,
        max=24.0
    )

    start_mod_depth: FloatProperty(
        name="Start Mod",
        description="How much control input affects sample start (%)",
        default=50.0,
        min=0.0,
        max=100.0
    )

    end_mod_depth: FloatProperty(
        name="End Mod",
        description="How much control input affects sample end (%)",
        default=50.0,
        min=0.0,
        max=100.0
    )
    
    @property
    def node_type(self) -> str:
        return "sampler"
    
    def init(self, context):
        # Trigger input
        self.inputs.new('ControlSocketType', "Trigger")
        self.inputs.new('ControlSocketType', "Volume")
        self.inputs.new('ControlSocketType', "Pitch")
        self.inputs.new('ControlSocketType', "Start")
        self.inputs.new('ControlSocketType', "End")
        
        # Audio output
        self.outputs.new('AudioSocketType', "Output")
        
        super().init(context)
    
    def get_initial_params(self) -> dict:
        return {
            "volume": self.volume,
            "pitch": self.pitch,
            "start": self.start_point / 100.0,
            "end": self.end_point / 100.0,
            "loop": 1.0 if self.loop else 0.0,
            "one_shot": 1.0 if self.one_shot else 0.0,
        }

    def get_modulatable_params(self) -> dict:
        return {
            "Volume": {
                "param": "volume",
                "base": self.volume,
                "min": 0.0,
                "max": 2.0,
                "mod_depth": self.volume_mod_depth
            },
            "Pitch": {
                "param": "pitch",
                "base": self.pitch,
                "min": -24.0,
                "max": 24.0,
                "mod_depth": self.pitch_mod_depth
            },
            "Start": {
                "param": "start",
                "base": self.start_point / 100.0,
                "min": 0.0,
                "max": 1.0,
                "mod_depth": self.start_mod_depth / 100.0
            },
            "End": {
                "param": "end",
                "base": self.end_point / 100.0,
                "min": 0.0,
                "max": 1.0,
                "mod_depth": self.end_mod_depth / 100.0
            },
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
        # Sample file selection (auto-loads on change)
        row = layout.row(align=True)
        row.prop(self, "sample_path", text="")
        op = row.operator("audionodes.load_sample", text="", icon='FILE_FOLDER')
        op.node_name = self.name
        op.tree_name = self.id_data.name
        
        from ... import sample_loading
        sample_loading.draw_status(layout, self, self.sample_path)
        if self.sample_path:
            op = layout.operator("audionodes.send_sample_path", text="Reload sample", icon='FILE_REFRESH')
            op.node_name = self.name
            op.tree_name = self.id_data.name
            op.file_path = self.sample_path

        layout.separator()

        # Volume and pitch
        row = layout.row()
        row.prop(self, "volume")
        row.prop(self, "pitch")
        
        # Start/End points
        row = layout.row()
        row.prop(self, "start_point")
        row.prop(self, "end_point")
        
        # Options
        row = layout.row()
        row.prop(self, "loop")
        row.prop(self, "one_shot")

        volume_socket = self.inputs.get("Volume")
        pitch_socket = self.inputs.get("Pitch")
        start_socket = self.inputs.get("Start")
        end_socket = self.inputs.get("End")

        if volume_socket and volume_socket.is_linked:
            layout.prop(self, "volume_mod_depth")
        if pitch_socket and pitch_socket.is_linked:
            layout.prop(self, "pitch_mod_depth")
        if start_socket and start_socket.is_linked:
            layout.prop(self, "start_mod_depth")
        if end_socket and end_socket.is_linked:
            layout.prop(self, "end_mod_depth")
        
        layout.separator()
        
        # Manual trigger
        row = layout.row()
        op = row.operator("audionodes.trigger_sampler", text="Trigger", icon='PLAY')
        op.node_name = self.name
        op.tree_name = self.id_data.name


class AUDIONODES_OT_load_sample(bpy.types.Operator, ImportHelper):
    bl_idname = "audionodes.load_sample"
    bl_label = "Load Sample"
    bl_description = "Select an audio sample file"
    
    filter_glob: StringProperty(
        default="*.wav;*.mp3;*.aiff;*.flac;*.ogg",
        options={'HIDDEN'}
    )
    
    node_name: StringProperty()
    tree_name: StringProperty()
    
    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioSamplerNode',
                         tree_name=self.tree_name)
        if node:
            node.sample_path = self.filepath
        return {'FINISHED'}
    


class AUDIONODES_OT_send_sample_path(bpy.types.Operator):
    bl_idname = "audionodes.send_sample_path"
    bl_label = "Send Sample Path"
    bl_description = "Load the sample in the audio engine"
    
    node_name: StringProperty()
    tree_name: StringProperty()
    file_path: StringProperty()
    
    def execute(self, context):
        from ... import command_client
        
        node = find_node(context, self.node_name, bl_idname='AudioSamplerNode',
                         tree_name=self.tree_name)
        if node:
            # Send load sample command to engine
            from ... import engine_manager
            if not engine_manager.is_engine_running():
                self.report({'WARNING'}, "Start the audio engine before loading")
                return {'CANCELLED'}
            command_client.load_sample(node.engine_node_id, bpy.path.abspath(self.file_path))
            self.report({'INFO'}, f"Loading sample: {self.file_path}")
        return {'FINISHED'}
    


class AUDIONODES_OT_trigger_sampler(bpy.types.Operator):
    bl_idname = "audionodes.trigger_sampler"
    bl_label = "Trigger Sampler"
    bl_options = {'INTERNAL'}
    
    node_name: StringProperty()
    tree_name: StringProperty()
    
    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioSamplerNode',
                         tree_name=self.tree_name)
        if node:
            node.send_event("trigger", 1.0)
            # Capture engine_node_id to avoid referencing self in timer
            engine_id = node.engine_node_id
            
            def send_trigger_off():
                from ... import command_client
                from ... import engine_manager
                if engine_manager.is_engine_running():
                    command_client.set_parameter(engine_id, "trigger", 0.0)
                return None  # Don't repeat
            
            # Send trigger off shortly after (simulating a gate pulse)
            bpy.app.timers.register(send_trigger_off, first_interval=0.1)
        return {'FINISHED'}
    

