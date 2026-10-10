import bpy
import time


# Cache for audio devices to avoid blocking ZMQ calls on every UI redraw
_audio_device_cache = {
    'input_devices': [],
    'output_devices': [],
    'current_input': '',
    'current_output': '',
    'input_channels': [],   # active channel names of the current input device
    'sample_rate': 0,       # negotiated by the device (Hz)
    'buffer_size': 0,       # negotiated by the device (samples)
    'min_buffer_size': 0,   # smallest size the device supports
    'last_refresh': 0,
    'valid': False
}


def get_cached_input_channels():
    """Active input channel names of the current device (may be empty).

    Index N in this list is device input channel N - the same index the
    Audio Input node's channel params and the `input_peak_N` meter
    channels use. Non-blocking: returns whatever the last refresh saw.
    """
    if not _audio_device_cache['valid']:
        return []
    return list(_audio_device_cache['input_channels'])


def refresh_audio_device_cache():
    """Refresh the audio device cache from the engine."""
    from .. import command_client
    from .. import engine_manager
    
    if not engine_manager.is_engine_running():
        _audio_device_cache['valid'] = False
        return False
    
    devices = command_client.get_devices()
    if devices:
        _audio_device_cache['input_devices'] = devices.get('input_devices', [])
        _audio_device_cache['output_devices'] = devices.get('output_devices', [])
        _audio_device_cache['current_input'] = devices.get('current_input', '')
        _audio_device_cache['current_output'] = devices.get('current_output', '')
        _audio_device_cache['input_channels'] = devices.get('input_channels', [])
        _audio_device_cache['sample_rate'] = devices.get('sample_rate', 0)
        _audio_device_cache['buffer_size'] = devices.get('buffer_size', 0)
        _audio_device_cache['min_buffer_size'] = devices.get('min_buffer_size', 0)
        _audio_device_cache['last_refresh'] = time.time()
        _audio_device_cache['valid'] = True
        return True
    else:
        _audio_device_cache['valid'] = False
        return False


class AudioNodesPanel:
    """Base class for Audio Nodes panels."""
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Audio"
    
    @classmethod
    def poll(cls, context):
        snode = context.space_data
        return snode and snode.tree_type == 'AudioNodeTree'


class AUDIONODES_PT_devices(AudioNodesPanel, bpy.types.Panel):
    """Panel for audio device selection."""
    bl_label = "Audio Devices"
    bl_idname = "AUDIONODES_PT_devices"
    
    def draw(self, context):
        layout = self.layout
        
        from .. import engine_manager
        
        if not engine_manager.is_engine_running():
            layout.label(text="Engine not running", icon='ERROR')
            layout.operator("audionodes.start_engine")
            return
        
        # Refresh button
        row = layout.row()
        row.operator("audionodes.refresh_audio_devices", icon='FILE_REFRESH')
        
        # Use cached devices (non-blocking)
        if not _audio_device_cache['valid']:
            layout.label(text="Click Refresh to load devices", icon='INFO')
            return
        
        # Input devices
        box = layout.box()
        box.label(text="Input Device", icon='IMPORT')
        
        current_input = _audio_device_cache['current_input']
        input_devices = _audio_device_cache['input_devices']
        
        if not input_devices:
            box.label(text="No input devices found")
        else:
            for device in input_devices:
                row = box.row()
                is_current = device == current_input
                op = row.operator("audionodes.set_device", 
                                  text=device,
                                  icon='CHECKMARK' if is_current else 'NONE',
                                  emboss=not is_current)
                op.device_type = 'input'
                op.device_name = device

        # Input channels of the current device, with live meters. This is
        # the mapping reference for multi-channel interfaces (e.g. XR18):
        # sing/play into a channel, watch which meter moves, pick that
        # channel on an Audio Input node.
        channels = _audio_device_cache['input_channels']
        if channels:
            chan_box = layout.box()
            row = chan_box.row()
            row.label(text=f"Input Channels ({len(channels)})", icon='OUTLINER_DATA_SPEAKER')

            audio_data = getattr(context.scene, 'audio_nodes_data', None)
            col = chan_box.column(align=True)
            for i, name in enumerate(channels):
                peak = 0.0
                if audio_data is not None:
                    chan = audio_data.channels.get(f"input_peak_{i}")
                    if chan is not None:
                        peak = chan.value
                col.progress(factor=min(peak, 1.0), text=f"{i + 1}: {name}")

        # Output devices
        box = layout.box()
        box.label(text="Output Device", icon='EXPORT')
        
        current_output = _audio_device_cache['current_output']
        output_devices = _audio_device_cache['output_devices']
        
        if not output_devices:
            box.label(text="No output devices found")
        else:
            for device in output_devices:
                row = box.row()
                is_current = device == current_output
                op = row.operator("audionodes.set_device",
                                  text=device,
                                  icon='CHECKMARK' if is_current else 'NONE',
                                  emboss=not is_current)
                op.device_type = 'output'
                op.device_name = device


class AUDIONODES_PT_engine(AudioNodesPanel, bpy.types.Panel):
    """Panel for engine status and settings."""
    bl_label = "Engine"
    bl_idname = "AUDIONODES_PT_engine"
    
    def draw(self, context):
        layout = self.layout
        
        from .. import engine_manager
        
        # Engine status
        if engine_manager.is_engine_running():
            row = layout.row()
            row.label(text="Status: Running", icon='PLAY')
            layout.operator("audionodes.stop_engine", icon='PAUSE')
        else:
            row = layout.row()
            row.alert = True
            row.label(text="Status: Stopped", icon='PAUSE')
            layout.operator("audionodes.start_engine", icon='PLAY')
        
        layout.separator()

        # Devices that refused to open when the engine started. Without
        # this the user just gets silence and no explanation.
        device_errors = engine_manager.get_device_restore_errors()
        if device_errors:
            box = layout.box()
            box.alert = True
            box.label(text="Saved audio device unavailable", icon='ERROR')
            for message in device_errors.values():
                box.label(text=message)
            box.label(text="Pick a device below to continue.", icon='INFO')

        from .. import command_client
        errors = command_client.get_command_errors()
        if errors:
            box = layout.box()
            box.alert = True
            box.label(text="Audio engine commands failed", icon='ERROR')
            names = {n.engine_node_id: n.name for t in bpy.data.node_groups
                     if t.bl_idname == 'AudioNodeTree' for n in t.nodes
                     if hasattr(n, 'engine_node_id')}
            for key, message in list(errors.items())[-5:]:
                box.label(text=f"{names.get(key[1], key[1])}: {message}")

        # Negotiated audio settings + resulting device latency. These are
        # what the device ACTUALLY runs at - buffer/sample-rate requests
        # snap to the nearest supported value.
        if engine_manager.is_engine_running() and _audio_device_cache['valid']:
            sr = _audio_device_cache['sample_rate']
            buf = _audio_device_cache['buffer_size']
            min_buf = _audio_device_cache['min_buffer_size']
            if sr and buf:
                box = layout.box()
                latency_ms = buf / sr * 1000.0
                box.label(text=f"{int(sr)} Hz · {buf} samples ({latency_ms:.1f} ms)",
                          icon='TIME')
                if min_buf and buf > min_buf:
                    box.label(text=f"Device supports down to {min_buf} samples",
                              icon='INFO')

        # Audio levels (if running)
        if engine_manager.is_engine_running():
            try:
                scene = context.scene
                if hasattr(scene, 'audio_nodes_data'):
                    audio_data = scene.audio_nodes_data
                    
                    box = layout.box()
                    box.label(text="Levels")
                    
                    col = box.column(align=True)
                    
                    if 'peak_left' in audio_data.channels:
                        peak_l = audio_data.channels['peak_left'].value
                        col.progress(factor=min(peak_l, 1.0), text=f"Peak L: {peak_l:.2f}")
                    
                    if 'peak_right' in audio_data.channels:
                        peak_r = audio_data.channels['peak_right'].value
                        col.progress(factor=min(peak_r, 1.0), text=f"Peak R: {peak_r:.2f}")
            except:
                pass


# Operator for refreshing audio devices
class AUDIONODES_OT_refresh_audio_devices(bpy.types.Operator):
    bl_idname = "audionodes.refresh_audio_devices"
    bl_label = "Refresh Devices"
    bl_description = "Refresh the list of available audio devices"
    
    def execute(self, context):
        from .. import engine_manager
        
        if not engine_manager.is_engine_running():
            self.report({'WARNING'}, "Engine not running")
            return {'CANCELLED'}
        
        if refresh_audio_device_cache():
            input_count = len(_audio_device_cache['input_devices'])
            output_count = len(_audio_device_cache['output_devices'])
            self.report({'INFO'}, f"Found {input_count} input, {output_count} output devices")
        else:
            self.report({'ERROR'}, "Could not get devices from engine")
        
        return {'FINISHED'}


# Operator for setting audio device
class AUDIONODES_OT_set_device(bpy.types.Operator):
    bl_idname = "audionodes.set_device"
    bl_label = "Set Audio Device"
    bl_description = "Set the audio input or output device"
    
    device_type: bpy.props.StringProperty()
    device_name: bpy.props.StringProperty()
    
    def execute(self, context):
        from .. import command_client

        result = command_client.set_device(self.device_type, self.device_name)
        if result.get("ok"):
            # Update cache with new selection
            if self.device_type == 'input':
                _audio_device_cache['current_input'] = self.device_name
            else:
                _audio_device_cache['current_output'] = self.device_name

            # Persist the choice on the scene so it survives save/reload and
            # is re-applied automatically the next time the engine starts.
            scene = context.scene
            if hasattr(scene, 'audio_nodes_data'):
                data = scene.audio_nodes_data
                if self.device_type == 'input':
                    data.input_device_name = self.device_name
                else:
                    data.output_device_name = self.device_name

            # Re-query the engine — JUCE may have negotiated a different
            # sample rate / buffer size, and the panel should reflect the
            # actual running configuration.
            refresh_audio_device_cache()

            self.report({'INFO'}, f"Set {self.device_type} device to {self.device_name}")
        else:
            # Say WHY. "Failed to set output device" gave the user nothing
            # to act on; the engine now reports what the device refused.
            detail = result.get("message") or "the device could not be opened"
            self.report({'ERROR'},
                        f"Could not use '{self.device_name}' as the "
                        f"{self.device_type} device: {detail}")
            refresh_audio_device_cache()

        return {'FINISHED'}

