"""Live spectrum overlay + frequency tap picking for the FFT Analyzer node.

A SpaceNodeEditor draw handler paints the analyzer's live spectrum (from
the shared-memory FFT the 60fps timer already reads) directly inside the
node body, in the area draw_buttons reserves with empty rows. Tap bands
are drawn as colored markers on top.

A modal operator implements the frequency picker: crosshair with a live
Hz / dB readout, scroll wheel adjusts the band width, click places the
tap. Everything runs Blender-side; the C++ engine is untouched.
"""

import math

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

from ..nodes.effects import fft_taps

# ---------------------------------------------------------------------------
# Geometry calibration (view-space units at ui_scale 1.0).
# Node layout: header, output sockets, draw_buttons content, input sockets.
# The spectrum area is the block of empty label rows at the TOP of
# draw_buttons, i.e. directly below the last visible output socket.
# ---------------------------------------------------------------------------
HEADER_H = 26.0        # node header height
SOCKET_H = 22.0        # per visible output socket row
TOP_PAD = 6.0          # gap between sockets and the reserved rows
ROW_H = 20.0           # height of one empty label row (widget unit)
SIDE_PAD = 12.0        # horizontal inset inside the node

SPECTRUM_COLUMNS = 96

# Active pick session (None when idle). Read by the draw handler.
_pick = None

_draw_handle = None


# ---------------------------------------------------------------------------
# Helpers
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


def _spectrum_rect_view(context, node):
    """Spectrum area rect in view coordinates: (x0, y0, x1, y1) or None."""
    if not getattr(node, "show_spectrum", False) or node.hide:
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
    height = node.SPECTRUM_ROWS * ROW_H * ui
    bottom = top - height

    x0 = loc_x + SIDE_PAD * ui
    x1 = loc_x + width - SIDE_PAD * ui
    if x1 - x0 < 40:
        return None
    return (x0, bottom, x1, top)


def _spectrum_rect_region(context, node):
    """Spectrum rect in region pixels, or None."""
    rect = _spectrum_rect_view(context, node)
    if rect is None:
        return None
    v2d = context.region.view2d
    x0, y0 = v2d.view_to_region(rect[0], rect[1], clip=False)
    x1, y1 = v2d.view_to_region(rect[2], rect[3], clip=False)
    if x1 - x0 < 60:
        return None  # too zoomed out to be useful
    return (x0, y0, x1, y1)


def _get_spectrum(node=None):
    """Latest (fft_left, fft_right, sample_rate) or None.

    Pass the FFT Analyzer node to get THAT node's own spectrum - every
    analyzer publishes its own slot, so two FFT nodes show two different
    spectra (previously all nodes drew the newest analyzer's data).
    """
    from .. import shared_memory_reader
    node_id = getattr(node, 'engine_node_id', None) if node is not None else None
    return shared_memory_reader.get_latest_spectrum(node_id)


def _display_value(v):
    """Perceptual shaping for bin magnitudes (already 0-1 normalized)."""
    if v <= 0.0:
        return 0.0
    return min(1.0, v ** 0.45)


def _band_level(spectrum, center_hz, width_oct, sample_rate):
    """Band energy (0-1) from a cached spectrum, mono max of L/R."""
    fft_left, fft_right, _ = spectrum
    left = fft_taps.tap_band_energy(fft_left, center_hz, width_oct, sample_rate)
    right = fft_taps.tap_band_energy(fft_right, center_hz, width_oct, sample_rate)
    return max(left, right)


def _level_to_db_text(level):
    db = 20.0 * math.log10(max(level, 1.0e-5))
    return f"{db:.0f} dB"


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _draw_quad(shader, x0, y0, x1, y1, color):
    batch = batch_for_shader(shader, 'TRI_STRIP', {
        "pos": [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_lines(shader, coords, color):
    batch = batch_for_shader(shader, 'LINES', {"pos": coords})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_text(x, y, size, color, text):
    font_id = 0
    blf.size(font_id, size)
    blf.color(font_id, *color)
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, text)


def _freq_to_x(freq, x0, x1):
    return x0 + fft_taps.freq_to_norm(freq) * (x1 - x0)


def _x_to_freq(x, x0, x1):
    if x1 <= x0:
        return 440.0
    return fft_taps.norm_to_freq((x - x0) / (x1 - x0))


def _draw_spectrum_for_node(context, node, shader, spectrum):
    rect = _spectrum_rect_region(context, node)
    if rect is None:
        return
    x0, y0, x1, y1 = rect
    w = x1 - x0
    h = y1 - y0

    # Background
    _draw_quad(shader, x0, y0, x1, y1, (0.06, 0.06, 0.06, 0.9))

    # Grid lines at decades
    grid = []
    for f in (100.0, 1000.0, 10000.0):
        gx = _freq_to_x(f, x0, x1)
        grid.extend([(gx, y0), (gx, y1)])
    _draw_lines(shader, grid, (1.0, 1.0, 1.0, 0.07))

    # Spectrum columns (log-frequency, peak-preserving downsample)
    if spectrum is not None:
        fft_left, fft_right, sample_rate = spectrum
        num_bins = len(fft_left)
        if sample_rate <= 0:
            sample_rate = 44100.0
        bin_hz = sample_rate / (2.0 * num_bins)

        fill = []
        line = []
        prev_bin = 0
        for c in range(SPECTRUM_COLUMNS + 1):
            t = c / SPECTRUM_COLUMNS
            freq = fft_taps.norm_to_freq(t)
            b = min(num_bins - 1, int(freq / bin_hz))
            # Peak over the bins this column spans
            v = 0.0
            for i in range(min(prev_bin, b), b + 1):
                m = max(fft_left[i], fft_right[i])
                if m > v:
                    v = m
            prev_bin = b + 1
            v = _display_value(v)
            cx = x0 + t * w
            cy = y0 + v * (h - 14)  # leave headroom for tap labels
            fill.extend([(cx, y0), (cx, cy)])
            line.append((cx, cy))

        batch = batch_for_shader(shader, 'TRI_STRIP', {"pos": fill})
        shader.uniform_float("color", (0.25, 0.42, 0.32, 0.75))
        batch.draw(shader)

        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": line})
        shader.uniform_float("color", (0.56, 0.83, 0.66, 0.95))
        batch.draw(shader)

    # Tap bands
    for tap in node.taps:
        if not tap.enabled:
            continue
        low_hz, high_hz = fft_taps.tap_freq_range(tap.center_hz, tap.width_oct)
        bx0 = _freq_to_x(low_hz, x0, x1)
        bx1 = _freq_to_x(high_hz, x0, x1)
        cx = _freq_to_x(tap.center_hz, x0, x1)
        c = tap.color
        _draw_quad(shader, bx0, y0, bx1, y1, (c[0], c[1], c[2], 0.16))
        _draw_lines(shader, [(cx, y0), (cx, y1)], (c[0], c[1], c[2], 0.9))
        # Label chip at the top of the band
        _draw_quad(shader, bx0, y1 - 13, bx1, y1, (c[0], c[1], c[2], 0.45))
        if w > 150:
            hz = tap.center_hz
            hz_text = f"{hz / 1000.0:.1f}k" if hz >= 1000 else f"{hz:.0f}"
            _draw_text(min(bx0 + 3, x1 - 30), y1 - 11, 10,
                       (1.0, 1.0, 1.0, 0.95), hz_text)

    # Axis labels
    if w > 170:
        for f, label in ((100.0, "100"), (1000.0, "1k"), (10000.0, "10k")):
            gx = _freq_to_x(f, x0, x1)
            _draw_text(gx + 2, y0 + 2, 9, (1.0, 1.0, 1.0, 0.35), label)

    # Active pick session on this node
    global _pick
    if _pick is not None and _pick.get("node") == node.name:
        freq = _pick.get("freq")
        if freq:
            width_oct = _pick.get("width", 0.5)
            low_hz, high_hz = fft_taps.tap_freq_range(freq, width_oct)
            px0 = _freq_to_x(low_hz, x0, x1)
            px1 = _freq_to_x(high_hz, x0, x1)
            cx = _freq_to_x(freq, x0, x1)

            _draw_quad(shader, px0, y0, px1, y1, (0.95, 0.78, 0.35, 0.15))
            _draw_lines(shader, [(cx, y0), (cx, y1)], (0.98, 0.92, 0.75, 0.95))

            # Readout box above the spectrum
            level = _pick.get("level", 0.0)
            hz_text = f"{freq / 1000.0:.2f} kHz" if freq >= 1000 else f"{freq:.0f} Hz"
            text = f"{hz_text} · {_level_to_db_text(level)} · scroll = width"
            _draw_quad(shader, x0, y1 + 2, x1, y1 + 18, (0.0, 0.0, 0.0, 0.75))
            _draw_text(x0 + 5, y1 + 6, 10, (0.95, 0.85, 0.55, 1.0), text)


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
        if node.bl_idname != 'AudioFFTAnalyzerNode':
            continue
        try:
            # Each analyzer draws ITS OWN spectrum slot
            _draw_spectrum_for_node(context, node, shader, _get_spectrum(node))
        except Exception:
            pass  # never break the node editor's draw loop

    gpu.state.blend_set('NONE')


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class AUDIONODES_OT_add_tap(bpy.types.Operator):
    """Pick a frequency on the analyzer spectrum to capture as a tap.

    Click the spectrum to place the tap; scroll adjusts the band width.
    """
    bl_idname = "audionodes.add_tap"
    bl_label = "Add Frequency Tap"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty()

    def _node(self, context):
        tree = context.space_data.edit_tree
        if tree is None:
            return None
        return tree.nodes.get(self.node_name)

    def invoke(self, context, event):
        global _pick
        node = self._node(context)
        if node is None:
            self.report({'ERROR'}, "FFT Analyzer node not found")
            return {'CANCELLED'}

        if not node.show_spectrum:
            node.show_spectrum = True

        _pick = {
            "node": node.name,
            "freq": 440.0,
            "width": 0.5,
            "level": 0.0,
        }

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        try:
            context.window.cursor_modal_set('EYEDROPPER')
        except Exception:
            context.window.cursor_modal_set('CROSSHAIR')
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _cleanup(self, context):
        global _pick
        _pick = None
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        if getattr(self, "_timer", None) is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if context.area is not None:
            context.area.header_text_set(None)
            context.area.tag_redraw()

    def _update_hover(self, context, event, node):
        global _pick
        rect = _spectrum_rect_region(context, node)
        if rect is None:
            return
        x0, _y0, x1, _y1 = rect
        freq = _x_to_freq(min(max(event.mouse_region_x, x0), x1), x0, x1)
        _pick["freq"] = freq

        spectrum = _get_spectrum(node)
        if spectrum is not None:
            _pick["level"] = _band_level(spectrum, freq, _pick["width"], spectrum[2])

    def modal(self, context, event):
        global _pick
        node = self._node(context)
        if node is None or _pick is None:
            self._cleanup(context)
            return {'CANCELLED'}

        if event.type == 'MOUSEMOVE':
            self._update_hover(context, event, node)
            context.area.tag_redraw()

        elif event.type == 'TIMER':
            # keep the level readout live even without mouse movement
            spectrum = _get_spectrum(node)
            if spectrum is not None:
                _pick["level"] = _band_level(
                    spectrum, _pick["freq"], _pick["width"], spectrum[2])
            context.area.tag_redraw()

        elif event.type == 'WHEELUPMOUSE':
            _pick["width"] = min(fft_taps.WIDTH_OCT_MAX, _pick["width"] * 1.25)
            context.area.tag_redraw()

        elif event.type == 'WHEELDOWNMOUSE':
            _pick["width"] = max(0.05, _pick["width"] / 1.25)
            context.area.tag_redraw()

        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            freq = _pick["freq"]
            width = _pick["width"]
            self._cleanup(context)
            node.add_tap(freq, width)
            self.report({'INFO'}, f"Tap added at {freq:.0f} Hz")
            return {'FINISHED'}

        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            self._cleanup(context)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}


class AUDIONODES_OT_remove_tap(bpy.types.Operator):
    """Remove the selected frequency tap and its output socket"""
    bl_idname = "audionodes.remove_tap"
    bl_label = "Remove Frequency Tap"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty()
    tap_index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        tree = context.space_data.edit_tree
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None:
            return {'CANCELLED'}
        node.remove_tap(self.tap_index)
        return {'FINISHED'}


class AUDIONODES_OT_tap_to_value_output(bpy.types.Operator):
    """Create a Value Output node wired to this tap"""
    bl_idname = "audionodes.tap_to_value_output"
    bl_label = "Tap to Value Output"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty()
    tap_index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        tree = context.space_data.edit_tree
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or not (0 <= self.tap_index < len(node.taps)):
            return {'CANCELLED'}

        tap = node.taps[self.tap_index]
        socket = node.get_tap_socket(tap.tap_id)
        if socket is None:
            node.sync_tap_sockets()
            socket = node.get_tap_socket(tap.tap_id)
        if socket is None:
            return {'CANCELLED'}

        value_out = tree.nodes.new('AudioValueOutputNode')
        value_out.location = (
            node.location.x + node.width + 80,
            node.location.y - 40 - self.tap_index * 180,
        )
        value_out.label = tap.name
        value_out.property_name = tap.name.lower().replace(" ", "_")

        tree.links.new(socket, value_out.inputs['Value'])

        for n in tree.nodes:
            n.select = False
        value_out.select = True
        tree.nodes.active = value_out
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

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
