import bpy
from bpy.props import FloatProperty, StringProperty, IntProperty
from bpy_extras.io_utils import ImportHelper
from ..core.node_base import AudioNodeBase, find_node

# Names a drum machine actually uses, so a pad reads as a sound instead of
# "Slot 3". Each pad keeps its own editable name (slot_N_name) seeded from
# this list - the kit is the user's to rename.
DEFAULT_SLOT_NAMES = ("Kick", "Snare", "Hi-Hat", "Clap",
                      "Tom", "Rim", "Perc", "FX")

NUM_SLOTS = 8


class AudioDrumSamplerNode(AudioNodeBase):
    """Drum Sampler node - 8-pad drum machine."""
    bl_idname = "AudioDrumSamplerNode"
    bl_label = "Drum Sampler"
    bl_icon = 'SEQ_SEQUENCER'
    bl_width_default = 240

    master_volume: FloatProperty(
        name="Master",
        description="Master volume",
        default=1.0,
        min=0.0,
        max=2.0,
        update=lambda self, ctx: self.send_param("master_volume", self.master_volume)
    )

    selected_pad: IntProperty(
        name="Selected Pad",
        description="Pad shown in the editor below the grid",
        default=0,
        min=0,
        max=NUM_SLOTS - 1
    )

    # Legacy name for selected_pad: -1 meant "nothing expanded", which left
    # the node with a dead strip of empty space under the grid.
    expanded_slot: IntProperty(default=-1)

    # Class-level defaults, kept for anything still reading slot_names.
    slot_names = list(DEFAULT_SLOT_NAMES)

    slot_0_name: StringProperty(name="Name", default="Kick")
    slot_0_path: StringProperty(default="")
    slot_0_volume: FloatProperty(name="Vol", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: s.send_param("slot_0_volume", s.slot_0_volume))
    slot_0_pitch: FloatProperty(name="Pitch", description="Pitch shift in semitones",
        default=0.0, min=-24.0, max=24.0,
        update=lambda s, c: s.send_param("slot_0_pitch", s.slot_0_pitch))
    slot_0_pan: FloatProperty(name="Pan", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: s.send_param("slot_0_pan", s.slot_0_pan))
    slot_1_name: StringProperty(name="Name", default="Snare")
    slot_1_path: StringProperty(default="")
    slot_1_volume: FloatProperty(name="Vol", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: s.send_param("slot_1_volume", s.slot_1_volume))
    slot_1_pitch: FloatProperty(name="Pitch", description="Pitch shift in semitones",
        default=0.0, min=-24.0, max=24.0,
        update=lambda s, c: s.send_param("slot_1_pitch", s.slot_1_pitch))
    slot_1_pan: FloatProperty(name="Pan", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: s.send_param("slot_1_pan", s.slot_1_pan))
    slot_2_name: StringProperty(name="Name", default="Hi-Hat")
    slot_2_path: StringProperty(default="")
    slot_2_volume: FloatProperty(name="Vol", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: s.send_param("slot_2_volume", s.slot_2_volume))
    slot_2_pitch: FloatProperty(name="Pitch", description="Pitch shift in semitones",
        default=0.0, min=-24.0, max=24.0,
        update=lambda s, c: s.send_param("slot_2_pitch", s.slot_2_pitch))
    slot_2_pan: FloatProperty(name="Pan", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: s.send_param("slot_2_pan", s.slot_2_pan))
    slot_3_name: StringProperty(name="Name", default="Clap")
    slot_3_path: StringProperty(default="")
    slot_3_volume: FloatProperty(name="Vol", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: s.send_param("slot_3_volume", s.slot_3_volume))
    slot_3_pitch: FloatProperty(name="Pitch", description="Pitch shift in semitones",
        default=0.0, min=-24.0, max=24.0,
        update=lambda s, c: s.send_param("slot_3_pitch", s.slot_3_pitch))
    slot_3_pan: FloatProperty(name="Pan", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: s.send_param("slot_3_pan", s.slot_3_pan))
    slot_4_name: StringProperty(name="Name", default="Tom")
    slot_4_path: StringProperty(default="")
    slot_4_volume: FloatProperty(name="Vol", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: s.send_param("slot_4_volume", s.slot_4_volume))
    slot_4_pitch: FloatProperty(name="Pitch", description="Pitch shift in semitones",
        default=0.0, min=-24.0, max=24.0,
        update=lambda s, c: s.send_param("slot_4_pitch", s.slot_4_pitch))
    slot_4_pan: FloatProperty(name="Pan", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: s.send_param("slot_4_pan", s.slot_4_pan))
    slot_5_name: StringProperty(name="Name", default="Rim")
    slot_5_path: StringProperty(default="")
    slot_5_volume: FloatProperty(name="Vol", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: s.send_param("slot_5_volume", s.slot_5_volume))
    slot_5_pitch: FloatProperty(name="Pitch", description="Pitch shift in semitones",
        default=0.0, min=-24.0, max=24.0,
        update=lambda s, c: s.send_param("slot_5_pitch", s.slot_5_pitch))
    slot_5_pan: FloatProperty(name="Pan", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: s.send_param("slot_5_pan", s.slot_5_pan))
    slot_6_name: StringProperty(name="Name", default="Perc")
    slot_6_path: StringProperty(default="")
    slot_6_volume: FloatProperty(name="Vol", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: s.send_param("slot_6_volume", s.slot_6_volume))
    slot_6_pitch: FloatProperty(name="Pitch", description="Pitch shift in semitones",
        default=0.0, min=-24.0, max=24.0,
        update=lambda s, c: s.send_param("slot_6_pitch", s.slot_6_pitch))
    slot_6_pan: FloatProperty(name="Pan", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: s.send_param("slot_6_pan", s.slot_6_pan))
    slot_7_name: StringProperty(name="Name", default="FX")
    slot_7_path: StringProperty(default="")
    slot_7_volume: FloatProperty(name="Vol", default=1.0, min=0.0, max=2.0,
        update=lambda s, c: s.send_param("slot_7_volume", s.slot_7_volume))
    slot_7_pitch: FloatProperty(name="Pitch", description="Pitch shift in semitones",
        default=0.0, min=-24.0, max=24.0,
        update=lambda s, c: s.send_param("slot_7_pitch", s.slot_7_pitch))
    slot_7_pan: FloatProperty(name="Pan", default=0.0, min=-1.0, max=1.0,
        update=lambda s, c: s.send_param("slot_7_pan", s.slot_7_pan))

    @property
    def node_type(self) -> str:
        return "drum_sampler"

    def init(self, context):
        # Trigger inputs, named after the pads they fire.
        for i in range(NUM_SLOTS):
            self.inputs.new('ControlSocketType', f"Trig {i + 1}")

        # Audio output
        self.outputs.new('AudioSocketType', "Output")

        super().init(context)

    # ---- Slot helpers ----------------------------------------------------

    def slot_name(self, slot: int) -> str:
        """Display name of a pad, falling back to the kit default."""
        name = getattr(self, f"slot_{slot}_name", "").strip()
        if name:
            return name
        if 0 <= slot < len(DEFAULT_SLOT_NAMES):
            return DEFAULT_SLOT_NAMES[slot]
        return f"Pad {slot + 1}"

    def slot_has_sample(self, slot: int) -> bool:
        return bool(getattr(self, f"slot_{slot}_path", ""))

    def active_pad(self) -> int:
        """Pad currently being edited, tolerating the legacy -1 sentinel."""
        pad = self.selected_pad
        if not 0 <= pad < NUM_SLOTS:
            return 0
        return pad

    def get_initial_params(self) -> dict:
        """Everything the engine needs to match what the node is showing.

        This used to carry master_volume alone. Per-pad volume, pitch and
        pan were saved in the .blend and drawn in the UI but never sent, so
        a reopened project played every pad at the engine's defaults while
        the sliders confidently showed the saved values - and only moving a
        slider (which fires its update callback) made the two agree.
        """
        params = {"master_volume": self.master_volume}
        for slot in range(NUM_SLOTS):
            for prop in ("volume", "pitch", "pan"):
                name = f"slot_{slot}_{prop}"
                params[name] = float(getattr(self, name, 0.0))
        return params

    # ---- UI --------------------------------------------------------------

    def draw_buttons(self, context, layout):
        from ... import sample_loading

        pad = self.active_pad()

        row = layout.row(align=True)
        row.prop(self, "master_volume", text="Master")

        # 2 x 4 pad grid. One button per pad: click fires it AND selects it
        # for editing. The old layout stacked a pad button, a folder button
        # and a status line for all eight slots, so the node was about
        # twenty rows tall before a single setting was visible.
        grid = layout.column(align=True)
        for row_index in range(2):
            pad_row = grid.row(align=True)
            pad_row.scale_y = 1.5
            for col in range(4):
                slot = row_index * 4 + col
                cell = pad_row.row(align=True)
                cell.active = self.slot_has_sample(slot)
                op = cell.operator(
                    "audionodes.trigger_drum_slot",
                    text=self.slot_name(slot),
                    depress=(slot == pad),
                )
                op.node_name = self.name
                op.tree_name = self.id_data.name
                op.slot = slot

        # Editor for the selected pad only.
        box = layout.box()

        row = box.row(align=True)
        row.prop(self, f"slot_{pad}_name", text="")
        op = row.operator("audionodes.load_drum_sample", text="", icon='FILE_FOLDER')
        op.node_name = self.name
        op.tree_name = self.id_data.name
        op.slot = pad

        path = getattr(self, f"slot_{pad}_path", "")
        if path:
            sample_loading.draw_status(box, self, path, pad)
        else:
            box.label(text="No sample loaded", icon='DOT')

        row = box.row(align=True)
        row.prop(self, f"slot_{pad}_volume")
        row.prop(self, f"slot_{pad}_pitch")
        row.prop(self, f"slot_{pad}_pan")

        # Pads other than the selected one still need to report a failed
        # load - just not with a line each.
        broken = [self.slot_name(s) for s in range(NUM_SLOTS)
                  if s != pad and sample_loading.slot_failed(self, s)]
        if broken:
            warn = layout.row()
            warn.alert = True
            warn.label(text="Failed: " + ", ".join(broken), icon='ERROR')


class AUDIONODES_OT_trigger_drum_slot(bpy.types.Operator):
    bl_idname = "audionodes.trigger_drum_slot"
    bl_label = "Trigger Drum Pad"
    bl_options = {'INTERNAL'}

    node_name: StringProperty()
    tree_name: StringProperty()
    slot: IntProperty()

    def execute(self, context):
        node = find_node(context, self.node_name, bl_idname='AudioDrumSamplerNode',
                         tree_name=self.tree_name)
        if node:
            # send_event, not send_param: parameter updates coalesce per
            # (node, param) in a ~33ms window, so a fast double hit on the
            # same pad became a single hit.
            node.send_event(f"trigger_{self.slot}", 1.0)
            node.selected_pad = self.slot
        return {'FINISHED'}


class AUDIONODES_OT_load_drum_sample(bpy.types.Operator, ImportHelper):
    bl_idname = "audionodes.load_drum_sample"
    bl_label = "Load Drum Sample"

    filter_glob: StringProperty(
        default="*.wav;*.mp3;*.aiff;*.flac;*.ogg",
        options={'HIDDEN'}
    )

    node_name: StringProperty()
    tree_name: StringProperty()
    slot: IntProperty()

    def execute(self, context):
        from ... import command_client
        from ... import engine_manager

        node = find_node(context, self.node_name, bl_idname='AudioDrumSamplerNode',
                         tree_name=self.tree_name)
        if node:
            setattr(node, f"slot_{self.slot}_path", self.filepath)
            if engine_manager.is_engine_running():
                command_client.load_drum_sample(
                    node.engine_node_id, self.slot, bpy.path.abspath(self.filepath))
                # The engine creates the slot fresh; re-send this pad's
                # settings so a loaded sample plays at the pitch shown.
                for prop in ("volume", "pitch", "pan"):
                    name = f"slot_{self.slot}_{prop}"
                    command_client.set_parameter(node.engine_node_id, name,
                                                 float(getattr(node, name)))
            self.report({'INFO'}, f"Loading sample into {node.slot_name(self.slot)}")
        return {'FINISHED'}
