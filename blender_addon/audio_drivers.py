"""Audio driver namespace - drive ANY Blender property with audio.

Registers functions into bpy.app.driver_namespace so every animatable
property in Blender (shader parameters, compositor values, object
transforms, light energy, shape keys, ...) can use audio in a driver
expression:

    audio_tap("FFT Analyzer", "Lows#61f03ca0")   # one tap, by stable ref
    audio_tap("FFT Analyzer", "Lows#61f03ca0", accumulated=True)  # its count
    audio_channel("peak_left")                   # any raw channel by name

Refresh model: driver expressions have no RNA dependencies Blender can
track, so the 60fps visualization timer calls refresh_driver_dependencies()
each tick. It keeps a (rescanned every ~2s) cache of datablocks whose
drivers mention our functions and calls update_tag() on them, forcing the
dependency graph to re-evaluate those drivers with fresh audio values.

Note: Blender clears the driver namespace when loading a new file, so a
persistent load_post handler re-registers the functions.
"""

import time

import bpy
from bpy.app.handlers import persistent


# ---------------------------------------------------------------------------
# Driver expression functions
# ---------------------------------------------------------------------------

def audio_channel(name, default=0.0):
    """Value of an audio channel by name (peak_left, rms_right, ...)."""
    try:
        for scene in bpy.data.scenes:
            data = getattr(scene, "audio_nodes_data", None)
            if data is None:
                continue
            channel = data.channels.get(name)
            if channel is not None:
                return channel.value
    except Exception:
        pass
    return default


_fft_taps_mod = None


def _taps():
    """Lazily grab the fft_taps module (avoids an import cycle)."""
    global _fft_taps_mod
    if _fft_taps_mod is None:
        from .nodes.effects import fft_taps
        _fft_taps_mod = fft_taps
    return _fft_taps_mod


def audio_tap(node, tap=0, default=0.0, accumulated=False):
    """Live energy of one tap on an FFT Analyzer node.

    BOTH arguments are stable reference strings of the form "Name#id" - the
    Copy Driver Expression button on the tap emits the pair for you:

        audio_tap("FFT Analyzer#7ac41f02", "Lights#61f03ca0")

    A bare node name still resolves, as long as only one analyzer carries
    it, but it breaks the moment you rename the node. A bare integer tap
    still resolves too, but addresses the tap **by position**, and positions
    move: deleting a tap shifts every later tap down one and frees a slot
    the next added tap reuses. Prefer the string forms.

    The value is mapped to the tap's Min/Max output range (0-1 by default).

    accumulated=True reads the tap's Accumulate output instead (the running
    count). It returns `default` while that tap's Accumulate is off. Keep
    it a keyword argument: the positional (node, tap, default) form is
    unchanged for existing drivers.
    """
    try:
        taps_mod = _taps()
        _tree, analyzer = taps_mod.resolve_analyzer(node)
        if analyzer is None:
            return default  # renamed away, deleted, or an ambiguous name
        index = taps_mod.resolve_tap_index(analyzer.taps, tap)
        if index < 0:
            return default  # deleted tap - never re-bind to its slot
        resolved = analyzer.taps[index]
        if resolved.tap_id:
            channel = f"{analyzer.engine_node_id}_tap_{resolved.tap_id}"
            if accumulated:
                if not getattr(resolved, "accumulate", False):
                    return default
                channel += taps_mod.ACC_CHANNEL_SUFFIX
            return audio_channel(channel, default)
    except Exception:
        pass
    return default


# ---------------------------------------------------------------------------
# Dependency refresh
# ---------------------------------------------------------------------------

_driver_users = []      # datablocks whose drivers reference audio functions
_last_scan = 0.0
_SCAN_INTERVAL = 2.0    # seconds between rescans for new/removed drivers


def _id_has_audio_driver(id_block):
    anim = getattr(id_block, "animation_data", None)
    if anim is None:
        return False
    try:
        for fcurve in anim.drivers:
            driver = fcurve.driver
            if driver is not None and driver.type == 'SCRIPTED' \
                    and 'audio_' in (driver.expression or ''):
                return True
    except Exception:
        pass
    return False


def _scan_driver_users():
    """Collect every datablock with a driver that mentions audio_*.

    Shader/compositor drivers live on the embedded node_tree datablock,
    not the material/scene itself, so those are scanned too.
    """
    users = []

    pools = (
        bpy.data.objects, bpy.data.materials, bpy.data.worlds,
        bpy.data.lights, bpy.data.cameras, bpy.data.scenes,
        bpy.data.node_groups, bpy.data.meshes, bpy.data.curves,
        bpy.data.shape_keys, bpy.data.speakers, bpy.data.armatures,
    )
    for pool in pools:
        for id_block in pool:
            if _id_has_audio_driver(id_block):
                users.append(id_block)

    # Embedded node trees (shader editors, compositor)
    for pool in (bpy.data.materials, bpy.data.worlds, bpy.data.lights,
                 bpy.data.scenes):
        for owner in pool:
            tree = getattr(owner, "node_tree", None)
            if tree is not None and _id_has_audio_driver(tree):
                users.append(tree)

    return users


def refresh_driver_dependencies():
    """Called each visualization tick - re-evaluate audio-driven drivers.

    Cheap when nothing uses audio drivers (empty list, rescan every 2s).
    """
    global _driver_users, _last_scan

    now = time.monotonic()
    if now - _last_scan > _SCAN_INTERVAL:
        _last_scan = now
        try:
            _driver_users = _scan_driver_users()
        except Exception:
            _driver_users = []

    for id_block in _driver_users:
        try:
            id_block.update_tag()
        except (ReferenceError, Exception):
            pass  # deleted datablock - next rescan drops it


def has_driver_users():
    return bool(_driver_users)


# ---------------------------------------------------------------------------
# Copy-expression operator (button on tap rows)
# ---------------------------------------------------------------------------

class AUDIONODES_OT_copy_tap_driver(bpy.types.Operator):
    """Copy a driver expression for this tap to the clipboard.

    Paste it anywhere: right-click any property > Add Driver, then paste
    into the expression field.
    """
    bl_idname = "audionodes.copy_tap_driver"
    bl_label = "Copy Driver Expression"

    node_name: bpy.props.StringProperty()
    tap_index: bpy.props.IntProperty(default=0)
    # Stable "Name#id" references. Preferred over the plain name and over
    # tap_index, which are only fallbacks for callers predating stable refs.
    tap_ref: bpy.props.StringProperty(default="")
    node_ref: bpy.props.StringProperty(default="")
    # Set when the tap has Accumulate on: copy the running count instead.
    accumulated: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        ref = self.tap_ref
        if not ref:
            ref = self._lookup_ref()
        node = self.node_ref or self.node_name
        if ref:
            expression = f'audio_tap("{node}", "{ref}"'
        else:
            expression = f'audio_tap("{node}", {self.tap_index}'
        if self.accumulated:
            expression += ', accumulated=True'
        expression += ')'
        context.window_manager.clipboard = expression
        self.report({'INFO'},
                    f"Copied: {expression} - right-click any property > "
                    f"Add Driver > paste as expression")
        return {'FINISHED'}

    def _lookup_ref(self):
        """Resolve tap_index -> stable ref, for callers that only set it."""
        try:
            taps_mod = _taps()
            _tree, node = taps_mod.resolve_analyzer(
                self.node_ref or self.node_name)
            if node is not None and 0 <= self.tap_index < len(node.taps):
                return taps_mod.tap_ref(node.taps[self.tap_index])
        except Exception:
            pass
        return ""


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _install_namespace():
    bpy.app.driver_namespace["audio_tap"] = audio_tap
    bpy.app.driver_namespace["audio_channel"] = audio_channel


@persistent
def _on_load_post(_dummy):
    # Blender clears the driver namespace on file load - reinstall.
    _install_namespace()


def register_driver_namespace():
    _install_namespace()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister_driver_namespace():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    bpy.app.driver_namespace.pop("audio_tap", None)
    bpy.app.driver_namespace.pop("audio_channel", None)
