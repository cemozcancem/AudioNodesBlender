import bpy
import sys
from bpy.props import StringProperty, IntProperty, BoolProperty

# Transient state for button feedback
_mic_status_message = ""
_mic_status_ok = False


def _on_permission_done(granted):
    global _mic_status_message, _mic_status_ok
    _mic_status_ok = granted
    _mic_status_message = "Access granted." if granted else (
        "Access denied. Enable in System Settings > Privacy & Security > Microphone."
    )
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'PREFERENCES':
                area.tag_redraw()


class AUDIONODES_OT_patch_plist(bpy.types.Operator):
    """Add NSMicrophoneUsageDescription to Blender's Info.plist"""
    bl_idname = "audionodes.patch_plist"
    bl_label = "Patch Info.plist"

    def execute(self, context):
        global _mic_status_message, _mic_status_ok
        from . import permissions
        ok, msg = permissions.patch_info_plist()
        _mic_status_ok = ok
        _mic_status_message = msg
        return {'FINISHED'}


class AUDIONODES_OT_reset_tcc(bpy.types.Operator):
    """Reset Blender's microphone TCC entry so macOS can show the permission dialog again"""
    bl_idname = "audionodes.reset_tcc"
    bl_label = "Reset Microphone Permission"

    def execute(self, context):
        global _mic_status_message, _mic_status_ok
        from . import permissions
        ok, msg = permissions.reset_tcc_permission()
        _mic_status_ok = ok
        _mic_status_message = msg
        return {'FINISHED'}


class AUDIONODES_OT_request_mic_permission(bpy.types.Operator):
    """Request macOS microphone access for Blender"""
    bl_idname = "audionodes.request_mic_permission"
    bl_label = "Request Microphone Access"

    def execute(self, context):
        global _mic_status_message, _mic_status_ok
        from . import permissions

        # Ensure the plist is patched first; without NSMicrophoneUsageDescription
        # macOS silently ignores the AVFoundation request.
        patched = permissions.check_plist_patched()
        if patched is False:
            ok, msg = permissions.patch_info_plist()
            _mic_status_ok = ok
            if not ok:
                _mic_status_message = msg
                return {'FINISHED'}
            # Patched — user must restart before macOS will honour the request
            _mic_status_ok = False
            _mic_status_message = msg  # already contains "restart Blender" instructions
            return {'FINISHED'}

        current = permissions.check_mic_status()

        if current == 'authorized':
            _mic_status_ok = True
            _mic_status_message = "Microphone access already granted."
            return {'FINISHED'}

        if current == 'denied':
            # TCC has a stale denied entry — reset it so the dialog can fire
            ok, msg = permissions.reset_tcc_permission()
            _mic_status_ok = ok
            _mic_status_message = msg
            return {'FINISHED'}

        if current in ('unavailable', 'restricted'):
            _mic_status_ok = False
            _mic_status_message = f"Permission status: {current}."
            return {'FINISHED'}

        # not_determined — trigger the dialog
        _mic_status_ok = False
        _mic_status_message = "System dialog shown — please respond to grant access."
        permissions.request_mic_permission(on_done=_on_permission_done)
        return {'FINISHED'}


class AudioNodesPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    zmq_port: IntProperty(
        name="ZMQ Port",
        description="Port for ZeroMQ communication with the audio engine",
        default=5555,
        min=1024,
        max=65535,
    )

    auto_start_engine: BoolProperty(
        name="Auto-start Engine",
        description="Start the audio engine automatically when opening a .blend that contains an Audio Nodes tree",
        default=True,
    )

    sample_rate: IntProperty(
        name="Sample Rate",
        description="Audio sample rate in Hz (applied live; the device "
                    "snaps to the nearest rate it supports)",
        default=44100,
        min=22050,
        max=192000,
        update=lambda self, ctx: self._apply_audio_settings(),
    )

    buffer_size: IntProperty(
        name="Buffer Size",
        description="Audio buffer size in samples. Lower = less latency, "
                    "higher CPU. Applied live; the device snaps to the "
                    "nearest size it supports (check the Engine panel "
                    "for the actual value)",
        default=128,
        min=32,
        max=4096,
        update=lambda self, ctx: self._apply_audio_settings(),
    )

    def _apply_audio_settings(self):
        """Push sample rate / buffer size to the running engine, live."""
        from . import command_client
        from . import engine_manager

        if not engine_manager.is_engine_running():
            return  # applied automatically on next engine start

        applied = command_client.set_audio_settings(
            self.sample_rate, self.buffer_size)
        if applied:
            print(f"Audio Nodes: audio settings applied - "
                  f"{applied.get('sample_rate')} Hz / "
                  f"{applied.get('buffer_size')} samples "
                  f"(device min: {applied.get('min_buffer_size')})")
            # Keep the Engine panel's cached values current
            try:
                from .ui import panels
                panels.refresh_audio_device_cache()
            except Exception:
                pass

    engine_path: StringProperty(
        name="Engine Path",
        description="Path to the audio engine executable (leave empty for bundled engine)",
        subtype='FILE_PATH',
        default="",
    )

    def draw(self, context):
        layout = self.layout

        # ── Microphone Permission (macOS only) ────────────────────────────────
        if sys.platform == 'darwin':
            from . import permissions

            status = permissions.check_mic_status()
            patched = permissions.check_plist_patched()

            box = layout.box()
            box.label(text="Microphone Permission", icon='SOUND')

            # Plist status
            plist_row = box.row()
            if patched is True:
                plist_row.label(text="Info.plist: OK", icon='CHECKMARK')
            elif patched is False:
                plist_row.label(text="Info.plist: Missing mic usage key", icon='ERROR')
                box.operator("audionodes.patch_plist", text="Patch Info.plist", icon='FILE_SCRIPT')
            else:
                plist_row.label(text="Info.plist: Not found", icon='QUESTION')

            # Permission status
            status_row = box.row()
            if status == 'authorized':
                status_row.label(text="Permission: Granted", icon='CHECKMARK')
            elif status == 'denied':
                status_row.label(text="Permission: Denied", icon='ERROR')
            elif status == 'restricted':
                status_row.label(text="Permission: Restricted by system policy", icon='ERROR')
            elif status == 'not_determined':
                status_row.label(text="Permission: Not yet requested", icon='QUESTION')
            else:
                status_row.label(text="Permission: Unknown", icon='QUESTION')

            if status != 'authorized':
                box.operator(
                    "audionodes.request_mic_permission",
                    text="Request Microphone Access",
                    icon='RESTRICT_VIEW_OFF',
                )

            if _mic_status_message:
                for line in _mic_status_message.split("\n"):
                    box.label(text=line, icon='INFO' if not _mic_status_ok else 'NONE')

        # ── Audio Engine Settings ─────────────────────────────────────────────
        layout.label(text="Audio Engine Settings")

        box = layout.box()
        box.prop(self, "auto_start_engine")
        box.prop(self, "zmq_port")
        box.prop(self, "sample_rate")
        box.prop(self, "buffer_size")

        box = layout.box()
        box.label(text="Advanced")
        box.prop(self, "engine_path")
