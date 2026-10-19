import bpy
import subprocess
import os
import sys
import time
import atexit
from pathlib import Path

# Global engine process
_engine_process = None


def _win_no_window() -> int:
    """CREATE_NO_WINDOW on Windows, 0 elsewhere.

    Keeps helper processes (and the engine itself) from flashing a console
    window. Harmless to pass as 0 on POSIX.
    """
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def kill_orphan_engines(port=None):
    """Kill orphan engine processes from previous sessions of THIS setup.

    The pkill pattern includes the ZMQ port so we never kill engines that
    belong to other Blender instances running on different ports.
    """
    import subprocess

    if port is None:
        try:
            port = get_zmq_port()
        except Exception:
            port = 5555

    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        # The Linux binary is "AudioNodesEngine" (no spaces)
        name = "Audio Nodes Engine" if sys.platform == "darwin" else "AudioNodesEngine"
        try:
            # Only match engines started with our port
            result = subprocess.run(
                ["pkill", "-f", f"{name}.*--zmq-port {port}"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                print("Audio Nodes: Killed orphan engine process")
        except Exception:
            pass  # No orphans found or pkill not available
    elif sys.platform == "win32":
        # Scope the kill to OUR port, like the macOS branch above.
        # `taskkill /F /IM AudioNodesEngine.exe` used to live here and killed
        # EVERY engine on the machine - a second Blender running on a
        # different port lost its engine without warning. There is no
        # command-line filter in taskkill, so go through CIM.
        #
        # Deliberately no blanket-kill fallback: an orphan holding the port
        # produces a clear "engine not responding" message, whereas killing
        # someone else's engine is silent and much worse.
        ps = (
            "Get-CimInstance Win32_Process "
            "-Filter \"Name='AudioNodesEngine.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*--zmq-port {port}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, timeout=15,
                creationflags=_win_no_window(),
            )
        except Exception as e:
            print(f"Audio Nodes: could not check for orphan engines on port "
                  f"{port} ({e}). If the engine fails to start, end any "
                  f"stray AudioNodesEngine.exe in Task Manager.")


def get_platform_binary():
    """Get the path to the platform-specific engine binary."""
    # Get the addon directory - this is where __init__.py is located
    addon_dir = Path(__file__).resolve().parent
    
    print(f"Audio Nodes: Addon directory: {addon_dir}")
    
    if sys.platform == "darwin":
        # macOS - binary name has spaces: "Audio Nodes Engine"
        binary_path = addon_dir / "bin" / "darwin_arm64" / "AudioNodesEngine.app" / "Contents" / "MacOS" / "Audio Nodes Engine"
    elif sys.platform == "win32":
        # Windows
        binary_path = addon_dir / "bin" / "win_x64" / "AudioNodesEngine.exe"
    elif sys.platform.startswith("linux"):
        binary_path = addon_dir / "bin" / "linux_x64" / "AudioNodesEngine"
    else:
        print(f"Audio Nodes: Unsupported platform: {sys.platform}")
        return None
    
    print(f"Audio Nodes: Looking for binary at: {binary_path}")
    
    if binary_path.exists():
        print(f"Audio Nodes: Found binary!")
        return binary_path
    else:
        print(f"Audio Nodes: Binary NOT found at: {binary_path}")
        # List what's actually in the bin directory for debugging
        bin_dir = addon_dir / "bin"
        if bin_dir.exists():
            print(f"Audio Nodes: Contents of {bin_dir}:")
            for item in bin_dir.rglob("*"):
                print(f"  - {item.relative_to(bin_dir)}")
        else:
            print(f"Audio Nodes: bin directory does not exist: {bin_dir}")
        return None


def get_engine_path():
    """Get the engine path, checking preferences first."""
    prefs = bpy.context.preferences.addons.get(__package__)
    
    if prefs and prefs.preferences.engine_path:
        custom_path = Path(prefs.preferences.engine_path)
        if custom_path.exists():
            return custom_path
    
    return get_platform_binary()


def get_zmq_port():
    """Get the ZMQ port from preferences."""
    prefs = bpy.context.preferences.addons.get(__package__)
    if prefs:
        return prefs.preferences.zmq_port
    return 5555


def is_engine_running():
    """Check if the engine process is running."""
    global _engine_process
    
    if _engine_process is None:
        return False
    
    # Check if process is still running
    return _engine_process.poll() is None


def _engine_env(binary_path):
    """Environment for the engine subprocess.

    The .app ships its non-system dylibs (libzmq, libsodium) in
    Contents/Frameworks. Pointing DYLD_LIBRARY_PATH there makes dyld
    resolve them by leaf name BEFORE the Homebrew install paths baked
    into the binary, so the packaged add-on runs on a fresh Mac with no
    Homebrew — and dev machines exercise the exact same bundled copies a
    customer gets. Nothing signed is modified, so code signatures stay
    valid. Ignored on non-mac platforms and when Frameworks is absent.
    """
    env = os.environ.copy()
    if sys.platform != "darwin":
        return env

    frameworks = Path(binary_path).parent.parent / "Frameworks"
    if not frameworks.is_dir():
        return env

    fw = str(frameworks)
    for var in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
        existing = env.get(var, "")
        env[var] = f"{fw}:{existing}" if existing else fw
    return env


def start_engine():
    """Start the audio engine subprocess."""
    global _engine_process
    
    if is_engine_running():
        print("Audio Nodes: Engine already running")
        return True
    
    binary_path = get_engine_path()
    if binary_path is None:
        print("Audio Nodes: Engine binary not found!")
        print(f"Audio Nodes: Looked in addon directory: {Path(__file__).parent}")
        return False
    
    print(f"Audio Nodes: Starting engine from: {binary_path}")
    
    # Kill any orphan engines from previous sessions (scoped to our port)
    kill_orphan_engines()
    time.sleep(0.2)  # Brief pause to ensure port is freed
    
    # Ensure the binary is executable (may lose permission when unzipped)
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        try:
            import stat
            current_mode = binary_path.stat().st_mode
            if not (current_mode & stat.S_IXUSR):
                print("Audio Nodes: Setting executable permission on binary...")
                binary_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception as e:
            print(f"Audio Nodes: Warning - could not set executable permission: {e}")

    # Remove macOS quarantine flag that Gatekeeper sets on downloaded files.
    # Without this the engine is silently blocked when Blender runs as a GUI app.
    if sys.platform == "darwin":
        try:
            app_bundle = binary_path.parent.parent.parent  # .app directory
            subprocess.run(
                ["xattr", "-rd", "com.apple.quarantine", str(app_bundle)],
                capture_output=True, timeout=10
            )
        except Exception as e:
            print(f"Audio Nodes: Warning - could not remove quarantine: {e}")
    
    port = get_zmq_port()
    
    try:
        from . import command_client
        import tempfile
        log_path = Path(tempfile.gettempdir()) / "AudioNodesEngine.log"
        log_file = open(log_path, "w")

        # Start the engine process — stderr goes to a log file so we can
        # read crash output without blocking the engine with a pipe.
        # start_new_session is POSIX-only and is silently ignored on
        # Windows; the equivalent there is DETACHED_PROCESS, plus
        # CREATE_NO_WINDOW so no console flashes up.
        popen_kwargs = {}
        if sys.platform == "win32":
            detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            popen_kwargs["creationflags"] = detached | _win_no_window()
        else:
            popen_kwargs["start_new_session"] = True

        _engine_process = subprocess.Popen(
            [str(binary_path), "--zmq-port", str(port)],
            stdout=log_file,
            stderr=log_file,
            env=_engine_env(binary_path),
            **popen_kwargs
        )

        # Wait up to 15 seconds for the engine to start — audio device
        # enumeration on some Macs can take several seconds. Poll at 250ms
        # so a fast boot doesn't hold Blender's UI a full second.
        started = False
        for attempt in range(60):
            time.sleep(0.25)
            if not is_engine_running():
                break
            if command_client.ping():
                started = True
                break
            if attempt % 4 == 3:
                print("Audio Nodes: Waiting for engine to become ready...")

        if not is_engine_running():
            log_file.flush()
            log_file.close()
            print("Audio Nodes: Engine process exited immediately")
            try:
                output = log_path.read_text(errors="replace").strip()
                if output:
                    print(f"Audio Nodes: Engine log ({log_path}):")
                    for line in output.splitlines():
                        print(f"  {line}")
                else:
                    print(f"Audio Nodes: Engine log was empty — check {log_path}")
            except Exception:
                pass
            return False

        if not started:
            log_file.close()
            print("Audio Nodes: Engine is running but not responding on ZMQ after 15s")
            try:
                output = log_path.read_text(errors="replace").strip()
                if output:
                    print(f"Audio Nodes: Engine log:")
                    for line in output.splitlines():
                        print(f"  {line}")
            except Exception:
                pass
            return False

        # Popen duplicated this fd into the child, which owns it for the
        # rest of the engine's life - our copy is dead weight. Leaving it
        # open leaked one descriptor per engine start.
        log_file.close()

        print(f"Audio Nodes: Engine started successfully on port {port}")

        # Fresh engine knows no nodes - reset the client-side registry so
        # sync_all repopulates it and the reconciler starts from truth.
        command_client.clear_registered_nodes()
        from . import shared_memory_reader
        shared_memory_reader.reset_control_state()

        # Stale connection cache from a previous engine run would make the
        # reconciler skip re-sending Sequencer→Sampler trigger links.
        from .nodes.core import node_tree as node_tree_mod
        node_tree_mod.clear_connection_cache()

        # Start the parameter sender thread
        command_client.start_param_thread()

        # Stage the user's sample rate / buffer size preferences WITHOUT
        # reopening the device, then restore the saved devices - which
        # opens once, already at the right settings.
        #
        # These two used to run the other way round and both reopened the
        # device, so starting the engine meant two full CoreAudio
        # teardown/reopen cycles back to back. The second one had to
        # succeed against a device that was still settling, which is how a
        # half-open output such as "External Headphones" ended up working
        # only about half the time.
        apply_audio_settings_from_prefs(defer=True)

        # Restore the user's saved audio device selection BEFORE syncing the
        # node graph. This stops the BlackHole-on-both-sides feedback loop
        # that happens when reopening a saved project: JUCE auto-picks the
        # macOS system default for both input AND output when the engine
        # boots, so we immediately switch to the user's saved selection to
        # minimise the time spent with a loopback device on both sides.
        if not restore_saved_audio_devices():
            # No saved device to open: the staged preferences still have to
            # reach the device that is already running.
            apply_audio_settings_from_prefs()

        # Drop data channels left behind by nodes deleted in a previous
        # session before repopulating from the live graph.
        try:
            from . import shared_memory_reader
            shared_memory_reader.prune_stale_channels()
        except Exception as e:
            print(f"Audio Nodes: channel prune failed: {e!r}")

        # Sync all existing nodes and connections to the engine
        sync_all_nodes_to_engine()

        # Auto-refresh device caches
        try:
            from .ui import panels
            panels.refresh_audio_device_cache()
        except:
            pass

        try:
            from .nodes.midi import midi_input_node
            midi_input_node.refresh_midi_device_cache()
        except:
            pass

        return True
            
    except Exception as e:
        import traceback
        try:
            log_file.close()
        except Exception:
            pass
        print(f"Audio Nodes: Failed to start engine: {e}")
        print(f"Audio Nodes: Traceback: {traceback.format_exc()}")
        return False


def stop_engine():
    """Stop the audio engine subprocess."""
    global _engine_process

    # Stop the parameter sender thread
    from . import command_client
    command_client.stop_param_thread()
    command_client.clear_registered_nodes()
    from . import shared_memory_reader
    shared_memory_reader.reset_control_state()

    if not is_engine_running():
        _engine_process = None
        return
    
    try:
        # Send shutdown command
        command_client.send_command({"type": "shutdown"})
        
        # Wait for graceful shutdown
        _engine_process.wait(timeout=5)
        
    except subprocess.TimeoutExpired:
        # Force kill if not responding
        _engine_process.kill()
        _engine_process.wait()
    
    except Exception as e:
        print(f"Audio Nodes: Error stopping engine: {e}")
        if _engine_process:
            _engine_process.kill()
    
    finally:
        _engine_process = None
        print("Audio Nodes: Engine stopped")


def sync_all_nodes_to_engine():
    """Sync all existing Audio Nodes to the engine after it starts."""
    from . import command_client
    
    print("Audio Nodes: Syncing existing nodes to engine...")
    
    # Find all AudioNodeTree node groups
    for tree in bpy.data.node_groups:
        if tree.bl_idname != 'AudioNodeTree':
            continue
        
        print(f"Audio Nodes: Syncing tree '{tree.name}'")
        
        # First, create all nodes in the engine
        for node in tree.nodes:
            if hasattr(node, 'engine_node_id') and hasattr(node, 'node_type'):
                node_type = node.node_type
                node_id = node.engine_node_id
                params = node.get_initial_params() if hasattr(node, 'get_initial_params') else {}
                
                # Skip Blender-only nodes (no C++ counterpart). Imported
                # from node_tree so this list cannot drift out of sync with
                # the reconciler's copy (it did: a Blender-only type was missing
                # here and the engine was asked to create a node for it).
                from .nodes.core.node_tree import BLENDER_ONLY_NODE_TYPES
                if node_type in BLENDER_ONLY_NODE_TYPES:
                    continue
                
                print(f"  Creating node: {node_id} (type: {node_type})")
                command_client.create_node(node_id, node_type, params)

        # Creates and connects travel through the same ordered queue, so no
        # sleep is needed between them.

        # Sync audio AND control links. Control ports (Trigger/Gate/Trig N)
        # are consumed by Sampler/Drum/ADSR on the audio thread; skipping
        # them left sequencers unwired after every engine restart.
        from .nodes.core import node_tree as node_tree_mod
        tree_connections = node_tree_mod._get_tree_connections(tree)
        for from_id, to_id, from_port, to_port in tree_connections:
            command_client.connect_nodes(from_id, to_id, from_port, to_port)
        node_tree_mod.set_connection_cache(tree.name, tree_connections)

        # Restore node-specific state (samples, MIDI devices, patterns, etc.)
        for node in tree.nodes:
            _restore_node_state(node)
    
    print("Audio Nodes: Sync complete")


def apply_audio_settings_from_prefs(defer: bool = False):
    """Push the sample rate / buffer size preferences to the engine.

    defer=True stores them as the engine's preferred settings without
    reopening the audio device, so the device open that follows applies
    them in one go.
    """
    from . import command_client

    try:
        prefs = bpy.context.preferences.addons.get(__package__)
        if not prefs:
            return
        p = prefs.preferences
        applied = command_client.set_audio_settings(p.sample_rate, p.buffer_size,
                                                    defer=defer)
        if defer:
            return
        if applied:
            print(f"Audio Nodes: audio settings - {applied.get('sample_rate')} Hz"
                  f" / {applied.get('buffer_size')} samples"
                  f" (device min: {applied.get('min_buffer_size')})")
        else:
            print("Audio Nodes: could not apply audio settings preferences")
    except Exception as e:
        print(f"Audio Nodes: audio settings apply error: {e}")


# Devices that could not be restored at startup, so the UI can say so
# instead of the user discovering silence. {"input"|"output": message}
_device_restore_errors = {}


def get_device_restore_errors():
    """Devices that failed to open when the engine last started."""
    return dict(_device_restore_errors)


def _apply_device_with_retry(device_type: str, device_name: str,
                             attempts: int = 2) -> bool:
    """Switch a device, retrying once before giving up.

    A device that has just been (re)enumerated can refuse the first open
    and accept the second a moment later - which is why selecting an
    output like "External Headphones" appeared to work only some of the
    time. One retry converts that coin flip into a reliable switch.
    """
    from . import command_client

    last_message = ""
    for attempt in range(attempts):
        if attempt:
            time.sleep(0.4)
        result = command_client.set_device(device_type, device_name)
        if result.get("ok"):
            if attempt:
                print(f"Audio Nodes: {device_type} device '{device_name}' "
                      f"opened on attempt {attempt + 1}")
            _device_restore_errors.pop(device_type, None)
            return True
        last_message = result.get("message") or "the device could not be opened"
        if result.get("timed_out"):
            # The engine is still working on the switch. Piling a second
            # request on top would queue behind the first and hold
            # Blender's UI for another full timeout without helping.
            break

    message = (f"Could not use '{device_name}' as the {device_type} device: "
               f"{last_message}")
    print(f"Audio Nodes: {message}")
    _device_restore_errors[device_type] = message
    return False


def restore_saved_audio_devices() -> bool:
    """Re-apply the audio input/output devices saved in the .blend file.

    JUCE picks whatever device macOS reports as the system default when the
    engine first opens its audio device. For users who route system audio
    through a virtual loopback device (e.g. BlackHole) that's also the
    macOS default, this means the engine boots with the loopback as both
    input AND output, producing a feedback loop until the user manually
    re-selects devices. Restoring the saved choices here fixes that.

    Returns True if a saved device was found and applied.
    """
    _device_restore_errors.clear()

    # No engine, nothing to restore. Without this check each saved device
    # would wait out the full device-switch timeout (about 20 s) before
    # giving up, with Blender frozen the whole time.
    if not is_engine_running():
        return False

    # Find any AudioNodesData with saved devices (we only have one scene
    # in normal use, but iterate to be safe).
    for scene in bpy.data.scenes:
        if not hasattr(scene, 'audio_nodes_data'):
            continue

        data = scene.audio_nodes_data
        input_name = getattr(data, 'input_device_name', '')
        output_name = getattr(data, 'output_device_name', '')

        if not input_name and not output_name:
            continue

        # Output first: it is the leg the user actually hears, and opening
        # it before the input means a stubborn input device cannot leave
        # playback pointing at the wrong output.
        if output_name:
            print(f"Audio Nodes: Restoring output device: {output_name}")
            _apply_device_with_retry('output', output_name)

        if input_name:
            print(f"Audio Nodes: Restoring input device: {input_name}")
            _apply_device_with_retry('input', input_name)

        # Only the first scene with saved devices is restored.
        return True

    return False


def _restore_node_state(node):
    """Restore node-specific state like sample paths, MIDI devices, etc."""
    from . import command_client
    
    bl_idname = getattr(node, 'bl_idname', '')
    
    # Restore Sampler sample paths
    if bl_idname == 'AudioSamplerNode':
        sample_path = getattr(node, 'sample_path', '')
        if sample_path:
            print(f"  Restoring sample: {sample_path}")
            command_client.load_sample(node.engine_node_id, bpy.path.abspath(sample_path))
    
    # Restore Drum Sampler samples AND per-slot settings.
    #
    # The slot volume/pitch/pan values were saved in the .blend and shown
    # in the UI, but were never sent anywhere: create_node only carried
    # master_volume, and this function only reloaded the samples. So every
    # slot came back at the engine's defaults (pitch 0) while the node
    # confidently displayed the saved number, and the only way to make the
    # two agree was to nudge the slider - which fired its update callback
    # and finally pushed the value. Restarting the engine could not fix it
    # because the restart ran this same incomplete restore.
    elif bl_idname == 'AudioDrumSamplerNode':
        for i in range(8):
            slot_path = getattr(node, f'slot_{i}_path', '')
            if slot_path:
                print(f"  Restoring drum sample slot {i}: {slot_path}")
                command_client.load_drum_sample(node.engine_node_id, i, bpy.path.abspath(slot_path))
            for prop in ('volume', 'pitch', 'pan'):
                name = f'slot_{i}_{prop}'
                if hasattr(node, name):
                    command_client.set_parameter(node.engine_node_id, name,
                                                 float(getattr(node, name)))
    
    # Restore MIDI Input device
    elif bl_idname == 'AudioMIDIInputNode':
        midi_device = getattr(node, 'midi_device_name', '')
        if midi_device:
            print(f"  Restoring MIDI input device: {midi_device}")
            command_client.set_midi_device('input', midi_device)
    
    # Restore MIDI Output device
    elif bl_idname == 'AudioMIDIOutputNode':
        midi_device = getattr(node, 'midi_device', 'NONE')
        if midi_device and midi_device != 'NONE':
            print(f"  Restoring MIDI output device: {midi_device}")
            command_client.set_midi_device('output', midi_device)

    # Restore Sequencer step pattern + transport (patterns are not in
    # create_node params - without this the engine mask stays all zeros)
    elif bl_idname == 'AudioSequencerNode':
        if hasattr(node, '_send_pattern'):
            print(f"  Restoring sequencer pattern: {node.engine_node_id}")
            node._send_pattern(0)
        if getattr(node, 'is_playing', False):
            command_client.set_parameter(node.engine_node_id, 'play', 1.0)


class AUDIONODES_OT_start_engine(bpy.types.Operator):
    bl_idname = "audionodes.start_engine"
    bl_label = "Start Audio Engine"
    bl_description = "Start the audio processing engine"
    
    def execute(self, context):
        if start_engine():
            self.report({'INFO'}, "Audio engine started")
        else:
            self.report({'ERROR'}, "Failed to start audio engine")
        return {'FINISHED'}


class AUDIONODES_OT_stop_engine(bpy.types.Operator):
    bl_idname = "audionodes.stop_engine"
    bl_label = "Stop Audio Engine"
    bl_description = "Stop the audio processing engine"
    
    def execute(self, context):
        stop_engine()
        self.report({'INFO'}, "Audio engine stopped")
        return {'FINISHED'}


# Register atexit handler to stop engine when Python exits
def _cleanup_on_exit():
    """Stop engine when Python interpreter exits."""
    global _engine_process
    if _engine_process is not None:
        try:
            _engine_process.terminate()
            _engine_process.wait(timeout=2)
        except:
            try:
                _engine_process.kill()
            except:
                pass

atexit.register(_cleanup_on_exit)

