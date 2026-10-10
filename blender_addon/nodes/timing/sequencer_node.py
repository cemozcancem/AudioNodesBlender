import bpy
from bpy.props import (
    FloatProperty, IntProperty, BoolProperty,
    EnumProperty, StringProperty
)
from ..core.node_base import AudioNodeBase, find_node

MAX_STEPS = 64

# Grid resolutions offered per beat. 3 and 6 are what make compound meters
# (6/8, 9/8, 12/8) land on real triplet subdivisions instead of an
# approximation.
STEPS_PER_BEAT_ITEMS = [
    ('1', "1", "One step per beat"),
    ('2', "2", "Two steps per beat"),
    ('3', "3", "Triplet subdivision"),
    ('4', "4", "Four steps per beat"),
    ('6', "6", "Six steps per beat"),
    ('8', "8", "Eight steps per beat"),
]


def choose_grid_and_bars(legacy_steps, beats):
    """Grid + bars that reproduce a legacy fixed step count, or None.

    Pure arithmetic, kept out of the node class so it can be tested
    without Blender. Prefers a grid close to the familiar 4 steps per
    beat, which is what the old fixed 16-step default effectively was.
    """
    if legacy_steps <= 0 or beats <= 0:
        return None
    for per_beat in (4, 2, 8, 3, 6, 1):
        if legacy_steps % (beats * per_beat):
            continue
        bars = legacy_steps // (beats * per_beat)
        if 1 <= bars <= 8:
            return per_beat, bars
    return None


def _timing_changed(self, context):
    """Push the whole meter to the engine and keep the pattern long enough.

    The time signature is sent as a unit because the engine derives both
    the step length and the LOOP LENGTH from it - changing the numerator
    alone used to be stored and never read, which is why setting 7/8 did
    nothing you could hear.
    """
    self._ensure_pattern_length()
    self.send_param("beats_per_bar", float(self.time_sig_num))
    self.send_param("beat_unit", float(int(self.time_sig_denom)))
    self.send_param("steps_per_beat", float(int(self.steps_per_beat)))
    self.send_param("bars", float(self.bars))
    self.send_param("fit_beats", self.effective_fit_beats())


class AudioSequencerNode(AudioNodeBase):
    """Step Sequencer node - generates trigger patterns with time signature support."""
    bl_idname = "AudioSequencerNode"
    bl_label = "Step Sequencer"
    bl_icon = 'SEQ_SEQUENCER'
    bl_width_default = 300

    bpm: FloatProperty(
        name="BPM",
        description="Tempo in quarter-note beats per minute",
        default=120.0,
        min=20.0,
        max=300.0,
        update=lambda self, ctx: self.send_param("bpm", self.bpm)
    )

    time_sig_num: IntProperty(
        name="Beats",
        description="Time signature numerator - beats in a bar. "
                    "Sets how long the loop is, not just what the label says",
        default=4,
        min=1,
        max=16,
        update=_timing_changed
    )

    time_sig_denom: EnumProperty(
        name="Note",
        description="Time signature denominator - which note value is one beat",
        items=[
            ('4', "4", "Quarter note beats"),
            ('8', "8", "Eighth note beats"),
            ('16', "16", "Sixteenth note beats"),
        ],
        default='4',
        update=_timing_changed
    )

    steps_per_beat: EnumProperty(
        name="Grid",
        description="How many sequencer steps make up one beat",
        items=STEPS_PER_BEAT_ITEMS,
        default='4',
        update=_timing_changed
    )

    bars: IntProperty(
        name="Bars",
        description="Bars in the loop",
        default=1,
        min=1,
        max=8,
        update=_timing_changed
    )

    # Legacy: the fixed step count this node had before the loop length was
    # derived from the time signature. Kept (and hidden) so an existing
    # .blend can be migrated to the equivalent grid + bars exactly once.
    step_count: EnumProperty(
        name="Steps",
        items=[('8', "8", ""), ('16', "16", ""),
               ('32', "32", ""), ('64', "64", "")],
        default='16',
    )

    meter_migrated: BoolProperty(default=False)

    polyrhythm: BoolProperty(
        name="Polyrhythm",
        description="Squeeze the whole loop into a fixed number of beats, so "
                    "its steps cut across the pulse instead of following it. "
                    "7 steps fitted into 4 beats is a 7:4 cross-rhythm",
        default=False,
        update=_timing_changed
    )

    fit_beats: FloatProperty(
        name="Fit to",
        description="Beats (quarter notes at this node's BPM) that the whole "
                    "loop spans when Polyrhythm is on",
        default=4.0,
        min=0.25,
        max=64.0,
        update=_timing_changed
    )

    swing: FloatProperty(
        name="Swing",
        description="Swing amount (0-100%)",
        default=0.0,
        min=0.0,
        max=100.0,
        subtype='PERCENTAGE',
        update=lambda self, ctx: self.send_param("swing", self.swing / 100.0)
    )

    gate_length: FloatProperty(
        name="Gate",
        description="Gate length (0-100%)",
        default=50.0,
        min=1.0,
        max=100.0,
        subtype='PERCENTAGE',
        update=lambda self, ctx: self.send_param("gate_length", self.gate_length / 100.0)
    )

    is_playing: BoolProperty(
        name="Playing",
        description="Sequencer is playing",
        default=False
    )

    # Step pattern stored as a string of 0s and 1s
    pattern_lane_0: StringProperty(
        name="Pattern",
        default="1000100010001000",
        update=lambda self, ctx: self._send_pattern(0)
    )

    @property
    def node_type(self) -> str:
        return "sequencer"

    # ---- Meter -----------------------------------------------------------

    @property
    def total_steps(self) -> int:
        """Loop length in steps, straight from the time signature."""
        steps = self.time_sig_num * int(self.steps_per_beat) * self.bars
        return max(1, min(MAX_STEPS, steps))

    def migrate_meter(self):
        """Map a pre-time-signature step count onto grid + bars, once.

        Old files stored a free-running 8/16/32/64 step count that had
        nothing to do with the displayed time signature. Reproduce the same
        number of steps where the meter allows it, so an existing pattern
        keeps playing exactly as it did.

        Called from the file-load handler, never from draw_buttons: it
        writes RNA (and fires update callbacks), which has no business
        happening inside a redraw.
        """
        if self.meter_migrated:
            return
        self.meter_migrated = True

        try:
            legacy = int(self.step_count)
        except (TypeError, ValueError):
            return
        if legacy == self.total_steps:
            return

        match = choose_grid_and_bars(legacy, max(1, self.time_sig_num))
        if match is not None:
            per_beat, bars = match
            self.steps_per_beat = str(per_beat)
            self.bars = bars

    def _ensure_pattern_length(self):
        """Keep the stored pattern at full length.

        The pattern is held at MAX_STEPS internally and only the first
        total_steps are shown. Truncating it to the visible length would
        silently destroy steps every time the meter shrank - switch 4/4 to
        7/8 and back and half the pattern would be gone.
        """
        pattern = self.pattern_lane_0
        if len(pattern) < MAX_STEPS:
            self.pattern_lane_0 = pattern.ljust(MAX_STEPS, '0')

    def effective_fit_beats(self) -> float:
        """Beats the loop spans, or 0.0 when it just follows its meter."""
        return float(self.fit_beats) if self.polyrhythm else 0.0

    def polyrhythm_ratio(self):
        """(steps, beats) reduced - the cross-rhythm this node plays."""
        from math import gcd
        steps = self.total_steps
        beats = self.fit_beats
        if beats <= 0:
            return None
        # Only reduce when the span is a whole number of beats; 7:4.5 is
        # perfectly playable, it just has no integer ratio to name.
        if abs(beats - round(beats)) > 1e-6:
            return (steps, beats)
        whole = int(round(beats))
        divisor = gcd(steps, whole) or 1
        return (steps // divisor, whole // divisor)

    def steps_beyond_loop(self):
        """Active steps that sit past the end of the loop, so never play.

        The pattern is stored at full length and the loop length is derived
        from the meter, so shrinking the meter (or the Grid) can leave hits
        stranded past the end. They are kept rather than destroyed - switch
        the meter back and they return - but until this is surfaced the only
        symptom is a sequencer that has gone quiet for no visible reason.
        """
        pattern = self.pattern_lane_0.ljust(MAX_STEPS, '0')
        total = self.total_steps
        return [i for i in range(total, MAX_STEPS) if pattern[i] == '1']

    def fold_steps_into_loop(self) -> int:
        """Wrap stranded steps back into the loop. Returns how many moved."""
        total = self.total_steps
        pattern = list(self.pattern_lane_0.ljust(MAX_STEPS, '0'))
        moved = 0
        for i in range(total, MAX_STEPS):
            if pattern[i] == '1':
                pattern[i] = '0'
                pattern[i % total] = '1'
                moved += 1
        if moved:
            self.pattern_lane_0 = ''.join(pattern)
        return moved

    def clear_steps_beyond_loop(self) -> int:
        """Discard stranded steps. Returns how many were removed."""
        total = self.total_steps
        pattern = list(self.pattern_lane_0.ljust(MAX_STEPS, '0'))
        removed = 0
        for i in range(total, MAX_STEPS):
            if pattern[i] == '1':
                pattern[i] = '0'
                removed += 1
        if removed:
            self.pattern_lane_0 = ''.join(pattern)
        return removed

    def _send_pattern(self, lane: int):
        """Send pattern to engine as a 64-bit integer.

        Uses the dedicated set_pattern command: sending the mask as a float
        through set_param loses precision beyond 24 bits (steps >= 25).
        """
        from ... import command_client
        from ... import engine_manager

        if not engine_manager.is_engine_running():
            return

        pattern_str = getattr(self, f"pattern_lane_{lane}", "0" * MAX_STEPS)
        pattern_int = int(pattern_str[::-1], 2) if pattern_str else 0
        command_client.set_pattern(self.engine_node_id, lane, pattern_int)

    def _toggle_step(self, step: int, lane: int = 0):
        """Toggle a step in the pattern."""
        pattern = list(self.pattern_lane_0.ljust(MAX_STEPS, '0'))[:MAX_STEPS]
        if 0 <= step < MAX_STEPS:
            pattern[step] = '0' if pattern[step] == '1' else '1'
            self.pattern_lane_0 = ''.join(pattern)

    def init(self, context):
        # Transport inputs (e.g. Bang → Play to start from a shared trigger)
        self.inputs.new('ControlSocketType', "Play")
        self.inputs.new('ControlSocketType', "Stop")
        # Trigger outputs for each lane
        self.outputs.new('ControlSocketType', "Trigger")
        self.outputs.new('ControlSocketType', "Step")
        self.outputs.new('ControlSocketType', "Playing")

        self.meter_migrated = True  # brand new node: nothing to migrate
        super().init(context)

    def _ensure_transport_sockets(self):
        """Add Play/Stop inputs on nodes created before those sockets existed.

        Blender only runs init() when a node is first added, so saved
        sequencers would otherwise stay without the new transport ports.
        """
        if self.inputs.get("Play") is None:
            self.inputs.new('ControlSocketType', "Play")
            self.inputs.move(len(self.inputs) - 1, 0)
        if self.inputs.get("Stop") is None:
            self.inputs.new('ControlSocketType', "Stop")
            play_idx = self.inputs.find("Play")
            dest = (play_idx + 1) if play_idx >= 0 else 0
            self.inputs.move(len(self.inputs) - 1, dest)

    def register_with_engine(self):
        super().register_with_engine()
        # Pattern is sent separately (as an exact integer) right after the
        # queued create_node; the sender thread preserves ordering.
        self._send_pattern(0)

    def get_initial_params(self) -> dict:
        return {
            "bpm": self.bpm,
            "beats_per_bar": float(self.time_sig_num),
            "beat_unit": float(int(self.time_sig_denom)),
            "steps_per_beat": float(int(self.steps_per_beat)),
            "bars": float(self.bars),
            "fit_beats": self.effective_fit_beats(),
            "swing": self.swing / 100.0,
            "gate_length": self.gate_length / 100.0,
        }

    def get_output_channel_name(self, output_name: str) -> str:
        """This node's OWN transport channels.

        These used to be the global `seq_trigger_active` / `seq_current_step`
        / `seq_is_playing` channels, which the engine fills from the primary
        (oldest) sequencer - so with two Step Sequencer nodes the second
        one's outputs carried the first one's transport. Every sequencer now
        publishes its own slot; see
        shared_memory_reader._update_sequencer_outputs.
        """
        suffix_map = {
            "Trigger": "trigger",
            "Step": "step",
            "Playing": "playing",
        }
        suffix = suffix_map.get(output_name, "")
        if not suffix or not self.engine_node_id:
            return ""
        return f"{self.engine_node_id}_{suffix}"

    def get_current_step(self, context) -> int:
        """Live step index for this sequencer, from its own channel."""
        try:
            audio_data = context.scene.audio_nodes_data
            channel = audio_data.channels.get(f"{self.engine_node_id}_step")
            if channel is not None:
                return int(channel.value)
        except Exception:
            pass
        return 0

    # ---- UI --------------------------------------------------------------

    def draw_buttons(self, context, layout):
        self._ensure_transport_sockets()

        per_beat = int(self.steps_per_beat)
        num_steps = self.total_steps

        # Transport controls
        row = layout.row(align=True)
        row.scale_y = 1.5

        if self.is_playing:
            op = row.operator("audionodes.sequencer_stop", text="", icon='PAUSE')
        else:
            op = row.operator("audionodes.sequencer_play", text="", icon='PLAY')
        op.node_name = self.name
        op.tree_name = self.id_data.name

        op = row.operator("audionodes.sequencer_reset", text="", icon='REW')
        op.node_name = self.name
        op.tree_name = self.id_data.name

        layout.separator()

        layout.prop(self, "bpm")

        # Time signature reads like a time signature: 7 / 8.
        row = layout.row(align=True)
        row.label(text="Meter:")
        row.prop(self, "time_sig_num", text="")
        row.label(text="/")
        row.prop(self, "time_sig_denom", text="")

        row = layout.row(align=True)
        row.prop(self, "steps_per_beat", text="Grid")
        row.prop(self, "bars")

        # Polyrhythm: the loop spans a fixed number of beats, so the steps
        # divide that span instead of inheriting the pulse.
        row = layout.row(align=True)
        row.prop(self, "polyrhythm", text="")
        sub = row.row(align=True)
        sub.enabled = self.polyrhythm
        sub.prop(self, "fit_beats", text="Fit to beats")

        # The loop length is derived, so say what it came out as.
        note_name = {'4': "quarter", '8': "eighth", '16': "sixteenth"}.get(
            self.time_sig_denom, "beat")
        if self.polyrhythm:
            ratio = self.polyrhythm_ratio()
            spb = 60.0 / max(self.bpm, 1e-6)
            step_s = spb * self.fit_beats / max(num_steps, 1)
            if ratio and isinstance(ratio[1], int):
                text = (f"{num_steps} steps across {self.fit_beats:g} beats "
                        f"\u2014 {ratio[0]}:{ratio[1]}")
            else:
                text = f"{num_steps} steps across {self.fit_beats:g} beats"
            layout.label(text=text, icon='MOD_MULTIRES')
            layout.label(text=f"step = {step_s * 1000:.0f} ms "
                              f"(pulse = {spb * 1000:.0f} ms)")
        else:
            layout.label(
                text=f"{num_steps} steps \u00b7 {per_beat} per {note_name}",
                icon='TIME')

        row = layout.row()
        row.prop(self, "swing")
        row.prop(self, "gate_length")

        layout.separator()

        self._draw_step_grid(context, layout, per_beat, num_steps)

    def _draw_step_grid(self, context, layout, per_beat, num_steps):
        """Step buttons grouped into beats, so the meter is visible.

        The grid used to be a flat run of eight-step rows with a label every
        fourth step - a 4/4 shape no matter what the time signature said.
        Steps are now grouped per beat and wrapped per bar, so 7/8 looks
        like seven beats and 6/8 looks like two groups of three.
        """
        box = layout.box()

        pattern = self.pattern_lane_0.ljust(MAX_STEPS, '0')
        current_step = self.get_current_step(context)
        beats_per_bar = max(1, self.time_sig_num)

        # Aim for roughly eight buttons per UI row, never splitting a beat.
        beats_per_row = max(1, min(beats_per_bar, max(1, 8 // max(1, per_beat))))

        header = box.row()
        header.label(text="Steps", icon='SEQ_SEQUENCER')
        if self.is_playing:
            beat = (current_step // per_beat) % beats_per_bar + 1
            bar = current_step // (per_beat * beats_per_bar) + 1
            header.label(text=f"Bar {bar} · Beat {beat}")

        step = 0
        for bar in range(self.bars):
            if self.bars > 1:
                box.label(text=f"Bar {bar + 1}")
            for row_start in range(0, beats_per_bar, beats_per_row):
                row = box.row(align=True)
                row.scale_y = 1.2
                for beat in range(row_start,
                                  min(row_start + beats_per_row, beats_per_bar)):
                    group = row.row(align=True)
                    for sub in range(per_beat):
                        if step >= num_steps:
                            break
                        is_active = pattern[step] == '1'
                        is_current = step == current_step and self.is_playing

                        cell = group.row(align=True)
                        cell.alert = is_current
                        # The first step of every beat carries the beat
                        # number; the rest stay blank so the groups read as
                        # groups.
                        label = str(beat + 1) if sub == 0 else ""

                        op = cell.operator(
                            "audionodes.toggle_step",
                            text=label,
                            depress=is_active
                        )
                        op.node_name = self.name
                        op.tree_name = self.id_data.name
                        op.step = step
                        step += 1
                    # Visual gap between beats
                    row.separator(factor=0.4)

        self._draw_stranded_steps(box, pattern, num_steps)

    def _draw_stranded_steps(self, box, pattern, num_steps):
        """Report steps the meter has pushed outside the loop.

        Without this a shrinking loop silently swallows hits: a sequencer
        whose only step ends up past the end plays nothing at all, and the
        node looks perfectly healthy while doing it.
        """
        beyond = self.steps_beyond_loop()
        if not beyond:
            return

        col = box.column(align=True)
        row = col.row()
        row.alert = True
        plural = "s" if len(beyond) != 1 else ""
        row.label(text=f"{len(beyond)} step{plural} past the {num_steps}-step loop",
                  icon='ERROR')

        if not any(pattern[i] == '1' for i in range(num_steps)):
            silent = col.row()
            silent.alert = True
            silent.label(text="This sequencer never fires", icon='MUTE_IPO_ON')

        col.label(text="Steps " + ", ".join(str(i + 1) for i in beyond[:8])
                       + ("..." if len(beyond) > 8 else ""))

        ops = col.row(align=True)
        op = ops.operator("audionodes.fold_steps_into_loop",
                          text="Fold into loop", icon='LOOP_BACK')
        op.node_name = self.name
        op.tree_name = self.id_data.name
        op = ops.operator("audionodes.clear_steps_beyond_loop",
                          text="Discard", icon='X')
        op.node_name = self.name
        op.tree_name = self.id_data.name


class AUDIONODES_OT_sequencer_play(bpy.types.Operator):
    bl_idname = "audionodes.sequencer_play"
    bl_label = "Play Sequencer"
    bl_options = {'INTERNAL'}

    node_name: StringProperty()
    tree_name: StringProperty()

    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioSequencerNode',
                         tree_name=self.tree_name)
        if node:
            node.is_playing = True
            node.send_event("play", 1.0)
        return {'FINISHED'}


class AUDIONODES_OT_sequencer_stop(bpy.types.Operator):
    bl_idname = "audionodes.sequencer_stop"
    bl_label = "Stop Sequencer"
    bl_options = {'INTERNAL'}

    node_name: StringProperty()
    tree_name: StringProperty()

    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioSequencerNode',
                         tree_name=self.tree_name)
        if node:
            node.is_playing = False
            node.send_event("stop", 1.0)
        return {'FINISHED'}


class AUDIONODES_OT_sequencer_reset(bpy.types.Operator):
    bl_idname = "audionodes.sequencer_reset"
    bl_label = "Reset Sequencer"
    bl_options = {'INTERNAL'}

    node_name: StringProperty()
    tree_name: StringProperty()

    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioSequencerNode',
                         tree_name=self.tree_name)
        if node:
            node.send_event("stop", 1.0)
            node.is_playing = False
        return {'FINISHED'}


class AUDIONODES_OT_fold_steps_into_loop(bpy.types.Operator):
    """Wrap steps that fall past the end of the loop back into it"""
    bl_idname = "audionodes.fold_steps_into_loop"
    bl_label = "Fold Steps Into Loop"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: StringProperty()
    tree_name: StringProperty()

    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioSequencerNode',
                         tree_name=self.tree_name)
        if node is None:
            return {'CANCELLED'}
        moved = node.fold_steps_into_loop()
        self.report({'INFO'}, f"Folded {moved} step(s) back into the loop")
        return {'FINISHED'}


class AUDIONODES_OT_clear_steps_beyond_loop(bpy.types.Operator):
    """Remove steps that fall past the end of the loop"""
    bl_idname = "audionodes.clear_steps_beyond_loop"
    bl_label = "Discard Steps Beyond Loop"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: StringProperty()
    tree_name: StringProperty()

    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioSequencerNode',
                         tree_name=self.tree_name)
        if node is None:
            return {'CANCELLED'}
        removed = node.clear_steps_beyond_loop()
        self.report({'INFO'}, f"Removed {removed} step(s) beyond the loop")
        return {'FINISHED'}


class AUDIONODES_OT_toggle_step(bpy.types.Operator):
    bl_idname = "audionodes.toggle_step"
    bl_label = "Toggle Step"
    bl_options = {'INTERNAL'}

    node_name: StringProperty()
    tree_name: StringProperty()
    step: IntProperty()

    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioSequencerNode',
                         tree_name=self.tree_name)
        if node:
            node._toggle_step(self.step)
        return {'FINISHED'}
