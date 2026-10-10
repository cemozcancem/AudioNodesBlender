import bpy
from bpy.props import FloatVectorProperty, BoolVectorProperty, FloatProperty
from ..core.node_base import AudioNodeBase


class AudioMixerNode(AudioNodeBase):
    """8-channel audio mixer with per-channel level/pan and control inputs."""
    bl_idname = "AudioMixerNode"
    bl_label = "8ch Mixer"
    bl_icon = 'SOUND'
    bl_width_default = 240

    channels_expanded: BoolVectorProperty(
        name="Expanded",
        description="Show this channel's Level/Pan control inputs",
        size=8,
        default=(False,) * 8,
        update=lambda self, ctx: self.update_channel_socket_visibility()
    )

    channel_levels: FloatVectorProperty(
        name="Levels",
        size=8,
        default=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=2.0,
        update=lambda self, ctx: self._send_all_params()
    )

    channel_pans: FloatVectorProperty(
        name="Pans",
        size=8,
        default=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        min=-1.0,
        max=1.0,
        update=lambda self, ctx: self._send_all_params()
    )

    channel_mutes: BoolVectorProperty(
        name="Mutes",
        size=8,
        default=(False, False, False, False, False, False, False, False),
        update=lambda self, ctx: self._send_all_params()
    )

    level_mod_depths: FloatVectorProperty(
        name="Level Mod",
        size=8,
        default=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=2.0
    )

    pan_mod_depths: FloatVectorProperty(
        name="Pan Mod",
        size=8,
        default=(1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0
    )

    master_level: FloatProperty(
        name="Master",
        default=1.0,
        min=0.0,
        max=2.0,
        update=lambda self, ctx: self.send_param("master_level", self.master_level)
    )

    @property
    def node_type(self) -> str:
        return "mixer"

    def init(self, context):
        for i in range(8):
            self.inputs.new('AudioSocketType', f"Input {i + 1}")
        for i in range(8):
            self.inputs.new('ControlSocketType', f"Ch{i + 1} Level")
            self.inputs.new('ControlSocketType', f"Ch{i + 1} Pan")
        self.outputs.new('AudioSocketType', "Output")
        super().init(context)
        # Start collapsed - nothing is linked yet, so this hides all 16.
        self.update_channel_socket_visibility()

    def update_channel_socket_visibility(self):
        """Show a channel's Level/Pan inputs only when it earns the space.

        Eight audio inputs plus sixteen always-visible control inputs made
        this node several times taller than anything else in the editor, on
        a fresh drop, before the user had wired a single thing.

        A channel's control sockets show when it is expanded OR when either
        socket is already linked - so existing wiring never disappears, and
        an untouched mixer is just eight inputs and an output. Hidden
        sockets keep their cables, exactly like the FFT Analyzer's per-tap
        parameter inputs.

        Must NOT be called from draw_buttons (ID write ban). Safe from
        property updates, operators and update().
        """
        for i in range(8):
            level = self.inputs.get(f"Ch{i + 1} Level")
            pan = self.inputs.get(f"Ch{i + 1} Pan")
            linked = bool((level and level.is_linked) or (pan and pan.is_linked))
            want_hide = not (self.channels_expanded[i] or linked)
            for sock in (level, pan):
                # Avoid redundant RNA writes (quieter for undo/depsgraph)
                if sock is not None and sock.hide != want_hide:
                    sock.hide = want_hide

    def update(self):
        """Tree topology changed - a newly wired channel must reveal itself."""
        try:
            self.update_channel_socket_visibility()
        except Exception:
            pass

    def _send_all_params(self):
        for i in range(8):
            self.send_param(f"ch{i + 1}_level", self.channel_levels[i])
            self.send_param(f"ch{i + 1}_pan", self.channel_pans[i])
            self.send_param(f"ch{i + 1}_mute", 1.0 if self.channel_mutes[i] else 0.0)

    def get_initial_params(self) -> dict:
        params = {"master_level": self.master_level}
        for i in range(8):
            params[f"ch{i + 1}_level"] = self.channel_levels[i]
            params[f"ch{i + 1}_pan"] = self.channel_pans[i]
            params[f"ch{i + 1}_mute"] = 1.0 if self.channel_mutes[i] else 0.0
        return params

    def get_modulatable_params(self) -> dict:
        params = {}
        for i in range(8):
            channel = i + 1
            params[f"Ch{channel} Level"] = {
                "param": f"ch{channel}_level",
                "base": self.channel_levels[i],
                "min": 0.0,
                "max": 2.0,
                "mod_depth": self.level_mod_depths[i],
            }
            params[f"Ch{channel} Pan"] = {
                "param": f"ch{channel}_pan",
                "base": self.channel_pans[i],
                "min": -1.0,
                "max": 1.0,
                "mod_depth": self.pan_mod_depths[i],
            }
        return params

    def draw_buttons(self, context, layout):
        layout.prop(self, "master_level")
        box = layout.box()
        box.label(text="Channels")
        for i in range(8):
            level_socket = self.inputs.get(f"Ch{i + 1} Level")
            pan_socket = self.inputs.get(f"Ch{i + 1} Pan")
            linked = bool((level_socket and level_socket.is_linked)
                          or (pan_socket and pan_socket.is_linked))

            row = box.row(align=True)
            # Disclosure toggle reveals this channel's control sockets on
            # the left edge of the node. A linked channel is shown anyway,
            # so its arrow is drawn inactive rather than misleading.
            tri = row.row(align=True)
            tri.active = not linked
            tri.prop(
                self, "channels_expanded", index=i, text="",
                icon=('DISCLOSURE_TRI_DOWN'
                      if (self.channels_expanded[i] or linked)
                      else 'DISCLOSURE_TRI_RIGHT'),
                emboss=False,
            )
            row.label(text=f"{i + 1}")
            row.prop(self, "channel_levels", index=i, text="Lvl")
            row.prop(self, "channel_pans", index=i, text="Pan")
            row.prop(self, "channel_mutes", index=i, text="M")

            # Mod depth only matters once something is driving the param.
            if linked:
                sub = box.row(align=True)
                sub.scale_y = 0.85
                sub.label(text="", icon='BLANK1')
                if level_socket and level_socket.is_linked:
                    sub.prop(self, "level_mod_depths", index=i, text="L Mod")
                if pan_socket and pan_socket.is_linked:
                    sub.prop(self, "pan_mod_depths", index=i, text="P Mod")

