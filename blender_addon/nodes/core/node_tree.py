import bpy
from bpy.props import FloatProperty, StringProperty, CollectionProperty


class AudioChannelData(bpy.types.PropertyGroup):
    """Stores a single audio channel value for driver access."""
    value: FloatProperty(
        name="Value",
        default=0.0,
        description="Current audio value for this channel"
    )


class AudioNodesData(bpy.types.PropertyGroup):
    """Global audio data storage for driver access."""
    channels: CollectionProperty(type=AudioChannelData)

    # Persisted audio device selection — saved with the .blend file so the
    # engine restores the user's chosen input/output devices instead of
    # falling back to whatever JUCE picks as the system default (which on
    # macOS is often a virtual loopback device like BlackHole and produces a
    # feedback loop when used for both input and output).
    input_device_name: StringProperty(
        name="Input Device",
        description="Last-used audio input device, restored when the engine starts",
        default=""
    )
    output_device_name: StringProperty(
        name="Output Device",
        description="Last-used audio output device, restored when the engine starts",
        default=""
    )


class AudioSocket(bpy.types.NodeSocket):
    """Audio signal socket type."""
    bl_idname = "AudioSocketType"
    bl_label = "Audio Socket"
    
    def draw(self, context, layout, node, text):
        layout.label(text=text)
    
    def draw_color(self, context, node):
        return (0.2, 0.8, 0.2, 1.0)  # Green for audio


class ControlSocket(bpy.types.NodeSocket):
    """Control signal socket type (for parameters)."""
    bl_idname = "ControlSocketType"
    bl_label = "Control Socket"

    default_value: FloatProperty(
        name="Value",
        default=0.0,
        description="Default value when not connected"
    )

    # Set on FFT Analyzer tap outputs/inputs: stable id linking the socket
    # to its FrequencyTap entry, so renames/reorders never break routing.
    tap_id: StringProperty(
        name="Tap ID",
        default=""
    )

    # Set on FFT Analyzer tap *parameter inputs* (e.g. "center_hz", "gain").
    # Together with tap_id this uniquely identifies the input across renames.
    tap_param: StringProperty(
        name="Tap Param",
        default=""
    )

    def draw(self, context, layout, node, text):
        # Tap param inputs are driven by cables / tap UI sliders — no
        # inline default_value widget (that would fight the tap editor).
        if self.is_output or self.is_linked or self.tap_param:
            layout.label(text=text)
        else:
            layout.prop(self, "default_value", text=text)

    def draw_color(self, context, node):
        if self.tap_id and node.bl_idname == 'AudioFFTAnalyzerNode':
            # Tap sockets take their tap's marker color
            for tap in getattr(node, "taps", []):
                if tap.tap_id == self.tap_id:
                    c = tap.color
                    return (c[0], c[1], c[2], 1.0)
        return (0.8, 0.6, 0.2, 1.0)  # Orange for control


# Track current connections to detect actual changes
_current_connections = {}  # tree_name -> set of (from_id, to_id, from_port, to_port)
_sync_scheduled = False


def clear_connection_cache():
    """Drop all cached connection sets (call on engine start/stop)."""
    global _current_connections
    _current_connections = {}


def set_connection_cache(tree_name, connections):
    """Replace the cached connection set for one tree after a full sync."""
    global _current_connections
    _current_connections[tree_name] = set(connections)


def _get_tree_connections(tree):
    """Get the current set of audio connections in a tree."""
    connections = set()
    for link in tree.links:
        if not link.is_valid:
            continue
        
        from_node = link.from_node
        to_node = link.to_node
        
        if not hasattr(from_node, 'engine_node_id') or not hasattr(to_node, 'engine_node_id'):
            continue
        
        # Blender-only sources/sinks are evaluated by the control timer.
        if (getattr(from_node, 'node_type', '') in BLENDER_ONLY_NODE_TYPES or
                getattr(to_node, 'node_type', '') in BLENDER_ONLY_NODE_TYPES):
            continue

        # Track both audio and control connections
        if link.from_socket.bl_idname not in ('AudioSocketType', 'ControlSocketType'):
            continue

        # Never hand a cross-type link to the engine (the pruner removes
        # these, but the diff can observe one first).
        if not _link_type_is_valid(link):
            continue
        
        from_port = link.from_socket.name.lower().replace(" ", "_")
        to_port = link.to_socket.name.lower().replace(" ", "_")
        
        connections.add((
            from_node.engine_node_id,
            to_node.engine_node_id,
            from_port,
            to_port
        ))
    
    return connections


AUDIO_SOCKET = 'AudioSocketType'
CONTROL_SOCKET = 'ControlSocketType'
_OUR_SOCKETS = (AUDIO_SOCKET, CONTROL_SOCKET)


def _link_type_is_valid(link) -> bool:
    """Is this link between two sockets of the same signal kind?

    Audio (green) carries sample buffers in the C++ graph; Control (orange)
    carries a single float computed Blender-side. Crossing them produces a
    link Blender happily marks valid that does nothing at all - and worse,
    the connection diff then sends it to the engine as a connect for a node
    that has no C++ counterpart. Sockets we do not own are left alone.
    """
    from_type = link.from_socket.bl_idname
    to_type = link.to_socket.bl_idname
    if from_type not in _OUR_SOCKETS or to_type not in _OUR_SOCKETS:
        return True
    return from_type == to_type


def prune_invalid_links(tree) -> int:
    """Drop Audio<->Control links from a tree. Returns how many went."""
    invalid = [link for link in tree.links if not _link_type_is_valid(link)]
    for link in invalid:
        print(f"Audio Nodes: refusing "
              f"{link.from_node.name}.{link.from_socket.name} -> "
              f"{link.to_node.name}.{link.to_socket.name} "
              f"(audio and control signals cannot be mixed)")
        tree.links.remove(link)
    return len(invalid)


def _do_sync_connections():
    """Actually perform the connection sync (called from timer)."""
    global _sync_scheduled, _current_connections
    _sync_scheduled = False

    from ... import command_client
    from ... import engine_manager

    # Type-check links first, and do it whether or not the engine is up -
    # an invalid link must not survive just because nothing is running.
    # Removing a link re-enters update() and schedules another pass; that
    # pass finds nothing left to prune, so this terminates.
    for tree in bpy.data.node_groups:
        if tree.bl_idname == 'AudioNodeTree':
            prune_invalid_links(tree)

    if not engine_manager.is_engine_running():
        return None
    
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        
        new_connections = _get_tree_connections(tree)
        old_connections = _current_connections.get(tree.name, set())
        
        # Find what changed
        to_remove = old_connections - new_connections
        to_add = new_connections - old_connections
        
        if not to_remove and not to_add:
            continue
        
        print(f"Audio Nodes: Updating connections for '{tree.name}'")
        
        # Remove old connections
        for from_id, to_id, from_port, to_port in to_remove:
            print(f"  Disconnecting: {to_id}.{to_port}")
            command_client.disconnect_nodes(to_id, to_port)
        
        # Add new connections
        for from_id, to_id, from_port, to_port in to_add:
            print(f"  Connecting: {from_id} -> {to_id}")
            command_client.connect_nodes(from_id, to_id, from_port, to_port)
        
        # Update tracked state
        _current_connections[tree.name] = new_connections
    
    return None  # Don't repeat timer


def schedule_connection_sync():
    """Schedule a connection sync (debounced)."""
    global _sync_scheduled
    
    if _sync_scheduled:
        return
    
    _sync_scheduled = True
    # Delay sync by 100ms to batch rapid changes
    bpy.app.timers.register(_do_sync_connections, first_interval=0.1)


def cancel_pending_sync():
    """Drop a queued connection sync (call from unregister()).

    Without this the timer outlives the add-on: disabling Audio Nodes within
    100ms of a graph edit left `_do_sync_connections` queued, and it then
    fired into a module that had already been torn down.
    """
    global _sync_scheduled
    _sync_scheduled = False
    try:
        bpy.app.timers.unregister(_do_sync_connections)
    except Exception:
        pass  # already fired, or never registered


# Node types that live only in Blender (no C++ counterpart) - never sent
# to the engine. Keep in sync with engine_manager.sync_all_nodes_to_engine.
BLENDER_ONLY_NODE_TYPES = (
    'attribute', 'lfo', 'midi_monitor', 'midi_cc', 'midi_clock',
    'value_output', 'nla_scrub',
    'trigger', 'accumulator', 'decay', 'camera_cutter',
)


def ensure_engine_sync():
    """Self-healing reconciliation between Blender trees and the engine.

    Blender's update() callbacks can miss a node-add or link change (the
    symptom: a newly added node has no effect until the engine restarts).
    This runs periodically from the visualization timer and repairs any
    drift: nodes that were never registered get created, and when drift
    is found the tree's connection cache is invalidated so the diff
    re-sends its links (connect is idempotent engine-side). The no-drift
    path is a cheap set comparison, so running it at ~2Hz is free.
    """
    global _current_connections

    from ... import command_client
    from ... import engine_manager

    if not engine_manager.is_engine_running():
        return

    repaired = 0
    # getattr, not n.engine_node_id: an AudioNodeTree can hold plain Blender
    # nodes (NodeFrame, NodeReroute) that have neither engine_node_id nor
    # node_type. Reading the attribute directly raised AttributeError here and
    # killed the whole reconcile pass on every tick - so in any tree with a
    # frame in it, the self-healing sync silently did nothing at all.
    live_ids = {nid for t in bpy.data.node_groups
                if t.bl_idname == 'AudioNodeTree' for n in t.nodes
                if getattr(n, 'node_type', '') not in BLENDER_ONLY_NODE_TYPES
                for nid in (getattr(n, 'engine_node_id', ''),) if nid}
    for stale_id in command_client.registered_node_ids() - live_ids:
        command_client.delete_node(stale_id)
    # Reconcile failed ports against today's graph, not the cable that
    # existed when the command failed. Never erase unrelated cached links:
    # doing so would lose disconnects made while a create was being retried.
    desired_ports = {(conn[1], conn[3]): conn for tree in bpy.data.node_groups
                     if tree.bl_idname == 'AudioNodeTree'
                     for conn in _get_tree_connections(tree)}
    for key in command_client.get_command_errors():
        if key[0] not in ('connect', 'disconnect'):
            continue
        if key[1] not in live_ids:
            command_client.clear_command_error(key)
            continue
        desired = desired_ports.get((key[1], key[2]))
        if desired:
            command_client.connect_nodes(*desired)
        else:
            command_client.disconnect_nodes(key[1], key[2])
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue

        recreated = set()
        for node in tree.nodes:
            node_id = getattr(node, 'engine_node_id', '')
            node_type = getattr(node, 'node_type', '')
            if not node_id or not node_type:
                continue
            if node_type in BLENDER_ONLY_NODE_TYPES:
                continue
            if command_client.is_node_registered(node_id) or command_client.is_node_pending(node_id):
                continue

            params = node.get_initial_params() if hasattr(node, 'get_initial_params') else {}
            command_client.create_node(node_id, node_type, params)
            node._restore_engine_state()
            recreated.add(node_id)
            repaired += 1

        if recreated:
            previous = _current_connections.get(tree.name, set())
            _current_connections[tree.name] = {
                conn for conn in previous
                if conn[0] not in recreated and conn[1] not in recreated}

    if repaired:
        print(f"Audio Nodes: engine sync repaired {repaired} missing node(s)")

    # Always run the link diff - catches missed link events too. Cheap:
    # set comparison, sends nothing when nothing changed. It also prunes
    # any Audio<->Control link that slipped past update().
    _do_sync_connections()


class AudioNodeTree(bpy.types.NodeTree):
    """Audio Nodes node tree type."""
    bl_idname = "AudioNodeTree"
    bl_label = "Audio Nodes"
    bl_icon = 'SPEAKER'
    
    @classmethod
    def poll(cls, context):
        return True
    
    def update(self):
        """Called when node tree structure changes (connections, etc.)."""
        # Schedule a debounced sync instead of syncing immediately
        schedule_connection_sync()
    
    def sync_all_connections(self):
        """Force sync all connections (used during engine startup)."""
        global _current_connections
        
        from ... import command_client
        from ... import engine_manager
        
        if not engine_manager.is_engine_running():
            return
        
        print(f"Audio Nodes: Full sync for tree '{self.name}'")
        
        # Get current connections
        new_connections = _get_tree_connections(self)
        
        # Send all connections to engine
        for from_id, to_id, from_port, to_port in new_connections:
            print(f"  Connecting: {from_id} -> {to_id}")
            command_client.connect_nodes(from_id, to_id, from_port, to_port)
        
        # Update tracked state
        _current_connections[self.name] = new_connections
