import math

import bpy
from bpy.props import BoolProperty, IntProperty, CollectionProperty
from ..core.node_base import AudioNodeBase
from . import fft_taps


# Legacy fixed-band sockets (pre-4.2) mapped to (low_hz, high_hz, gain).
# Old .blend files still contain these sockets; linked ones are converted
# into equivalent frequency taps on load, then removed.
LEGACY_BANDS = {
    "Low":        (20.0, 250.0, 1.0),
    "Mid":        (250.0, 4000.0, 3.0),
    "High":       (4000.0, 20000.0, 5.0),
    "Sub Bass":   (20.0, 60.0, 1.0),
    "Bass":       (60.0, 250.0, 1.0),
    "Low Mid":    (250.0, 500.0, 3.0),
    "Mid Range":  (500.0, 2000.0, 3.0),
    "High Mid":   (2000.0, 4000.0, 3.0),
    "Presence":   (4000.0, 6000.0, 5.0),
    "Brilliance": (6000.0, 20000.0, 5.0),
}


class AudioFFTAnalyzerNode(AudioNodeBase):
    """FFT Analyzer node - live spectrum with user-picked frequency taps.

    The spectrum is drawn inside the node body (GPU overlay). Users place
    "taps" on it - picked center frequency + bandwidth. Each tap becomes a
    colored control output socket carrying that band's energy, ready to
    wire into a Value Output node. Peak/RMS level outputs are also
    provided; the old fixed Low/Mid/High bands are gone (existing files
    migrate their linked band sockets to equivalent taps automatically).
    """
    bl_idname = "AudioFFTAnalyzerNode"
    bl_label = "FFT Analyzer"
    bl_icon = 'FCURVE'
    bl_width_default = 240

    # Rows of vertical space reserved in draw_buttons for the GPU spectrum.
    # The overlay (ui/spectrum_overlay.py) draws into this reserved area.
    SPECTRUM_ROWS = 6

    show_spectrum: BoolProperty(
        name="Show Spectrum",
        description="Display the live spectrum inside the node",
        default=True
    )

    # Frequency taps (user-picked capture bands)
    taps: CollectionProperty(type=fft_taps.AudioFrequencyTap)
    active_tap_index: IntProperty(
        default=0,
        update=lambda self, ctx: self.update_tap_param_input_visibility()
    )

    @property
    def node_type(self) -> str:
        return "fft_analyzer"

    def init(self, context):
        # Audio pass-through
        self.inputs.new('AudioSocketType', "Input")
        self.outputs.new('AudioSocketType', "Output")

        # Level outputs
        self.outputs.new('ControlSocketType', "Peak L")
        self.outputs.new('ControlSocketType', "Peak R")
        self.outputs.new('ControlSocketType', "RMS L")
        self.outputs.new('ControlSocketType', "RMS R")

        super().init(context)

    # ------------------------------------------------------------------
    # Legacy band migration (pre-4.2 .blend files)
    # ------------------------------------------------------------------

    def migrate_legacy_band_sockets(self) -> bool:
        """Convert linked legacy band sockets to taps, drop the rest.

        Returns True if anything changed. Idempotent - once the legacy
        sockets are gone this is a cheap no-op.
        """
        legacy = [s for s in self.outputs
                  if s.name in LEGACY_BANDS and not getattr(s, "tap_id", "")]
        if not legacy:
            return False

        tree = self.id_data
        for socket in legacy:
            if socket.is_linked:
                low_hz, high_hz, gain = LEGACY_BANDS[socket.name]
                center = math.sqrt(low_hz * high_hz)
                width = min(fft_taps.WIDTH_OCT_MAX, math.log2(high_hz / low_hz))
                tap = self.add_tap(center, width, name=socket.name)
                tap.gain = gain
                new_socket = self.get_tap_socket(tap.tap_id)
                if new_socket is not None:
                    for link in list(socket.links):
                        tree.links.new(new_socket, link.to_socket)
            self.outputs.remove(socket)

        print(f"Audio Nodes: migrated legacy band outputs to taps on '{self.name}'")
        return True

    # ------------------------------------------------------------------
    # Frequency taps
    # ------------------------------------------------------------------

    def add_tap(self, center_hz: float, width_oct: float = 0.5, name: str = ""):
        """Create a tap + its output socket. Returns the new tap."""
        tap = self.taps.add()
        tap.tap_id = fft_taps.new_tap_id()
        tap.center_hz = center_hz
        tap.width_oct = width_oct
        tap.color = fft_taps.TAP_COLORS[(len(self.taps) - 1) % len(fft_taps.TAP_COLORS)]
        tap.name = name or "Tap"
        self.active_tap_index = len(self.taps) - 1
        self.sync_tap_sockets()
        return tap

    def remove_tap(self, index: int):
        """Remove a tap and its socket (links are dropped with the socket)."""
        if index < 0 or index >= len(self.taps):
            return
        self.taps.remove(index)
        self.active_tap_index = min(self.active_tap_index, len(self.taps) - 1)
        self.sync_tap_sockets()

    def get_tap_socket(self, tap_id: str):
        """Return the tap *output* socket (no tap_param) for this tap_id."""
        for socket in self.outputs:
            if (getattr(socket, "tap_id", "") == tap_id
                    and not getattr(socket, "tap_param", "")):
                return socket
        return None

    def get_tap_param_input(self, tap_id: str, param: str):
        """Return the control *input* socket for (tap_id, tap_param)."""
        for socket in self.inputs:
            if (getattr(socket, "tap_id", "") == tap_id
                    and getattr(socket, "tap_param", "") == param):
                return socket
        return None

    def sync_tap_sockets(self):
        """Mirror taps collection onto output + per-param input sockets.

        Identity is (tap_id) for outputs and (tap_id, tap_param) for inputs.
        Labels follow tap display names; add/remove rebuilds the set.
        """
        wanted = {tap.tap_id: tap for tap in self.taps if tap.tap_id}
        wanted_params = fft_taps.TAP_CONTROL_PARAM_KEYS

        # Drop output sockets whose tap no longer exists
        for socket in list(self.outputs):
            tap_id = getattr(socket, "tap_id", "")
            if tap_id and tap_id not in wanted:
                self.outputs.remove(socket)

        # Drop param input sockets whose tap is gone or param is unknown
        for socket in list(self.inputs):
            tap_id = getattr(socket, "tap_id", "")
            tap_param = getattr(socket, "tap_param", "")
            if not tap_id:
                continue
            if tap_id not in wanted or tap_param not in wanted_params:
                self.inputs.remove(socket)

        # Create/rename sockets for each tap (grouped by tap, collection order)
        for tap in self.taps:
            if not tap.tap_id:
                continue
            label_base = fft_taps.tap_display_name(self.taps, tap)

            # --- output ---
            out = self.get_tap_socket(tap.tap_id)
            if out is None:
                out = self.outputs.new('ControlSocketType', label_base)
                out.tap_id = tap.tap_id
                out.tap_param = ""
            elif out.name != label_base:
                out.name = label_base

            # --- param inputs (fixed order from TAP_CONTROL_PARAMS) ---
            # Names MUST be unique per tap. Duplicate names ("Gain" on every
            # tap) made Blender share/migrate links when switching visibility.
            # Inactive taps stay hide=True so the node stays short.
            for prop, short, _lo, _hi in fft_taps.TAP_CONTROL_PARAMS:
                sock_name = f"{label_base} · {short}"
                inp = self.get_tap_param_input(tap.tap_id, prop)
                if inp is None:
                    inp = self.inputs.new('ControlSocketType', sock_name)
                    inp.tap_id = tap.tap_id
                    inp.tap_param = prop
                elif inp.name != sock_name:
                    inp.name = sock_name

        self.update_tap_param_input_visibility()

    def update_tap_param_input_visibility(self):
        """Show only the active tap's param inputs — keeps the node short.

        Hidden sockets keep their cables; switching taps reveals that tap's
        sockets so you can wire LFO/MIDI without stacking every tap's ports.
        Blender cannot draw real link sockets inside draw_buttons, so this
        is the supported way to keep modulation next to the selected tap UI.

        Must NOT be called from draw_buttons / draw handlers (ID write ban).
        Safe from property updates, operators, and the 60fps timer.
        """
        active_id = ""
        if 0 <= self.active_tap_index < len(self.taps):
            active_id = self.taps[self.active_tap_index].tap_id or ""

        for sock in self.inputs:
            if not getattr(sock, "tap_param", ""):
                continue
            want_hide = (getattr(sock, "tap_id", "") != active_id)
            # Avoid redundant RNA writes (also quieter for undo/depsgraph)
            if sock.hide != want_hide:
                sock.hide = want_hide

    def get_initial_params(self) -> dict:
        return {}

    def update(self):
        """Called when node tree topology changes."""
        self.migrate_legacy_band_sockets()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def draw_buttons(self, context, layout):
        # --- Reserved area for the GPU spectrum overlay -------------------
        # These empty rows create the space the overlay draws into, and
        # ui/spectrum_overlay.py locates that space by assuming it starts
        # directly below the last output socket. So this MUST be the first
        # thing drawn - anything above it (the max-analyzers warning used
        # to live here) shifts the real rows down while the overlay keeps
        # painting at the old offset, and the spectrum lands a row out of
        # alignment exactly when the warning appears.
        if self.show_spectrum:
            col = layout.column(align=True)
            col.scale_y = 1.0
            for _ in range(self.SPECTRUM_ROWS):
                col.label(text="")

        row = layout.row(align=True)
        row.prop(self, "show_spectrum", text="", icon='FCURVE')

        # The engine publishes at most MAX_FFT_ANALYZERS analyzer slots -
        # warn on nodes that fell outside them (they show the oldest
        # analyzer's spectrum instead of their own).
        try:
            from ... import shared_memory_reader
            spectra = shared_memory_reader._latest_spectra_by_node
            if spectra and self.engine_node_id not in spectra:
                layout.label(
                    text=f"Max {shared_memory_reader.MAX_FFT_ANALYZERS} live "
                         f"analyzers - showing shared data",
                    icon='ERROR')
        except Exception:
            pass

        # --- Frequency taps ------------------------------------------------
        box = layout.box()
        header = box.row(align=True)
        header.label(text="Frequency taps", icon='EYEDROPPER')

        ops = box.row(align=True)
        op = ops.operator("audionodes.add_tap", text="Add tap", icon='ADD')
        op.node_name = self.name

        if len(self.taps) > 0:
            box.template_list(
                "AUDIONODES_UL_taps", "",
                self, "taps",
                self, "active_tap_index",
                rows=min(4, max(2, len(self.taps)))
            )

            # Active tap detail — param sockets on the left mirror this panel
            # (visibility is updated via active_tap_index's update callback /
            # sync_tap_sockets — not here; draw cannot write ID data).
            if 0 <= self.active_tap_index < len(self.taps):
                tap = self.taps[self.active_tap_index]

                hint = box.row()
                hint.scale_y = 0.75
                hint.label(text="Drive via left sockets (active tap)",
                           icon='BACK')

                def _link_icon(prop: str) -> str:
                    sock = self.get_tap_param_input(tap.tap_id, prop)
                    return 'LINKED' if (sock and sock.is_linked) else 'BLANK1'

                col = box.column(align=True)
                row = col.row(align=True)
                row.label(text="", icon=_link_icon("center_hz"))
                row.prop(tap, "center_hz", text="Hz")
                row.label(text="", icon=_link_icon("width_oct"))
                row.prop(tap, "width_oct", text="Oct")
                row = col.row(align=True)
                row.label(text="", icon=_link_icon("gain"))
                row.prop(tap, "gain", text="Gain")
                row.label(text="", icon=_link_icon("smoothing"))
                row.prop(tap, "smoothing", text="Smooth")
                row = col.row(align=True)
                row.label(text="", icon=_link_icon("out_min"))
                row.prop(tap, "out_min", text="Min")
                row.label(text="", icon=_link_icon("out_max"))
                row.prop(tap, "out_max", text="Max")

                # Occasional (random) triggering
                row = col.row(align=True)
                row.prop(tap, "use_random_trigger", text="Random Trigger",
                         toggle=True)
                sub = row.row(align=True)
                sub.active = tap.use_random_trigger
                sub.label(text="", icon=_link_icon("trigger_probability"))
                sub.prop(tap, "trigger_probability", text="")

                # What is this tap driving right now?
                targets = fft_taps.get_tap_targets(self).get(
                    self.active_tap_index, [])
                tgt = box.column(align=True)
                tgt.scale_y = 0.8
                if targets:
                    tgt.label(text="Drives:", icon='LINKED')
                    for label in targets[:4]:
                        tgt.label(text="   " + label)
                    if len(targets) > 4:
                        tgt.label(text=f"   … +{len(targets) - 4} more")
                else:
                    tgt.label(text="Drives: nothing yet", icon='UNLINKED')

                # The stable driver reference for this tap - shown so it can
                # be typed by hand, and copied verbatim by the button below.
                ref = fft_taps.tap_ref(tap)
                node_ref = fft_taps.node_ref(self)
                if ref:
                    # The full expression does not fit in a node body, and a
                    # half-shown one is useless - show the two refs stacked
                    # and let the copy button hand over the real thing.
                    expr = tgt.column(align=True)
                    expr.scale_y = 0.8
                    expr.label(text=f"node: {node_ref}", icon='DRIVER')
                    expr.label(text=f"tap:  {ref}")

                actions = box.row(align=True)
                op = actions.operator("audionodes.tap_to_value_output",
                                      text="Value Output", icon='EXPORT')
                op.node_name = self.name
                op.tap_index = self.active_tap_index
                op = actions.operator("audionodes.copy_tap_driver",
                                      text="", icon='COPYDOWN')
                op.node_name = self.name
                op.tap_index = self.active_tap_index
                op.tap_ref = ref
                op.node_ref = node_ref
                op = actions.operator("audionodes.remove_tap",
                                      text="", icon='TRASH')
                op.node_name = self.name
                op.tap_index = self.active_tap_index

    def draw_buttons_ext(self, context, layout):
        """Sidebar."""
        layout.prop(self, "show_spectrum")

    # ------------------------------------------------------------------
    # Channel routing
    # ------------------------------------------------------------------

    def get_output_channel_name(self, output_name: str) -> str:
        """Get the channel name for an output socket."""
        # Tap sockets first (their names are user-defined)
        for tap in self.taps:
            if tap.name == output_name and tap.tap_id:
                return f"{self.engine_node_id}_tap_{tap.tap_id}"

        # Fixed level sockets
        base_map = {
            "Peak L": "peak_left",
            "Peak R": "peak_right",
            "RMS L": "rms_left",
            "RMS R": "rms_right",
        }
        base_name = base_map.get(output_name, "")
        if base_name:
            return f"{self.engine_node_id}_{base_name}"
        return ""
