import bpy
from bpy.props import (
    StringProperty, 
    FloatProperty, 
    PointerProperty,
)
from .node_base import AudioNodeBase
import uuid


class AudioValueOutputNode(AudioNodeBase):
    """Value Output node - exports control signals to Geometry Nodes.
    
    This node bridges audio control signals (from LFO, FFT, MIDI, etc.)
    to Geometry Nodes modifier inputs for real-time updates.
    
    This is a Blender-only node - no C++ counterpart is needed.
    """
    bl_idname = "AudioValueOutputNode"
    bl_label = "Value Output"
    bl_icon = 'EXPORT'
    bl_width_default = 180
    
    # Target object with Geometry Nodes modifier
    target_object: PointerProperty(
        name="Target",
        description="Object with Geometry Nodes modifier to receive the value",
        type=bpy.types.Object
    )
    
    # Name of the modifier input (must match Group Input socket name)
    property_name: StringProperty(
        name="Input Name",
        description="Name of the Geometry Nodes input socket (must match exactly)",
        default="audio_value"
    )
    
    # Output range mapping
    min_value: FloatProperty(
        name="Min",
        description="Minimum output value (maps from 0)",
        default=0.0
    )
    
    max_value: FloatProperty(
        name="Max",
        description="Maximum output value (maps from 1)",
        default=1.0
    )
    
    # Temporal smoothing
    smoothing: FloatProperty(
        name="Smoothing",
        description="Temporal smoothing factor (0 = none, 0.99 = very smooth)",
        default=0.0,
        min=0.0,
        max=0.99,
        subtype='FACTOR'
    )
    
    @property
    def node_type(self) -> str:
        return "value_output"
    
    def init(self, context):
        # Control input - receives signal from LFO, FFT, MIDI CC, etc.
        self.inputs.new('ControlSocketType', "Value")
        
        # Generate ID for tracking
        self.engine_node_id = f"value_output_{uuid.uuid4().hex[:8]}"
    
    def register_with_engine(self):
        """Value Output is Blender-only, no C++ registration needed."""
        pass
    
    def unregister_from_engine(self):
        """Value Output is Blender-only, no C++ unregistration needed."""
        pass
    
    def get_channel_name(self) -> str:
        """Return the channel name for this node's value storage."""
        return f"{self.engine_node_id}_value"
    
    def get_current_value(self) -> float:
        """Get the current output value."""
        try:
            scene = bpy.context.scene
            if hasattr(scene, 'audio_nodes_data'):
                channel_name = self.get_channel_name()
                if channel_name in scene.audio_nodes_data.channels:
                    return scene.audio_nodes_data.channels[channel_name].value
        except Exception:
            pass
        return 0.0
    
    def draw_buttons(self, context, layout):
        # Target object picker
        layout.prop(self, "target_object", text="")
        
        # Input name (matches Geometry Nodes Group Input socket)
        layout.prop(self, "property_name", text="", icon='NODE')
        
        # Range mapping
        row = layout.row(align=True)
        row.prop(self, "min_value")
        row.prop(self, "max_value")
        
        # Smoothing
        layout.prop(self, "smoothing")
        
        # Current value display
        box = layout.box()
        box.scale_y = 0.8
        value = self.get_current_value()
        box.label(text=f"Value: {value:.3f}")
    
    def draw_buttons_ext(self, context, layout):
        """Extended UI in sidebar."""
        self.draw_buttons(context, layout)
        
        layout.separator()
        box = layout.box()
        box.label(text="Setup:", icon='QUESTION')
        col = box.column(align=True)
        col.scale_y = 0.8
        col.label(text="1. Add Group Input in Geometry Nodes")
        col.label(text="2. Create a Float input socket")
        col.label(text="3. Name it to match 'Input Name' above")
        col.label(text="4. Connect to your nodes")
