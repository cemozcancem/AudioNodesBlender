import bpy
from bpy.props import StringProperty
import uuid


class AudioNodeBase(bpy.types.Node):
    """Base class for all Audio Nodes."""
    bl_idname = "AudioNodeBase"
    bl_label = "Audio Node Base"
    bl_icon = 'NODE'
    
    # Unique ID for this node instance in the engine
    engine_node_id: StringProperty(
        name="Engine Node ID",
        default=""
    )
    
    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == 'AudioNodeTree'
    
    def init(self, context):
        """Called when node is created."""
        # Generate unique ID
        self.engine_node_id = f"{self.node_type}_{uuid.uuid4().hex[:8]}"
        
        # Register with engine
        self.register_with_engine()
    
    def free(self):
        """Called when node is deleted."""
        self.unregister_from_engine()
    
    def copy(self, node):
        """Called when node is copied (duplicate / copy-paste).

        The copy gets a fresh engine id, so the engine knows nothing about
        the sample path, MIDI device or pattern the UI copied over from the
        original. create_node params alone do not carry those, so replay the
        same restore step the engine startup sync uses - otherwise a
        duplicated Sampler shows its file path but plays silence until the
        engine is restarted.
        """
        self.engine_node_id = f"{self.node_type}_{uuid.uuid4().hex[:8]}"
        self.register_with_engine()
        self._restore_engine_state()

    def _restore_engine_state(self):
        """Re-send node-specific state (samples, devices, patterns) to the engine."""
        from ... import engine_manager

        if not engine_manager.is_engine_running():
            return
        try:
            engine_manager._restore_node_state(self)
        except Exception as e:
            print(f"Audio Nodes: could not restore state for "
                  f"{getattr(self, 'engine_node_id', '?')}: {e!r}")
    
    @property
    def node_type(self) -> str:
        """Override in subclasses to return the C++ node type name."""
        return "base"
    
    def register_with_engine(self):
        """Register this node with the C++ engine."""
        from ... import command_client
        from ... import engine_manager
        
        if not engine_manager.is_engine_running():
            return
        
        params = self.get_initial_params()
        command_client.create_node(self.engine_node_id, self.node_type, params)
    
    def unregister_from_engine(self):
        """Unregister this node from the C++ engine."""
        from ... import command_client
        from ... import engine_manager
        
        if not engine_manager.is_engine_running():
            return
        
        command_client.delete_node(self.engine_node_id)
    
    def send_param(self, param_name: str, value: float):
        """Send a parameter update to the C++ engine."""
        from ... import command_client
        from ... import engine_manager
        
        if not engine_manager.is_engine_running():
            return
        
        # A manual edit must not leave a cached modulation suppressing the
        # next linked value (or a reconnect at the same value).
        from ... import shared_memory_reader
        shared_memory_reader._last_param_values.pop(f"{self.engine_node_id}_{param_name}", None)
        command_client.set_parameter(self.engine_node_id, param_name, value)
    
    def send_event(self, param_name: str, value: float = 1.0):
        """Send a one-off trigger / gate / transport EVENT to the engine.

        Unlike send_param(), this is never coalesced. Parameter updates are
        merged per (node, param) inside a ~33ms flush window, which is right
        for a knob being dragged and wrong for a trigger: two drum pad hits
        in the same window became one hit, and the second press of a Fire
        button vanished. Events go one message each, in order.
        """
        from ... import command_client
        from ... import engine_manager

        if not engine_manager.is_engine_running():
            return

        from ... import shared_memory_reader
        shared_memory_reader._last_param_values.pop(
            f"{self.engine_node_id}_{param_name}", None)
        command_client.send_event(self.engine_node_id, param_name, value)

    def get_initial_params(self) -> dict:
        """Override in subclasses to return initial parameter values."""
        return {}
    
    def draw_buttons(self, context, layout):
        """Draw node UI. Override in subclasses."""
        pass
    
    def draw_buttons_ext(self, context, layout):
        """Draw extended UI in sidebar. Override in subclasses."""
        self.draw_buttons(context, layout)


def find_node(context, node_name: str, bl_idname: str = "",
              tree_name: str = ""):
    """Resolve an operator's `node_name` against the tree it was clicked in.

    Node names are unique PER TREE, not globally. Every operator here used
    to scan `bpy.data.node_groups` and take the first name match, so with a
    second Audio Nodes tree open a Play/Trigger/Load button would act on
    the identically-named node in the wrong tree.

    The editor the button was drawn in is authoritative: if the name is not
    in that tree, the answer is "not found", not "keep looking". The global
    scan is only a fallback for callers with no editor context (scripts,
    tests) and only accepts an unambiguous match.
    """
    def _ok(node):
        return node is not None and (not bl_idname or node.bl_idname == bl_idname)

    # An explicit tree name always wins. File-browser operators (Load
    # Sample, Load Drum Sample) run their execute() with the FILE_BROWSER
    # space as context, so by then there is no node editor to ask - the
    # drawing code records the tree up front for exactly that reason.
    if tree_name:
        tree = bpy.data.node_groups.get(tree_name)
        if tree is not None and tree.bl_idname == 'AudioNodeTree':
            node = tree.nodes.get(node_name)
            return node if _ok(node) else None
        return None

    space = getattr(context, "space_data", None)
    tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)

    if tree is not None and getattr(tree, "bl_idname", "") == 'AudioNodeTree':
        node = tree.nodes.get(node_name)
        return node if _ok(node) else None

    matches = []
    for group in bpy.data.node_groups:
        if group.bl_idname != 'AudioNodeTree':
            continue
        node = group.nodes.get(node_name)
        if _ok(node):
            matches.append(node)
    return matches[0] if len(matches) == 1 else None
