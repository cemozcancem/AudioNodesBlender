"""In-node graphics for Sampler, ADSR, Delay and Filter.

These four nodes were pure slider stacks: a sample you could not see, an
envelope you had to imagine, a delay whose repeats were a number in
milliseconds, and a filter whose shape lived only in the label "Low Pass".
Each one now paints a picture of what it is doing, in the node body.

The mechanism is the one ui/spectrum_overlay.py already uses for the FFT
Analyzer: the node reserves empty rows at the TOP of draw_buttons, and a
SpaceNodeEditor draw handler paints into that rectangle. Each node type
supplies a painter; everything else here is shared geometry.

Because the rectangle is located by assuming it starts directly below the
last visible output socket, the reserved rows MUST be the first thing a
node draws.
"""

import math
import os

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

# ---------------------------------------------------------------------------
# Geometry (view-space units at ui_scale 1.0) - same calibration as the
# spectrum overlay, which is pinned against the real node layout.
# ---------------------------------------------------------------------------
HEADER_H = 26.0
SOCKET_H = 22.0
TOP_PAD = 6.0
ROW_H = 20.0
SIDE_PAD = 12.0

_draw_handle = None

# Palette shared by every painter, so the four nodes read as one family.
BG = (0.06, 0.06, 0.06, 0.9)
GRID = (1.0, 1.0, 1.0, 0.07)
AXIS_TEXT = (1.0, 1.0, 1.0, 0.35)
CURVE = (0.56, 0.83, 0.66, 0.95)
CURVE_FILL = (0.25, 0.42, 0.32, 0.75)
ACCENT = (0.98, 0.78, 0.35, 0.95)
ACCENT_FILL = (0.95, 0.78, 0.35, 0.18)
MUTED = (1.0, 1.0, 1.0, 0.25)


# ---------------------------------------------------------------------------
# Shared geometry helpers
# ---------------------------------------------------------------------------

def _node_abs_location(node):
    """Node location including parent frame offsets."""
    x, y = node.location.x, node.location.y
    parent = node.parent
    while parent is not None:
        x += parent.location.x
        y += parent.location.y
        parent = parent.parent
    return x, y


def _graph_rect_region(context, node):
    """The reserved rectangle in region pixels, or None if not drawable."""
    rows = getattr(node, "GRAPH_ROWS", 0)
    if not rows or node.hide or not getattr(node, "show_graph", False):
        return None

    ui = context.preferences.system.ui_scale
    loc_x, loc_y = _node_abs_location(node)
    loc_x *= ui
    loc_y *= ui

    width = node.dimensions.x
    if width <= 0:
        return None

    n_out = sum(1 for s in node.outputs if not s.hide)

    top = loc_y - (HEADER_H + n_out * SOCKET_H + TOP_PAD) * ui
    bottom = top - rows * ROW_H * ui
    x0 = loc_x + SIDE_PAD * ui
    x1 = loc_x + width - SIDE_PAD * ui
    if x1 - x0 < 40:
        return None

    v2d = context.region.view2d
    rx0, ry0 = v2d.view_to_region(x0, bottom, clip=False)
    rx1, ry1 = v2d.view_to_region(x1, top, clip=False)
    if rx1 - rx0 < 60:
        return None  # too zoomed out to read
    return (rx0, ry0, rx1, ry1)


def _quad(shader, x0, y0, x1, y1, color):
    batch = batch_for_shader(shader, 'TRI_STRIP', {
        "pos": [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _lines(shader, coords, color):
    if len(coords) < 2:
        return
    batch = batch_for_shader(shader, 'LINES', {"pos": coords})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _strip(shader, coords, color):
    if len(coords) < 2:
        return
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _text(x, y, size, color, text):
    blf.size(0, size)
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, text)


def _channel(context, node, suffix, default=0.0):
    """Live value of one of this node's published channels."""
    try:
        channel = context.scene.audio_nodes_data.channels.get(
            f"{node.engine_node_id}_{suffix}")
        if channel is not None:
            return channel.value
    except Exception:
        pass
    return default


# ---------------------------------------------------------------------------
# Sample waveform cache
# ---------------------------------------------------------------------------

# (abspath, mtime, columns) -> [(min, max), ...] or None when unreadable.
_waveform_cache = {}
_WAVEFORM_COLUMNS = 220
_MAX_SAMPLE_BYTES = 64 * 1024 * 1024


def _decode_samples(abspath):
    """Mono float samples for a file, or None.

    Blender ships audaspace, which already decodes every format the engine
    accepts, so there is no new dependency here. A plain-wave fallback
    covers builds where aud.Sound.data() is unavailable.
    """
    try:
        import numpy as np
    except Exception:
        np = None

    try:
        import aud
        data = aud.Sound(abspath).data()
        if np is not None:
            array = np.asarray(data, dtype="float32")
            if array.ndim > 1:
                array = array.mean(axis=1)
            return array
    except Exception:
        pass

    # Fallback: uncompressed WAV only.
    try:
        import wave
        import array as pyarray
        with wave.open(abspath, "rb") as handle:
            if handle.getsampwidth() != 2:
                return None
            channels = handle.getnchannels()
            frames = handle.readframes(handle.getnframes())
        samples = pyarray.array("h")
        samples.frombytes(frames[:len(frames) - (len(frames) % 2)])
        if np is not None:
            array = np.asarray(samples, dtype="float32") / 32768.0
            if channels > 1:
                usable = (len(array) // channels) * channels
                array = array[:usable].reshape(-1, channels).mean(axis=1)
            return array
        values = [s / 32768.0 for s in samples]
        if channels > 1:
            values = [sum(values[i:i + channels]) / channels
                      for i in range(0, len(values) - channels + 1, channels)]
        return values
    except Exception:
        return None


def _peak_envelope(path, columns=_WAVEFORM_COLUMNS):
    """Per-column (min, max) peaks for a sample file, cached. None if unreadable."""
    if not path:
        return None
    try:
        abspath = bpy.path.abspath(path)
        stat = os.stat(abspath)
    except Exception:
        return None
    if stat.st_size > _MAX_SAMPLE_BYTES:
        return None  # not worth decoding just to draw a thumbnail

    key = (abspath, stat.st_mtime, columns)
    if key in _waveform_cache:
        return _waveform_cache[key]

    # One file's envelope per node is plenty; do not let the cache grow
    # without bound across a long session of swapping samples.
    if len(_waveform_cache) > 32:
        _waveform_cache.clear()

    samples = _decode_samples(abspath)
    envelope = None
    if samples is not None and len(samples):
        total = len(samples)
        # numpy's min/max where available: iterating a multi-million-sample
        # array in Python would stall the redraw that asked for the
        # waveform, which is the one place a stall is most visible.
        fast = hasattr(samples, "min") and hasattr(samples, "max")
        envelope = []
        for c in range(columns):
            start = (total * c) // columns
            end = max(start + 1, (total * (c + 1)) // columns)
            chunk = samples[start:end]
            try:
                if fast:
                    envelope.append((float(chunk.min()), float(chunk.max())))
                else:
                    envelope.append((float(min(chunk)), float(max(chunk))))
            except (ValueError, TypeError):
                envelope.append((0.0, 0.0))

    _waveform_cache[key] = envelope
    return envelope


def clear_waveform_cache():
    _waveform_cache.clear()


# ---------------------------------------------------------------------------
# Painters
# ---------------------------------------------------------------------------

def _draw_sampler(context, node, shader, rect):
    x0, y0, x1, y1 = rect
    w = x1 - x0
    h = y1 - y0
    mid = y0 + h * 0.5

    _quad(shader, x0, y0, x1, y1, BG)
    _lines(shader, [(x0, mid), (x1, mid)], GRID)

    envelope = _peak_envelope(node.sample_path)
    if envelope is None:
        label = "No sample" if not node.sample_path else "Waveform unavailable"
        _text(x0 + 6, mid - 4, 10, MUTED, label)
        return

    # Region outside Start/End is dimmed rather than hidden, so trimming
    # reads as trimming instead of the sample getting shorter.
    start = min(node.start_point, node.end_point) / 100.0
    end = max(node.start_point, node.end_point) / 100.0
    sx = x0 + start * w
    ex = x0 + end * w
    if sx > x0:
        _quad(shader, x0, y0, sx, y1, (0.0, 0.0, 0.0, 0.45))
    if ex < x1:
        _quad(shader, ex, y0, x1, y1, (0.0, 0.0, 0.0, 0.45))

    columns = len(envelope)
    half = h * 0.45
    coords = []
    for c, (lo, hi) in enumerate(envelope):
        cx = x0 + (c / max(1, columns - 1)) * w
        coords.append((cx, mid + max(-1.0, min(1.0, hi)) * half))
        coords.append((cx, mid + max(-1.0, min(1.0, lo)) * half))
    _lines(shader, coords, CURVE)

    _lines(shader, [(sx, y0), (sx, y1)], ACCENT)
    _lines(shader, [(ex, y0), (ex, y1)], ACCENT)

    if w > 130:
        _text(x0 + 4, y1 - 12, 9, AXIS_TEXT,
              os.path.basename(bpy.path.abspath(node.sample_path))[:28])
        if node.loop:
            _text(x1 - 32, y0 + 4, 9, ACCENT, "loop")


def _draw_adsr(context, node, shader, rect):
    x0, y0, x1, y1 = rect
    w = x1 - x0
    h = y1 - y0
    base = y0 + 4
    top = y1 - 10

    _quad(shader, x0, y0, x1, y1, BG)

    attack = max(node.attack, 1.0)
    decay = max(node.decay, 1.0)
    release = max(node.release, 1.0)
    sustain = node.sustain / 100.0
    # The sustain segment has no duration of its own; give it a fixed
    # quarter of the width so the shape stays readable at any A/D/R.
    span = attack + decay + release
    sustain_w = w * 0.25
    scale = (w - sustain_w) / span

    ax = x0 + attack * scale
    dx = ax + decay * scale
    sx = dx + sustain_w
    rx = sx + release * scale

    sustain_y = base + sustain * (top - base)
    curve = [(x0, base), (ax, top), (dx, sustain_y), (sx, sustain_y), (rx, base)]

    fill = []
    for px, py in curve:
        fill.extend([(px, base), (px, py)])
    batch = batch_for_shader(shader, 'TRI_STRIP', {"pos": fill})
    shader.uniform_float("color", CURVE_FILL)
    batch.draw(shader)
    _strip(shader, curve, CURVE)

    # Live envelope level - the engine publishes it, so this actually moves.
    level = _channel(context, node, "envelope")
    if level > 0.001:
        ly = base + min(1.0, level) * (top - base)
        _lines(shader, [(x0, ly), (x1, ly)], ACCENT)
        _quad(shader, x1 - 6, base, x1 - 2, ly, ACCENT)

    if w > 150:
        for px, label in ((x0, "A"), (ax, "D"), (dx, "S"), (sx, "R")):
            _text(px + 3, y0 + 3, 9, AXIS_TEXT, label)


def _draw_delay(context, node, shader, rect):
    x0, y0, x1, y1 = rect
    w = x1 - x0
    h = y1 - y0
    base = y0 + 4
    top = y1 - 8

    _quad(shader, x0, y0, x1, y1, BG)

    # Show two seconds of timeline, which is the node's own maximum, so the
    # spacing of the repeats is comparable as the time knob moves.
    window_ms = 2000.0
    for ms in (500.0, 1000.0, 1500.0):
        gx = x0 + (ms / window_ms) * w
        _lines(shader, [(gx, y0), (gx, y1)], GRID)

    delay_ms = max(1.0, node.delay_time)
    feedback = node.feedback
    mix = node.mix

    # Dry hit, then each repeat at feedback^n, laid out on the timeline.
    _quad(shader, x0, base, x0 + 3, base + (1.0 - mix * 0.5) * (top - base), MUTED)

    level = mix
    t = delay_ms
    index = 1
    while t < window_ms and level > 0.01 and index < 64:
        px = x0 + (t / window_ms) * w
        _quad(shader, px - 1, base, px + 2, base + level * (top - base), CURVE)
        level *= feedback
        t += delay_ms
        index += 1

    _lines(shader, [(x0, base), (x1, base)], GRID)

    if w > 150:
        _text(x0 + 4, y1 - 11, 9, AXIS_TEXT,
              f"{delay_ms:.0f} ms · fb {feedback:.2f} · mix {mix:.2f}")
        _text(x1 - 26, y0 + 3, 9, AXIS_TEXT, "2 s")


_FILTER_MIN_HZ = 20.0
_FILTER_MAX_HZ = 20000.0


def _filter_norm(hz):
    lo = math.log10(_FILTER_MIN_HZ)
    hi = math.log10(_FILTER_MAX_HZ)
    return (math.log10(max(_FILTER_MIN_HZ, min(_FILTER_MAX_HZ, hz))) - lo) / (hi - lo)


def _filter_magnitude(kind, ratio, q):
    """Magnitude of a 2-pole state-variable filter at frequency ratio w/wc."""
    if ratio <= 0.0:
        ratio = 1e-6
    # |H| for the standard biquad prototypes, in terms of w/wc.
    denom = math.sqrt((1.0 - ratio * ratio) ** 2 + (ratio / max(q, 0.01)) ** 2)
    if denom <= 0.0:
        return 0.0
    if kind == 'HIGHPASS':
        return (ratio * ratio) / denom
    if kind == 'BANDPASS':
        return (ratio / max(q, 0.01)) / denom
    return 1.0 / denom


def _draw_filter(context, node, shader, rect):
    x0, y0, x1, y1 = rect
    w = x1 - x0
    h = y1 - y0

    _quad(shader, x0, y0, x1, y1, BG)

    grid = []
    for hz in (100.0, 1000.0, 10000.0):
        gx = x0 + _filter_norm(hz) * w
        grid.extend([(gx, y0), (gx, y1)])
    _lines(shader, grid, GRID)

    cutoff = max(20.0, node.cutoff)
    q = max(0.1, node.resonance)
    kind = node.filter_type

    # Plot in dB so resonance shows as a peak instead of a spike off-screen.
    columns = 96
    curve = []
    fill = []
    lo = math.log10(_FILTER_MIN_HZ)
    hi = math.log10(_FILTER_MAX_HZ)
    for c in range(columns + 1):
        t = c / columns
        hz = 10.0 ** (lo + t * (hi - lo))
        mag = _filter_magnitude(kind, hz / cutoff, q)
        db = 20.0 * math.log10(max(mag, 1.0e-4))
        norm = max(0.0, min(1.0, (db + 48.0) / 60.0))  # -48 dB .. +12 dB
        cx = x0 + t * w
        cy = y0 + norm * (h - 12)
        curve.append((cx, cy))
        fill.extend([(cx, y0), (cx, cy)])

    batch = batch_for_shader(shader, 'TRI_STRIP', {"pos": fill})
    shader.uniform_float("color", CURVE_FILL)
    batch.draw(shader)
    _strip(shader, curve, CURVE)

    cx = x0 + _filter_norm(cutoff) * w
    _lines(shader, [(cx, y0), (cx, y1)], ACCENT)

    if w > 150:
        label = (f"{cutoff / 1000.0:.2f} kHz" if cutoff >= 1000
                 else f"{cutoff:.0f} Hz")
        _text(x0 + 4, y1 - 11, 9, ACCENT, f"{label} · Q {q:.2f}")
        for hz, name in ((100.0, "100"), (1000.0, "1k"), (10000.0, "10k")):
            _text(x0 + _filter_norm(hz) * w + 2, y0 + 3, 9, AXIS_TEXT, name)


PAINTERS = {
    'AudioSamplerNode': _draw_sampler,
    'AudioADSRNode': _draw_adsr,
    'AudioDelayNode': _draw_delay,
    'AudioFilterNode': _draw_filter,
}


# ---------------------------------------------------------------------------
# Draw handler
# ---------------------------------------------------------------------------

def _draw_callback():
    context = bpy.context
    space = context.space_data
    if space is None or space.type != 'NODE_EDITOR':
        return
    if space.tree_type != 'AudioNodeTree' or space.edit_tree is None:
        return

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    shader.bind()

    for node in space.edit_tree.nodes:
        painter = PAINTERS.get(node.bl_idname)
        if painter is None:
            continue
        try:
            rect = _graph_rect_region(context, node)
            if rect is not None:
                painter(context, node, shader, rect)
        except Exception:
            pass  # never break the node editor's draw loop

    gpu.state.blend_set('NONE')


def register_overlay():
    global _draw_handle
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceNodeEditor.draw_handler_add(
            _draw_callback, (), 'WINDOW', 'POST_PIXEL')


def unregister_overlay():
    global _draw_handle
    if _draw_handle is not None:
        try:
            bpy.types.SpaceNodeEditor.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            pass
        _draw_handle = None
    clear_waveform_cache()
