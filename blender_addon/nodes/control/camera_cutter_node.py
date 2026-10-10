"""Camera Cutter node - cut between cameras and play their moves to the music.

You build a list of shots. Each shot is a camera, plus an optional extra
object that moves with it (typically the empty a Track To constraint aims
at). Every tick the node:

  * pushes the current shot's Progress along, faster when the Speed input
    is higher (Speed = kick + bass -> the move surges with the music),
  * cuts to the next shot on a rising edge of the Cut input (a Trigger
    node on the kick), but never before Min Shot seconds; with Auto
    Advance on it also cuts when a shot reaches its end,
  * makes that shot's camera the scene camera,
  * scrubs the camera's own keyframed move (and the extra object's) to
    Progress, using the same NLA strip_time trick as the NLA Scrub node -
    so a shot is authored with ordinary keyframes and played back at the
    speed of the music.

Outputs: Progress (0-1), Shot (index in the list), Cut (short pulse on
every cut - feed a Decay node for a cut flash) and Time (seconds in shot).

Record bakes what the node does into a separate "baked" camera at the
scene frame rate while the track plays, so a live performance can be
rendered frame by frame afterwards.
"""

import math
import time

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..core.node_base import find_node
from ..core import nla_scrub_node
from . import control_logic
from .control_nodes import ControlNodeBase, publish, read_channel, _STATE


TRACK_NAME = "Audio Nodes Shot"


def _poll_camera(self, obj):
    return obj.type == 'CAMERA'


class AudioCameraShot(bpy.types.PropertyGroup):
    """One shot of a Camera Cutter."""

    # `name` (built in) is the label shown in the list
    enabled: BoolProperty(name="Enabled", default=True)
    camera: PointerProperty(
        name="Camera", type=bpy.types.Object, poll=_poll_camera,
        description="Camera for this shot (becomes the scene camera)")
    extra_object: PointerProperty(
        name="Also Scrub", type=bpy.types.Object,
        description="Optional object whose keyframed move plays along with "
                    "the camera's - e.g. the empty a Track To constraint aims at")
    use_custom_range: BoolProperty(
        name="Custom Range", default=False,
        description="Play only this frame range of the shot's animation")
    frame_start: FloatProperty(name="Start", default=1.0)
    frame_end: FloatProperty(name="End", default=100.0)
    duration_scale: FloatProperty(
        name="Length", default=1.0, min=0.1, soft_max=4.0,
        description="Multiplies the node's Shot Length for this shot")


class AUDIONODES_UL_camera_shots(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        live = False
        try:
            live = int(read_channel(context, data.get_output_channel_name("Shot"), -1)) == index
        except Exception:
            pass
        row.label(text="", icon='PLAY' if live else 'BLANK1')
        row.prop(item, "camera", text="")


# ---------------------------------------------------------------------------
# NLA helpers (shared with the NLA Scrub node's approach)
# ---------------------------------------------------------------------------

def _shot_strip(obj):
    ad = getattr(obj, "animation_data", None)
    if ad is None:
        return None
    track = ad.nla_tracks.get(TRACK_NAME)
    if track is None or not len(track.strips):
        return None
    return track.strips[0]


def needs_setup(obj):
    """True when obj has keyframes that are not yet prepared for scrubbing."""
    if obj is None:
        return False
    ad = getattr(obj, "animation_data", None)
    if ad is None:
        return False
    strip = _shot_strip(obj)
    if strip is not None:
        fc = nla_scrub_node.strip_time_fcurve(strip)
        return not (strip.use_animated_time and fc and len(fc.keyframe_points))
    return ad.action is not None


def prepare_object(obj):
    """Move obj's active action onto a scrub-ready NLA strip.

    Returns a message ('' on success or nothing to do).
    """
    ad = getattr(obj, "animation_data", None)
    if ad is None:
        return ""
    strip = _shot_strip(obj)
    if strip is None:
        action = ad.action
        if action is None:
            return ""
        slot = getattr(ad, "action_slot", None)
        track = ad.nla_tracks.new()
        track.name = TRACK_NAME
        start = int(math.floor(action.frame_range[0]))
        strip = track.strips.new(action.name, start, action)
        if slot is not None and hasattr(strip, "action_slot"):
            try:
                strip.action_slot = slot
            except Exception:
                pass
        strip.extrapolation = 'HOLD'
        strip.blend_type = 'REPLACE'
        ad.action = None
    fcurve, message = nla_scrub_node.ensure_animated_time(strip)
    track = ad.nla_tracks.get(TRACK_NAME)
    if track is not None and track.mute:
        track.mute = False
    return message


def _scrub(obj, progress, shot):
    strip = _shot_strip(obj)
    if strip is None:
        return
    fcurve = nla_scrub_node.strip_time_fcurve(strip)
    if fcurve is None or not len(fcurve.keyframe_points):
        return
    if shot.use_custom_range:
        lo, hi = float(shot.frame_start), float(shot.frame_end)
    else:
        lo, hi = float(strip.action_frame_start), float(strip.action_frame_end)
    frame = lo + (hi - lo) * progress
    if abs(fcurve.keyframe_points[0].co[1] - frame) > 1e-5:
        nla_scrub_node.write_playhead(strip, fcurve, frame)
        obj.update_tag(refresh={'TIME'})


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

class _Recorder:
    def __init__(self, node, scene):
        self.t0 = time.monotonic()
        self.frame0 = float(scene.frame_start)
        self.fps = scene.render.fps / (scene.render.fps_base or 1.0)
        self.cut_frames = []
        self.last_frame = self.frame0
        self.prev_euler = None
        self.cuts_seen = None
        self.target = node.baked_camera

    def frame_now(self):
        return self.frame0 + (time.monotonic() - self.t0) * self.fps


def _baked_camera_for(node, scene, template):
    obj = node.baked_camera
    if obj is None or obj.type != 'CAMERA':
        data = bpy.data.cameras.new(f"{node.name} Baked")
        obj = bpy.data.objects.new(f"{node.name} Baked", data)
        scene.collection.objects.link(obj)
        node.baked_camera = obj
    if obj.animation_data is not None:
        obj.animation_data_clear()
    if obj.data.animation_data is not None:
        obj.data.animation_data_clear()
    if template is not None:
        src = template.data
        dst = obj.data
        for attr in ("sensor_width", "sensor_height", "sensor_fit",
                     "clip_start", "clip_end", "type"):
            try:
                setattr(dst, attr, getattr(src, attr))
            except Exception:
                pass
        dst.dof.use_dof = src.dof.use_dof
    return obj


def _record_tick(node, rec, shot_cam, cut_now):
    obj = rec.target
    if obj is None or shot_cam is None:
        return
    frame = rec.frame_now()
    if cut_now:
        rec.cut_frames.append(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    src = shot_cam.evaluated_get(depsgraph)
    loc, rot, _scale = src.matrix_world.decompose()
    euler = rot.to_euler('XYZ', rec.prev_euler) if rec.prev_euler else rot.to_euler('XYZ')
    rec.prev_euler = euler
    obj.location = loc
    obj.rotation_euler = euler
    obj.keyframe_insert("location", frame=frame)
    obj.keyframe_insert("rotation_euler", frame=frame)

    cam = obj.data
    scam = src.data
    cam.lens = scam.lens
    cam.keyframe_insert("lens", frame=frame)
    if scam.dof.use_dof:
        focus = scam.dof.focus_distance
        fobj = scam.dof.focus_object
        if fobj is not None:
            focus = (fobj.evaluated_get(depsgraph).matrix_world.translation - loc).length
        cam.dof.focus_distance = focus
        cam.dof.aperture_fstop = scam.dof.aperture_fstop
        cam.keyframe_insert("dof.focus_distance", frame=frame)
        cam.keyframe_insert("dof.aperture_fstop", frame=frame)
    rec.last_frame = frame


def _fcurves_of(idblock):
    ad = getattr(idblock, "animation_data", None)
    action = getattr(ad, "action", None) if ad else None
    if action is None:
        return []
    try:
        from bpy_extras import anim_utils
        bag = anim_utils.action_get_channelbag_for_slot(action, ad.action_slot)
        if bag is not None:
            return list(bag.fcurves)
    except Exception:
        pass
    return list(getattr(action, "fcurves", []))


def _finish_recording(node, rec, scene):
    """Straight lines within a shot, hard steps at every cut."""
    obj = rec.target
    if obj is None:
        return 0
    cuts = sorted(rec.cut_frames)
    keys = 0
    for idb in (obj, obj.data):
        for fc in _fcurves_of(idb):
            points = fc.keyframe_points
            n = len(points)
            keys = max(keys, n)
            for i, kp in enumerate(points):
                kp.interpolation = 'LINEAR'
                if i + 1 < n:
                    a, b = kp.co[0], points[i + 1].co[0]
                    if any(a < c <= b for c in cuts):
                        kp.interpolation = 'CONSTANT'
            fc.update()
    scene.frame_end = max(scene.frame_start + 1, int(math.ceil(rec.last_frame)))
    return keys


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class AudioCameraCutterNode(ControlNodeBase):
    """Cut between cameras on the beat and play their moves at the music's speed."""

    bl_idname = "AudioCameraCutterNode"
    bl_label = "Camera Cutter"
    bl_icon = 'VIEW_CAMERA'
    bl_width_default = 260

    shots: CollectionProperty(type=AudioCameraShot)
    active_shot_index: IntProperty(default=0)

    order: EnumProperty(
        name="Order",
        items=[
            ('SEQUENCE', "In Order", "Play the shots top to bottom, then loop"),
            ('SHUFFLE', "Shuffle", "Every shot once per round, in random order"),
            ('RANDOM', "Random", "Any shot except the current one"),
        ],
        default='SEQUENCE')
    shot_length: FloatProperty(
        name="Shot Length", default=4.0, min=0.1, soft_max=20.0,
        subtype='TIME_ABSOLUTE',
        description="Seconds a shot's move takes when Speed is 1")
    base_speed: FloatProperty(
        name="Base Speed", default=0.4, min=0.0, soft_max=2.0,
        description="Speed when the Speed input is 0 (so quiet parts still move)")
    speed_gain: FloatProperty(
        name="Speed Gain", default=1.2, soft_min=0.0, soft_max=4.0,
        description="How much the Speed input adds")
    min_shot: FloatProperty(
        name="Min Shot", default=1.5, min=0.0, soft_max=10.0,
        subtype='TIME_ABSOLUTE',
        description="A Cut is ignored until the current shot has run this long")
    auto_advance: BoolProperty(
        name="Auto Advance", default=True,
        description="Cut to the next shot when a move reaches its end "
                    "(off: hold the last frame until the next Cut)")
    set_scene_camera: BoolProperty(
        name="Switch Scene Camera", default=True,
        description="Make the current shot's camera the scene camera")
    scrub_animation: BoolProperty(
        name="Play Camera Moves", default=True,
        description="Scrub each shot's keyframed move to Progress")

    baked_camera: PointerProperty(
        name="Baked Camera", type=bpy.types.Object,
        description="Camera the last recording was baked into")
    is_recording: BoolProperty(default=False)

    @property
    def node_type(self) -> str:
        return "camera_cutter"

    def build_sockets(self):
        self.inputs.new('ControlSocketType', "Speed")
        self.inputs.new('ControlSocketType', "Cut")
        self.outputs.new('ControlSocketType', "Progress")
        self.outputs.new('ControlSocketType', "Shot")
        self.outputs.new('ControlSocketType', "Cut")
        self.outputs.new('ControlSocketType', "Time")

    def free(self):
        _STATE.pop(self.engine_node_id + "_rec", None)
        super().free()

    # -- UI -----------------------------------------------------------------
    def _op(self, layout, idname, text, icon, **props):
        op = layout.operator(idname, text=text, icon=icon)
        op.node_name = self.name
        op.tree_name = self.id_data.name
        for k, v in props.items():
            setattr(op, k, v)
        return op

    def draw_buttons(self, context, layout):
        row = layout.row()
        row.template_list("AUDIONODES_UL_camera_shots", "", self, "shots",
                          self, "active_shot_index", rows=3)
        col = row.column(align=True)
        self._op(col, "audionodes.camera_cutter_add_shot", "", 'ADD')
        self._op(col, "audionodes.camera_cutter_remove_shot", "", 'REMOVE')
        col.separator()
        self._op(col, "audionodes.camera_cutter_move_shot", "", 'TRIA_UP', direction=-1)
        self._op(col, "audionodes.camera_cutter_move_shot", "", 'TRIA_DOWN', direction=1)

        if 0 <= self.active_shot_index < len(self.shots):
            shot = self.shots[self.active_shot_index]
            box = layout.box()
            box.prop(shot, "name", text="")
            box.prop(shot, "camera")
            box.prop(shot, "extra_object")
            box.prop(shot, "duration_scale")
            r = box.row(align=True)
            r.prop(shot, "use_custom_range", text="", icon='TIME')
            sub = r.row(align=True)
            sub.enabled = shot.use_custom_range
            sub.prop(shot, "frame_start", text="")
            sub.prop(shot, "frame_end", text="")

        if any(needs_setup(s.camera) or needs_setup(s.extra_object) for s in self.shots):
            box = layout.box()
            box.alert = True
            box.label(text="Some shots have moves not set up", icon='INFO')
            self._op(box, "audionodes.camera_cutter_setup", "Set Up Shots", 'CHECKMARK')

        layout.prop(self, "order", text="")
        col = layout.column(align=True)
        col.prop(self, "shot_length")
        col.prop(self, "base_speed")
        col.prop(self, "speed_gain")
        col.prop(self, "min_shot")
        col = layout.column(align=True)
        col.prop(self, "auto_advance")
        col.prop(self, "set_scene_camera")
        col.prop(self, "scrub_animation")

        self.draw_level(context, layout, "Progress")
        row = layout.row(align=True)
        self._op(row, "audionodes.camera_cutter_next", "Cut Now", 'FORWARD')
        if self.is_recording:
            r = row.row(align=True)
            r.alert = True
            self._op(r, "audionodes.camera_cutter_record", "Stop", 'SNAP_FACE')
        else:
            self._op(row, "audionodes.camera_cutter_record", "Record", 'REC')
        if self.baked_camera is not None and not self.is_recording:
            row = layout.row(align=True)
            row.prop(self, "baked_camera", text="")
            self._op(row, "audionodes.camera_cutter_use_baked", "", 'RESTRICT_RENDER_OFF')

    # -- evaluation ---------------------------------------------------------
    def enabled_shots(self):
        return [(i, s) for i, s in enumerate(self.shots) if s.enabled and s.camera]

    def evaluate(self, audio_data, resolve, dt):
        logic = self.state(control_logic.CutterLogic)
        shots = self.enabled_shots()
        n = len(shots)
        duration = self.shot_length
        if n:
            idx = min(logic.slot, n - 1)
            duration *= shots[idx][1].duration_scale
        slot, progress, pulse, t = logic.step(
            dt, n, resolve(self, "Speed", 0.0), resolve(self, "Cut", 0.0),
            duration, self.base_speed, self.speed_gain, self.min_shot,
            self.order, self.auto_advance)

        shot_index = -1
        shot_cam = None
        if n:
            shot_index, shot = shots[slot]
            shot_cam = shot.camera
            scene = bpy.context.scene
            if self.set_scene_camera and scene.camera != shot_cam:
                scene.camera = shot_cam
            if self.scrub_animation:
                _scrub(shot.camera, progress, shot)
                if shot.extra_object is not None:
                    _scrub(shot.extra_object, progress, shot)

        publish(audio_data, self.get_output_channel_name("Progress"), progress)
        publish(audio_data, self.get_output_channel_name("Shot"), float(shot_index))
        publish(audio_data, self.get_output_channel_name("Cut"), pulse)
        publish(audio_data, self.get_output_channel_name("Time"), t)

        rec = _STATE.get(self.engine_node_id + "_rec")
        if rec is not None and self.is_recording:
            cut_now = rec.cuts_seen is not None and logic.cuts != rec.cuts_seen
            rec.cuts_seen = logic.cuts
            _record_tick(self, rec, shot_cam, cut_now)

    def manual_cut(self):
        logic = self.state(control_logic.CutterLogic)
        n = len(self.enabled_shots())
        if n:
            logic.force_cut(n, self.order)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class _CutterOp:
    bl_options = {'REGISTER', 'UNDO'}
    node_name: StringProperty()
    tree_name: StringProperty()

    def node(self, context):
        return find_node(context, self.node_name,
                         bl_idname='AudioCameraCutterNode',
                         tree_name=self.tree_name)


class AUDIONODES_OT_camera_cutter_add_shot(_CutterOp, bpy.types.Operator):
    """Add a shot. Selected cameras are each added as their own shot"""

    bl_idname = "audionodes.camera_cutter_add_shot"
    bl_label = "Add Shot"

    def execute(self, context):
        node = self.node(context)
        if node is None:
            return {'CANCELLED'}
        cams = [o for o in getattr(context, "selected_objects", []) or []
                if o.type == 'CAMERA']
        existing = {s.camera for s in node.shots}
        cams = [c for c in cams if c not in existing]
        if not cams:
            shot = node.shots.add()
            shot.name = f"Shot {len(node.shots)}"
        for cam in sorted(cams, key=lambda o: o.name):
            shot = node.shots.add()
            shot.camera = cam
            shot.name = cam.name
            prepare_object(cam)
        node.active_shot_index = len(node.shots) - 1
        return {'FINISHED'}


class AUDIONODES_OT_camera_cutter_remove_shot(_CutterOp, bpy.types.Operator):
    """Remove the selected shot"""

    bl_idname = "audionodes.camera_cutter_remove_shot"
    bl_label = "Remove Shot"

    def execute(self, context):
        node = self.node(context)
        if node is None or not (0 <= node.active_shot_index < len(node.shots)):
            return {'CANCELLED'}
        node.shots.remove(node.active_shot_index)
        node.active_shot_index = max(0, min(node.active_shot_index, len(node.shots) - 1))
        return {'FINISHED'}


class AUDIONODES_OT_camera_cutter_move_shot(_CutterOp, bpy.types.Operator):
    """Move the selected shot up or down"""

    bl_idname = "audionodes.camera_cutter_move_shot"
    bl_label = "Move Shot"
    direction: IntProperty(default=1)

    def execute(self, context):
        node = self.node(context)
        if node is None:
            return {'CANCELLED'}
        i = node.active_shot_index
        j = i + self.direction
        if 0 <= i < len(node.shots) and 0 <= j < len(node.shots):
            node.shots.move(i, j)
            node.active_shot_index = j
        return {'FINISHED'}


class AUDIONODES_OT_camera_cutter_setup(_CutterOp, bpy.types.Operator):
    """Put every shot's keyframed move on an NLA strip the node can scrub"""

    bl_idname = "audionodes.camera_cutter_setup"
    bl_label = "Set Up Shots"

    def execute(self, context):
        node = self.node(context)
        if node is None:
            return {'CANCELLED'}
        problems = []
        for shot in node.shots:
            for obj in (shot.camera, shot.extra_object):
                if obj is not None:
                    msg = prepare_object(obj)
                    if msg:
                        problems.append(f"{obj.name}: {msg}")
        if problems:
            self.report({'WARNING'}, "; ".join(problems))
        else:
            self.report({'INFO'}, "Shots ready")
        return {'FINISHED'}


class AUDIONODES_OT_camera_cutter_next(_CutterOp, bpy.types.Operator):
    """Cut to the next shot now"""

    bl_idname = "audionodes.camera_cutter_next"
    bl_label = "Cut Now"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        node = self.node(context)
        if node is None:
            return {'CANCELLED'}
        node.manual_cut()
        return {'FINISHED'}


class AUDIONODES_OT_camera_cutter_record(_CutterOp, bpy.types.Operator):
    """Start / stop baking the cut into a camera you can render.

    Start the track playing, press Record, press Stop when it ends. The
    baked camera gets one key per tick, straight moves within a shot and
    hard cuts between shots, starting at the scene's start frame
    """

    bl_idname = "audionodes.camera_cutter_record"
    bl_label = "Record Camera Cut"

    def execute(self, context):
        node = self.node(context)
        if node is None:
            return {'CANCELLED'}
        key = node.engine_node_id + "_rec"
        scene = context.scene
        if not node.is_recording:
            shots = node.enabled_shots()
            if not shots:
                self.report({'WARNING'}, "Add at least one shot with a camera")
                return {'CANCELLED'}
            _baked_camera_for(node, scene, shots[0][1].camera)
            _STATE[key] = _Recorder(node, scene)
            node.is_recording = True
            self.report({'INFO'}, f"Recording into '{node.baked_camera.name}'")
            return {'FINISHED'}

        rec = _STATE.pop(key, None)
        node.is_recording = False
        if rec is not None:
            keys = _finish_recording(node, rec, scene)
            secs = (rec.last_frame - rec.frame0) / rec.fps
            self.report({'INFO'}, f"Baked {secs:.1f}s ({keys} keys) into "
                                  f"'{rec.target.name}', frames "
                                  f"{scene.frame_start}-{scene.frame_end}")
        return {'FINISHED'}


class AUDIONODES_OT_camera_cutter_use_baked(_CutterOp, bpy.types.Operator):
    """Render with the baked camera: make it the scene camera and stop live switching"""

    bl_idname = "audionodes.camera_cutter_use_baked"
    bl_label = "Use Baked Camera"

    def execute(self, context):
        node = self.node(context)
        if node is None or node.baked_camera is None:
            return {'CANCELLED'}
        node.set_scene_camera = False
        context.scene.camera = node.baked_camera
        self.report({'INFO'}, f"Scene camera: {node.baked_camera.name} "
                              f"(live switching off)")
        return {'FINISHED'}
