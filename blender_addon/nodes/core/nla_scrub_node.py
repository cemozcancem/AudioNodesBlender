"""NLA Scrub node - drive an NLA strip's playhead from audio.

Replaces the old Action Output node. Instead of evaluating an action's
F-Curves in Python and writing every channel onto the target, this node
writes **one float** - the strip's `strip_time` - and lets Blender's own
NLA system evaluate the pose in C. On a 468-channel Mixamo rig that is
0.03 ms per tick instead of 2.57 ms, with correct quaternion blending and
NLA layering for free.

The Blender timeline is still never used: `scene.frame_current` is not
touched and nothing plays back.

## The one non-obvious part

`strip.strip_time` looks like the knob, but assigning to it does nothing.
When `use_animated_time` is on, Blender recomputes `strip_time` during
animation evaluation from the strip's own F-Curve and discards whatever
you wrote. A *driver* on `strip_time` is ignored for the same reason.

The value that actually matters is the keyframe inside that strip F-Curve:

    strip.fcurves['strip_time'].keyframe_points[0].co[1]

The F-Curve is evaluated at the scene frame, so with a single keyframe it
is effectively a constant - and writing that constant is what moves the
rig. That is what this node does every tick.

## Scope

Scrub only. The playhead is wherever the Time input says it is. There is
no transport - no one-shot triggering, no voices, no loop modes - because
that needs per-frame state this design deliberately does not carry.
"""

import uuid

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
    StringProperty,
)

from .node_base import AudioNodeBase, find_node


# Blender does not retain the strings a dynamic EnumProperty callback
# returns; building them inline gets them garbage collected and the UI
# shows corrupted labels. Cache them module-side.
_track_enum_cache = []
_strip_enum_cache = []


def _tracks_of(node):
    obj = node.target_object
    ad = getattr(obj, "animation_data", None) if obj else None
    return list(ad.nla_tracks) if ad else []


def _track_items(self, context):
    global _track_enum_cache
    items = [(t.name, t.name, "NLA track" + (" (muted)" if t.mute else ""))
             for t in _tracks_of(self)]
    _track_enum_cache = items or [('', "No NLA tracks", "")]
    return _track_enum_cache


def _strip_items(self, context):
    global _strip_enum_cache
    items = []
    for track in _tracks_of(self):
        if track.name != self.track_name:
            continue
        for strip in track.strips:
            label = strip.action.name if strip.action else strip.name
            items.append((strip.name, strip.name, label))
    _strip_enum_cache = items or [('', "No strips", "")]
    return _strip_enum_cache


def resolve_strip(node):
    """The NlaStrip this node drives, or None."""
    for track in _tracks_of(node):
        if track.name != node.track_name:
            continue
        for strip in track.strips:
            if strip.name == node.strip_name:
                return track, strip
    return None, None


def strip_time_fcurve(strip):
    for fcurve in strip.fcurves:
        if fcurve.data_path == "strip_time":
            return fcurve
    return None


def ensure_animated_time(strip):
    """Turn on animated time and make sure a strip_time key exists.

    Returns (fcurve, message). Enabling `use_animated_time` normally
    creates the F-Curve; if it does not, a keyframe_insert on the strip
    forces one into existence.
    """
    if not strip.use_animated_time:
        strip.use_animated_time = True

    fcurve = strip_time_fcurve(strip)
    if fcurve is None:
        try:
            strip.keyframe_insert("strip_time", frame=0)
        except Exception as exc:
            return None, f"could not create strip_time F-Curve: {exc}"
        fcurve = strip_time_fcurve(strip)

    if fcurve is None:
        return None, "strip has no strip_time F-Curve"
    if not fcurve.keyframe_points:
        fcurve.keyframe_points.insert(0.0, strip.action_frame_start)
    return fcurve, ""


def write_playhead(strip, fcurve, frame):
    """Set the single strip_time keyframe - this is what moves the rig."""
    key = fcurve.keyframe_points[0]
    key.co[1] = frame
    key.handle_left[1] = frame        # keep handles flat or the curve
    key.handle_right[1] = frame       # bends away from the value we set
    fcurve.update()


def strip_range(node, strip):
    if node.use_custom_range:
        lo, hi = float(node.frame_start), float(node.frame_end)
    else:
        lo, hi = float(strip.action_frame_start), float(strip.action_frame_end)
    if abs(hi - lo) < 1e-6:
        hi = lo + 1.0
    return lo, hi


# ---------------------------------------------------------------------------
# Per-tick update - called from the 60fps timer in shared_memory_reader
# ---------------------------------------------------------------------------

_smoothed = {}


def update_nla_scrub_nodes(audio_data, resolve_input):
    """Drive every NLA Scrub node. `resolve_input(node, socket, default)`."""
    touched = set()

    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            if node.bl_idname != 'AudioNLAScrubNode':
                continue

            node_id = node.engine_node_id
            if node.mute or node.target_object is None:
                _smoothed.pop(node_id, None)
                _publish(audio_data, node_id + "_position", 0.0)
                continue

            track, strip = resolve_strip(node)
            if strip is None:
                _publish(audio_data, node_id + "_position", 0.0)
                continue

            fcurve = strip_time_fcurve(strip)
            if fcurve is None or not fcurve.keyframe_points:
                # Not set up yet - the node draws a Set Up button for this.
                _publish(audio_data, node_id + "_position", 0.0)
                continue

            t = max(0.0, min(1.0, resolve_input(node, "Time", 0.0)))
            smoothing = max(0.0, min(0.99, node.smoothing))
            if smoothing > 0.0:
                prev = _smoothed.get(node_id, t)
                t = prev * smoothing + t * (1.0 - smoothing)
            _smoothed[node_id] = t

            lo, hi = strip_range(node, strip)
            write_playhead(strip, fcurve, lo + (hi - lo) * t)

            if node.drive_influence:
                influence = max(0.0, min(1.0, resolve_input(node, "Influence", 1.0)))
                if not strip.use_animated_influence:
                    strip.use_animated_influence = True
                strip.influence = influence * node.influence

            touched.add(node.target_object)
            _publish(audio_data, node_id + "_position", t)

    for obj in touched:
        try:
            obj.update_tag()
        except Exception:
            pass


def _publish(audio_data, name, value):
    if name not in audio_data.channels:
        channel = audio_data.channels.add()
        channel.name = name
    audio_data.channels[name].value = value


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class AudioNLAScrubNode(AudioNodeBase):
    """Scrub an NLA strip's playhead from an audio control signal."""

    bl_idname = "AudioNLAScrubNode"
    bl_label = "NLA Scrub"
    bl_icon = 'NLA'
    bl_width_default = 230

    target_object: PointerProperty(
        name="Target",
        description="Object whose NLA strip is driven",
        type=bpy.types.Object,
    )

    track_name: EnumProperty(
        name="Track", description="NLA track on the target", items=_track_items)

    strip_name: EnumProperty(
        name="Strip", description="Strip within that track", items=_strip_items)

    use_custom_range: BoolProperty(
        name="Custom Range",
        description="Override the strip's action frame range",
        default=False)

    frame_start: FloatProperty(name="Start", default=1.0)
    frame_end: FloatProperty(name="End", default=100.0)

    smoothing: FloatProperty(
        name="Smoothing",
        description="Temporal smoothing on the playhead. This is the speed "
                    "control - higher values make the scrub glide instead of "
                    "snapping to the input",
        default=0.5, min=0.0, max=0.99, subtype='FACTOR')

    drive_influence: BoolProperty(
        name="Drive Influence",
        description="Also write the strip's influence from the Influence input",
        default=False)

    influence: FloatProperty(
        name="Influence", default=1.0, min=0.0, max=1.0, subtype='FACTOR')

    @property
    def node_type(self) -> str:
        return "nla_scrub"

    def init(self, context):
        self.inputs.new('ControlSocketType', "Time")
        influence = self.inputs.new('ControlSocketType', "Influence")
        influence.default_value = 1.0
        self.outputs.new('ControlSocketType', "Position")
        self.engine_node_id = f"nla_scrub_{uuid.uuid4().hex[:8]}"

    def copy(self, node):
        self.engine_node_id = f"nla_scrub_{uuid.uuid4().hex[:8]}"

    # Blender-only node - the C++ engine never sees it.
    def register_with_engine(self):
        pass

    def unregister_from_engine(self):
        pass

    def get_output_channel_name(self, socket_name: str) -> str:
        return f"{self.engine_node_id}_{socket_name.lower()}"

    def draw_buttons(self, context, layout):
        layout.prop(self, "target_object", text="")
        if self.target_object is None:
            return

        ad = getattr(self.target_object, "animation_data", None)
        if ad is None or not ad.nla_tracks:
            box = layout.box()
            box.alert = True
            box.scale_y = 0.85
            box.label(text="No NLA tracks on target", icon='ERROR')
            return

        layout.prop(self, "track_name", text="")
        layout.prop(self, "strip_name", text="")

        track, strip = resolve_strip(self)
        if strip is None:
            return

        if track.mute:
            box = layout.box()
            box.alert = True
            box.scale_y = 0.85
            box.label(text="Track is muted", icon='ERROR')

        fcurve = strip_time_fcurve(strip)
        if not strip.use_animated_time or fcurve is None or not fcurve.keyframe_points:
            box = layout.box()
            box.scale_y = 0.9
            box.label(text="Strip not set up for scrubbing", icon='INFO')
            op = box.operator("audionodes.nla_setup", text="Set Up Strip", icon='CHECKMARK')
            op.node_name = self.name
            op.tree_name = self.id_data.name
            return

        layout.prop(self, "smoothing")

        row = layout.row(align=True)
        row.prop(self, "use_custom_range", text="", icon='TIME')
        sub = row.row(align=True)
        sub.enabled = self.use_custom_range
        sub.prop(self, "frame_start", text="")
        sub.prop(self, "frame_end", text="")

        row = layout.row(align=True)
        row.prop(self, "drive_influence", text="")
        sub = row.row(align=True)
        sub.enabled = self.drive_influence
        sub.prop(self, "influence", text="Influence")

        lo, hi = strip_range(self, strip)
        position = 0.0
        try:
            data = context.scene.audio_nodes_data
            name = f"{self.engine_node_id}_position"
            if name in data.channels:
                position = data.channels[name].value
        except Exception:
            pass

        box = layout.box()
        box.scale_y = 0.8
        if hasattr(box, "progress"):
            box.progress(factor=max(0.0, min(1.0, position)), type='BAR',
                         text=f"frame {lo + (hi - lo) * position:.1f}")
        else:
            box.label(text=f"Position: {position:.3f}")
        box.label(text=f"range {lo:.0f}-{hi:.0f}  ·  blend {strip.blend_type.title()}")


class AUDIONODES_OT_nla_setup(bpy.types.Operator):
    """Enable animated time on this strip and create its strip_time keyframe"""

    bl_idname = "audionodes.nla_setup"
    bl_label = "Set Up Strip For Scrubbing"
    bl_options = {'INTERNAL', 'UNDO'}

    node_name: StringProperty()
    tree_name: StringProperty()

    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioNLAScrubNode',
                         tree_name=self.tree_name)
        if node is None:
            return {'CANCELLED'}

        track, strip = resolve_strip(node)
        if strip is None:
            self.report({'WARNING'}, "Pick a track and strip first")
            return {'CANCELLED'}

        fcurve, message = ensure_animated_time(strip)
        if fcurve is None:
            self.report({'WARNING'}, message)
            return {'CANCELLED'}

        if track.mute:
            track.mute = False
        self.report({'INFO'}, f"'{strip.name}' ready to scrub")
        return {'FINISHED'}
