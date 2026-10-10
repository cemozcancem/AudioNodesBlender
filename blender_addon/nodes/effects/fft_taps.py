"""Frequency Taps - user-defined spectrum capture bands for the FFT Analyzer.

A "tap" is a center frequency + bandwidth the user picks on the analyzer's
live spectrum. Each tap becomes a control output socket on the analyzer
node whose value is the band energy, computed per frame from the full
1024-bin FFT in shared memory. Everything is Blender-side Python - the
C++ engine is untouched.
"""

import math
import uuid

import bpy
from bpy.props import (
    BoolProperty,
    FloatProperty,
    FloatVectorProperty,
    StringProperty,
)

# Distinct, readable-on-dark colors cycled for new taps (RGBA).
TAP_COLORS = [
    (0.85, 0.35, 0.19, 1.0),   # coral
    (0.50, 0.47, 0.87, 1.0),   # purple
    (0.24, 0.72, 0.55, 1.0),   # teal
    (0.93, 0.69, 0.29, 1.0),   # amber
    (0.83, 0.33, 0.49, 1.0),   # pink
    (0.34, 0.62, 0.89, 1.0),   # blue
    (0.59, 0.77, 0.35, 1.0),   # green
    (0.72, 0.72, 0.72, 1.0),   # gray
]

FREQ_MIN = 20.0
FREQ_MAX = 20000.0

# Widest allowed tap band, in octaves. 4.0 covers the widest legacy band
# (Mid, 250-4000 Hz) so pre-4.2 files can migrate losslessly.
WIDTH_OCT_MAX = 4.0

# Control-input sockets created per tap on the FFT Analyzer. Linked
# cables drive the param in absolute units (clamped to min/max). LFO
# 0..1 is mapped onto that range in the shared-memory reader; MIDI CC
# should set Min/Max to the desired parameter range.
TAP_CONTROL_PARAMS = (
    ("center_hz", "Hz", FREQ_MIN, FREQ_MAX),
    ("width_oct", "Oct", 0.05, WIDTH_OCT_MAX),
    ("gain", "Gain", 0.1, 50.0),
    ("smoothing", "Smooth", 0.0, 0.99),
    ("out_min", "Min", -1000.0, 1000.0),
    ("out_max", "Max", -1000.0, 1000.0),
    ("trigger_probability", "Prob", 0.0, 1.0),
)

TAP_CONTROL_PARAM_KEYS = {p[0] for p in TAP_CONTROL_PARAMS}


def map_control_to_param(control_01: float, lo: float, hi: float) -> float:
    """Map a 0..1 control signal onto [lo, hi]."""
    t = max(0.0, min(1.0, float(control_01)))
    return lo + (hi - lo) * t


def effective_tap_attr(tap, prop: str, control_value) -> float:
    """Return tap.prop, or a linked control value clamped to the param range.

    Linked values are absolute (parameter units). MIDI CC Min/Max should be
    set to the range you want (e.g. Gain 0.1..50, Hz 20..20000). LFO 0..1
    signals are pre-mapped in the reader before they reach here.
    """
    if control_value is None:
        return float(getattr(tap, prop))
    for key, _label, lo, hi in TAP_CONTROL_PARAMS:
        if key == prop:
            return max(lo, min(hi, float(control_value)))
    return float(getattr(tap, prop))


def tap_display_name(taps, tap) -> str:
    """Socket label base for a tap. Disambiguate duplicate 'Tap' names."""
    name = (tap.name or "Tap").strip() or "Tap"
    if name != "Tap":
        return name
    # Count how many taps share this exact name; if >1 use Tap N by index.
    same = [t for t in taps if (t.name or "Tap").strip() == "Tap"]
    if len(same) <= 1:
        return "Tap"
    for i, t in enumerate(taps):
        if t.tap_id == tap.tap_id:
            return f"Tap {i + 1}"
    return name

def freq_to_norm(freq: float) -> float:
    """Map a frequency to 0-1 on the log display axis."""
    freq = max(FREQ_MIN, min(FREQ_MAX, freq))
    return math.log(freq / FREQ_MIN) / math.log(FREQ_MAX / FREQ_MIN)


def norm_to_freq(t: float) -> float:
    """Inverse of freq_to_norm."""
    t = max(0.0, min(1.0, t))
    return FREQ_MIN * math.exp(t * math.log(FREQ_MAX / FREQ_MIN))


def tap_freq_range(center_hz: float, width_oct: float):
    """Return (low_hz, high_hz) for a tap band."""
    half = 2.0 ** (width_oct * 0.5)
    return (max(FREQ_MIN, center_hz / half), min(FREQ_MAX, center_hz * half))


def tap_bin_range(center_hz: float, width_oct: float, sample_rate: float,
                  num_bins: int = 1024):
    """Return inclusive (lo_bin, hi_bin) FFT bin indices for a tap band.

    Bin resolution matches the engine: sample_rate / (2 * num_bins)
    (real FFT of a 2*num_bins window).
    """
    if sample_rate <= 0:
        sample_rate = 44100.0
    bin_hz = sample_rate / (2.0 * num_bins)
    low_hz, high_hz = tap_freq_range(center_hz, width_oct)
    lo = max(0, min(num_bins - 1, int(low_hz / bin_hz)))
    hi = max(lo, min(num_bins - 1, int(math.ceil(high_hz / bin_hz))))
    return lo, hi


def tap_band_energy(fft_data, center_hz: float, width_oct: float,
                    sample_rate: float) -> float:
    """Average magnitude of a tap band over one FFT frame (tuple of bins)."""
    lo, hi = tap_bin_range(center_hz, width_oct, sample_rate, len(fft_data))
    total = 0.0
    for i in range(lo, hi + 1):
        total += fft_data[i]
    return total / (hi - lo + 1)


def new_tap_id() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Stable tap references
#
# Driver expressions used to address a tap by its position in the list:
#
#     audio_tap("FFT Analyzer", 3)
#
# Positions are not stable. Deleting a tap shifts every later tap down one
# and frees a slot a newly added tap reuses, so an old driver silently
# latches onto an unrelated tap. Every tap carries a permanent `tap_id`, so
# the reference is a string instead:
#
#     audio_tap("FFT Analyzer", "Lights#61f03ca0")  <- what Copy emits
#     audio_tap("FFT Analyzer", "61f03ca0")         <- id alone
#     audio_tap("FFT Analyzer", "Lights")           <- name, breaks on rename
#
# When an id is spelled out (the "Name#id" form) the id is the authority:
# if that tap is gone the reference resolves to nothing and the driver falls
# back to its default. It never re-binds to whatever now sits in that slot.
# Bare integers still resolve by position, so old files keep working.
# ---------------------------------------------------------------------------

TAP_REF_SEP = "#"
_TAP_REF_UNSAFE = '"\'' + TAP_REF_SEP


def tap_ref(tap) -> str:
    """Stable reference string for a tap - 'Name#id', or the id alone."""
    tap_id = (getattr(tap, "tap_id", "") or "").strip()
    name = (getattr(tap, "name", "") or "").strip()
    if not tap_id:
        return name
    if not name or any(c in name for c in _TAP_REF_UNSAFE):
        return tap_id
    return f"{name}{TAP_REF_SEP}{tap_id}"


def resolve_tap_index(taps, ref) -> int:
    """Index of the tap a reference points at, or -1 if it points nowhere.

    `ref` may be an int (legacy position), a tap_id, a "Name#id" pair, or a
    tap name. Resolution order for a string: tap_id, then an unambiguous
    name, then a digits-only string treated as a legacy position. A ref that
    spells out an id resolves by id only - no name fallback - so a deleted
    tap stays deleted as far as the driver is concerned.
    """
    count = len(taps)
    if ref is None or isinstance(ref, bool):
        return -1

    if isinstance(ref, (int, float)):
        index = int(ref)
        return index if 0 <= index < count else -1

    try:
        ref = str(ref).strip()
    except Exception:
        return -1
    if not ref:
        return -1

    name_hint, sep, id_hint = ref.rpartition(TAP_REF_SEP)
    ref_id = id_hint.strip() if sep else ref
    if not sep:
        name_hint = ref

    # 1. tap_id - permanent, survives rename and reorder
    for i, tap in enumerate(taps):
        if tap.tap_id and tap.tap_id == ref_id:
            return i
    if sep:
        return -1  # explicit id that no longer exists

    # 2. exact name, only when it is unambiguous
    name_hint = name_hint.strip()
    matches = [i for i, tap in enumerate(taps)
               if (tap.name or "").strip() == name_hint]
    if len(matches) == 1:
        return matches[0]

    # 3. legacy position written as a string
    if ref.isdigit():
        index = int(ref)
        return index if 0 <= index < count else -1
    return -1


def resolve_tap(taps, ref):
    """The tap a reference points at, or None."""
    index = resolve_tap_index(taps, ref)
    return taps[index] if index >= 0 else None


# ---------------------------------------------------------------------------
# Stable analyzer-node references
#
# Tap refs were made stable in 4.12, but the OTHER half of a driver
# expression - which analyzer - was still a plain node name:
#
#     audio_tap("FFT Analyzer", "Lights#61f03ca0")
#
# Renaming the node in the editor silently broke every driver pointing at
# it, exactly the failure the tap fix removed one level down. The node now
# carries the same shape of reference, keyed on its permanent
# engine_node_id:
#
#     audio_tap("FFT Analyzer#7ac41f02", "Lights#61f03ca0")
#
# As with taps: when an id is spelled out it is the authority and there is
# no name fallback, so a deleted analyzer resolves to nothing rather than
# latching onto whatever now carries that name. Bare names still resolve
# (unambiguously), so existing files keep working untouched.
# ---------------------------------------------------------------------------

NODE_REF_SEP = TAP_REF_SEP


def node_id_hint(node) -> str:
    """Short stable id for an analyzer, taken from its engine_node_id.

    engine_node_id looks like 'fft_analyzer_7ac41f02'; the trailing hex is
    what goes in a driver expression - the type prefix is noise there.
    """
    engine_id = (getattr(node, "engine_node_id", "") or "").strip()
    if not engine_id:
        return ""
    return engine_id.rsplit("_", 1)[-1]


def node_ref(node) -> str:
    """Stable reference string for an analyzer node - 'Name#id', or bare."""
    name = (getattr(node, "name", "") or "").strip()
    hint = node_id_hint(node)
    if not hint:
        return name
    if not name or any(c in name for c in _TAP_REF_UNSAFE):
        return hint
    return f"{name}{NODE_REF_SEP}{hint}"


def _audio_trees():
    for tree in bpy.data.node_groups:
        if tree.bl_idname == 'AudioNodeTree':
            yield tree


def _analyzers():
    for tree in _audio_trees():
        for node in tree.nodes:
            if node.bl_idname == 'AudioFFTAnalyzerNode':
                yield tree, node


def resolve_analyzer(ref):
    """(tree, node) for an analyzer reference, or (None, None).

    Order: engine id, then an unambiguous exact name. A ref that spells out
    an id never falls back to the name.
    """
    if not ref:
        return (None, None)
    try:
        ref = str(ref).strip()
    except Exception:
        return (None, None)
    if not ref:
        return (None, None)

    name_hint, sep, id_hint = ref.rpartition(NODE_REF_SEP)
    ref_id = id_hint.strip() if sep else ""
    if not sep:
        name_hint = ref

    if ref_id:
        for tree, node in _analyzers():
            engine_id = getattr(node, "engine_node_id", "") or ""
            if engine_id == ref_id or engine_id.rsplit("_", 1)[-1] == ref_id:
                return (tree, node)
        return (None, None)  # explicit id that no longer exists

    name_hint = name_hint.strip()
    matches = [(tree, node) for tree, node in _analyzers()
               if node.name == name_hint]
    if len(matches) == 1:
        return matches[0]
    return (None, None)


def _tap_prop_changed(self, context):
    """Keep the socket label in sync when a tap is renamed."""
    node = getattr(self, "id_data", None)
    # id_data is the node tree; find the owning node via the tap_id.
    if node is None:
        return
    for n in getattr(node, "nodes", []):
        if n.bl_idname != 'AudioFFTAnalyzerNode':
            continue
        for tap in n.taps:
            if tap.tap_id == self.tap_id:
                if hasattr(n, "sync_tap_sockets"):
                    n.sync_tap_sockets()
                return


class AudioFrequencyTap(bpy.types.PropertyGroup):
    """One user-picked frequency capture band."""

    tap_id: StringProperty(
        name="Tap ID",
        description="Stable internal identifier",
        default=""
    )

    # PropertyGroup's built-in `name` is used as the display label so the
    # default UIList row shows an editable name.
    name: StringProperty(
        name="Name",
        description="Tap label (also the output socket name)",
        default="Tap",
        update=_tap_prop_changed
    )

    enabled: BoolProperty(
        name="Enabled",
        description="Compute and output this tap",
        default=True
    )

    center_hz: FloatProperty(
        name="Frequency",
        description="Center frequency of the capture band",
        default=440.0,
        min=FREQ_MIN,
        max=FREQ_MAX,
        precision=1,
        step=100
    )

    width_oct: FloatProperty(
        name="Width",
        description="Band width in octaves around the center",
        default=0.5,
        min=0.05,
        max=WIDTH_OCT_MAX,
        precision=2
    )

    gain: FloatProperty(
        name="Gain",
        description="Level multiplier for this tap",
        default=3.0,
        min=0.1,
        max=50.0,
        soft_max=15.0
    )

    smoothing: FloatProperty(
        name="Smoothing",
        description="Temporal smoothing (0 = instant, 0.99 = very slow)",
        default=0.3,
        min=0.0,
        max=0.99,
        subtype='FACTOR'
    )

    out_min: FloatProperty(
        name="Min",
        description="Output value when the band is silent (0 maps to this)",
        default=0.0,
        soft_min=-1000.0,
        soft_max=1000.0
    )

    out_max: FloatProperty(
        name="Max",
        description="Output value when the band is at full level (1 maps to this)",
        default=1.0,
        soft_min=-1000.0,
        soft_max=1000.0
    )

    use_random_trigger: BoolProperty(
        name="Random Trigger",
        description="Only fire occasionally: each time the band rises from "
                    "silence, the tap triggers with the probability below",
        default=False
    )

    trigger_probability: FloatProperty(
        name="Probability",
        description="Chance that a new onset passes through "
                    "(1 = every onset, 0.1 = roughly 1 in 10)",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype='FACTOR'
    )

    color: FloatVectorProperty(
        name="Color",
        description="Marker and socket color",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.85, 0.35, 0.19, 1.0)
    )


# ---------------------------------------------------------------------------
# Tap target discovery - "what is this tap driving right now?"
#
# A tap can reach the scene two ways:
#   1. Its output socket wired (directly or via Math nodes) into a Value
#      Output node -> Geometry Nodes modifier input on some object.
#   2. A driver expression anywhere in Blender calling
#      audio_tap("<analyzer node name>", "<tap ref>").
# Scanning every datablock each redraw would be expensive, so results are
# cached for a couple of seconds.
# ---------------------------------------------------------------------------

import time as _time

_tap_targets_cache = {}   # (tree_name, node_name) -> {tap_index: [labels]}
_tap_targets_stamp = 0.0
_TAP_TARGETS_TTL = 2.0

# Matches audio_tap("Name", "Lights#61f03ca0") / audio_tap("Name", 3) /
# audio_tap('Name'), with optional whitespace.
import re as _re
_TAP_EXPR = _re.compile(
    r"""audio_tap\(\s*['"](?P<node>[^'"]+)['"]\s*"""
    r"""(?:,\s*(?:['"](?P<ref>[^'"]*)['"]|(?P<index>\d+)))?""")
_NODE_PATH = _re.compile(r'nodes\["(?P<node>[^"]+)"\]')


def _axis_suffix(id_block, fcurve):
    """'.x' style suffix for vector properties, best effort."""
    try:
        if fcurve.array_index >= 0:
            path = fcurve.data_path or ""
            if path.endswith(("location", "rotation_euler", "scale", "delta_location")):
                return "." + "xyzw"[fcurve.array_index]
    except Exception:
        pass
    return ""


def _describe_driver_target(id_block, fcurve):
    """Human-readable 'what is being driven' for one driver fcurve."""
    path = fcurve.data_path or ""

    # Shader / compositor / geometry node trees: name the node being driven
    if isinstance(id_block, bpy.types.NodeTree):
        owner = None
        for pool in (bpy.data.materials, bpy.data.worlds, bpy.data.lights,
                     bpy.data.scenes):
            for cand in pool:
                if getattr(cand, "node_tree", None) == id_block:
                    owner = cand
                    break
            if owner:
                break
        kind = "compositor" if id_block.bl_idname == 'CompositorNodeTree' \
            else "shader" if id_block.bl_idname == 'ShaderNodeTree' \
            else "nodes"
        m = _NODE_PATH.search(path)
        node_part = m.group("node") if m else path
        base = owner.name if owner is not None else id_block.name
        return f"{base} › {node_part} ({kind})"

    # Collection-item paths: pose.bones["Bone"].rotation_euler,
    # key_blocks["Smile"].value, modifiers["GeometryNodes"]... - keep the
    # item name so the label stays meaningful.
    item = None
    for m in _re.finditer(r'\["([^"]+)"\]', path):
        item = m.group(1)
    prop = path.split(".")[-1] if path else "?"
    if item is not None and not prop.endswith('"]'):
        return f"{id_block.name} › {item}.{prop}{_axis_suffix(id_block, fcurve)}"
    if item is not None:
        return f"{id_block.name} › {item}"

    # Plain datablock property (object transform, light energy, ...)
    return f"{id_block.name}.{prop}{_axis_suffix(id_block, fcurve)}"


def _scan_tap_targets():
    """Build {(tree, node): {tap_index: [labels]}} for every analyzer."""
    targets = {}

    def add(tree_name, node_name, index, label):
        bucket = targets.setdefault((tree_name, node_name), {})
        bucket.setdefault(index, [])
        if label not in bucket[index]:
            bucket[index].append(label)

    # --- 1. Node-graph links: tap socket -> ... -> Value Output ----------
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        for node in tree.nodes:
            if node.bl_idname != 'AudioFFTAnalyzerNode':
                continue
            tap_index_by_id = {t.tap_id: i for i, t in enumerate(node.taps)}
            for socket in node.outputs:
                tap_id = getattr(socket, 'tap_id', '')
                if not tap_id or tap_id not in tap_index_by_id:
                    continue
                index = tap_index_by_id[tap_id]
                # Follow links (through pass-through nodes) up to 2 hops
                frontier = [link.to_node for link in socket.links]
                seen = set()
                for _hop in range(3):
                    next_frontier = []
                    for dest in frontier:
                        if dest is None or dest in seen:
                            continue
                        seen.add(dest)
                        if dest.bl_idname == 'AudioValueOutputNode':
                            obj = getattr(dest, 'target_object', None)
                            prop = getattr(dest, 'property_name', '')
                            if obj is not None and prop:
                                add(tree.name, node.name, index,
                                    f"{obj.name} › {prop} (geo nodes)")
                            else:
                                add(tree.name, node.name, index,
                                    f"{dest.name} (unassigned)")
                        else:
                            for out in dest.outputs:
                                for link in out.links:
                                    next_frontier.append(link.to_node)
                    frontier = next_frontier

    # --- 2. Drivers calling audio_tap(...) --------------------------------
    pools = (
        bpy.data.objects, bpy.data.materials, bpy.data.worlds,
        bpy.data.lights, bpy.data.cameras, bpy.data.scenes,
        bpy.data.node_groups, bpy.data.meshes, bpy.data.curves,
        bpy.data.shape_keys, bpy.data.speakers, bpy.data.armatures,
    )
    id_blocks = [b for pool in pools for b in pool]
    for pool in (bpy.data.materials, bpy.data.worlds, bpy.data.lights,
                 bpy.data.scenes):
        for owner in pool:
            tree = getattr(owner, "node_tree", None)
            if tree is not None:
                id_blocks.append(tree)

    for id_block in id_blocks:
        anim = getattr(id_block, "animation_data", None)
        if anim is None:
            continue
        try:
            fcurves = list(anim.drivers)
        except Exception:
            continue
        for fcurve in fcurves:
            driver = fcurve.driver
            if driver is None or driver.type != 'SCRIPTED':
                continue
            expr = driver.expression or ""
            for m in _TAP_EXPR.finditer(expr):
                node_name = m.group("node")
                ref = m.group("ref")
                if ref is None:
                    ref = int(m.group("index") or 0)
                # Resolve the analyzer through the same stable-ref path the
                # driver itself uses, so a "Name#id" expression still finds
                # its node after a rename - and a stale ref lists nothing.
                tree, analyzer = resolve_analyzer(node_name)
                if analyzer is None:
                    continue
                index = resolve_tap_index(analyzer.taps, ref)
                if index < 0:
                    continue
                add(tree.name, analyzer.name, index,
                    _describe_driver_target(id_block, fcurve))

    return targets


def get_tap_targets(node):
    """Cached {tap_index: [target labels]} for one analyzer node."""
    global _tap_targets_cache, _tap_targets_stamp
    now = _time.monotonic()
    if now - _tap_targets_stamp > _TAP_TARGETS_TTL:
        _tap_targets_stamp = now
        try:
            _tap_targets_cache = _scan_tap_targets()
        except Exception:
            _tap_targets_cache = {}
    return _tap_targets_cache.get((node.id_data.name, node.name), {})


class AUDIONODES_UL_taps(bpy.types.UIList):
    """Tap list shown on the FFT Analyzer node."""

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        tap = item
        row = layout.row(align=True)
        row.prop(tap, "enabled", text="")

        swatch = row.row(align=True)
        swatch.scale_x = 0.35
        swatch.prop(tap, "color", text="")

        row.prop(tap, "name", text="", emboss=False)

        info = row.row(align=True)
        info.alignment = 'RIGHT'
        # Show if any param input is driven (cables persist when tap inactive)
        driven = False
        try:
            for prop, _s, _lo, _hi in TAP_CONTROL_PARAMS:
                sock = data.get_tap_param_input(tap.tap_id, prop)
                if sock and sock.is_linked:
                    driven = True
                    break
        except Exception:
            pass
        if driven:
            info.label(text="", icon='LINKED')
        hz = tap.center_hz
        hz_text = f"{hz / 1000.0:.1f}k" if hz >= 1000 else f"{hz:.0f}"
        info.label(text=f"{hz_text} Hz")

        # Live level bar (channel values are mapped to out_min..out_max,
        # so normalize back to 0-1 for display)
        level = 0.0
        try:
            audio_data = context.scene.audio_nodes_data
            channel = f"{data.engine_node_id}_tap_{tap.tap_id}"
            if channel in audio_data.channels:
                raw = audio_data.channels[channel].value
                span = tap.out_max - tap.out_min
                if abs(span) > 1.0e-9:
                    level = (raw - tap.out_min) / span
                level = max(0.0, min(1.0, level))
        except Exception:
            pass

        bar = row.row(align=True)
        bar.scale_x = 0.8
        if hasattr(bar, "progress"):
            bar.progress(factor=level, type='BAR', text="")
        else:
            bar.label(text=f"{level:.2f}")
