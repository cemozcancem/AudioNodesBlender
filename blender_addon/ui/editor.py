import bpy


class AUDIONODES_OT_new_tree(bpy.types.Operator):
    """Create a new Audio Nodes tree"""
    bl_idname = "audionodes.new_tree"
    bl_label = "New Audio Nodes Tree"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        # Create new node tree
        tree = bpy.data.node_groups.new("Audio Nodes", 'AudioNodeTree')
        
        # Set it as active in the current space
        context.space_data.node_tree = tree
        
        self.report({'INFO'}, f"Created new Audio Nodes tree: {tree.name}")
        return {'FINISHED'}


def _draw_audio_header(self, context):
    """Appended to Blender's own node-editor header.

    A second `bpy.types.Header` class for NODE_EDITOR is NOT drawn by
    Blender 5.x - only the built-in one is - so the engine status and Add
    button used to be invisible. Appending onto NODE_HT_header puts them
    back in the header where they belong, and also avoids the duplicate
    editor-type dropdown a second header's template_header() produced.
    """
    snode = context.space_data
    if snode is None or snode.tree_type != 'AudioNodeTree':
        return

    layout = self.layout
    from .. import engine_manager

    layout.separator_spacer()

    row = layout.row(align=True)
    if engine_manager.is_engine_running():
        row.label(text="Engine: Running", icon='PLAY')
        row.operator("audionodes.stop_engine", text="", icon='PAUSE')
    else:
        row.alert = True
        row.label(text="Engine: Stopped", icon='PAUSE')
        row.operator("audionodes.start_engine", text="", icon='PLAY')

    if snode.node_tree is not None:
        layout.menu("AUDIONODES_MT_add", text="Add")
    else:
        layout.label(text="Create a tree to add nodes", icon='INFO')


def register_header():
    bpy.types.NODE_HT_header.append(_draw_audio_header)


def unregister_header():
    try:
        bpy.types.NODE_HT_header.remove(_draw_audio_header)
    except Exception:
        pass
