"""
ZMQ Command Client for Audio Nodes

This module handles communication between Blender and the C++ audio engine.

Threading model:
- Graph commands (create/delete/connect/disconnect) and parameter updates
  are sent by ONE background sender thread with its own ZMQ socket. Graph
  commands go through an ordered FIFO queue (fire-and-forget from the
  caller's point of view); parameter updates are coalesced per (node,
  param) and flushed at ~30Hz. Blender's main thread never blocks on a
  socket for these.
- Synchronous queries (ping, get_devices, ...) use a separate
  main-thread socket guarded by a lock. These are rare, user-initiated
  operations where the caller needs the result.

ZMQ REQ/REP requires strict send-recv alternation. Using separate sockets
per thread keeps the protocol valid.
"""

import json
import time
import threading
from collections import deque
from typing import Optional, Dict, Any

# Try to import zmq
try:
    import zmq
    HAS_ZMQ = True
except ImportError:
    HAS_ZMQ = False
    print("Audio Nodes: pyzmq not installed")

# =============================================================================
# Main Thread Socket (for synchronous queries)
# =============================================================================

_main_context = None
_main_socket = None
_main_lock = threading.Lock()

# =============================================================================
# Background Sender Thread (graph commands + parameter updates)
# =============================================================================

_cmd_queue = deque()           # ordered graph commands (dicts)
_pending_params = {}           # node_id -> {param: value} (coalesced)

# Confirmed engine state and in-flight creates are separate. The reconciler
# retries failures without treating queued commands as completed work.
_registered_nodes = set()  # Engine-confirmed creates only.
_pending_creates = set()
_pending_deletes = set()
_uncertain_creates = set()
_command_errors = {}  # command key -> user-visible failure (no bpy in worker).
_sample_loads = {}    # (node_id, slot) -> latest request/status.
_sample_request_id = 0
_sender_lock = threading.Lock()
_sender_wakeup = threading.Event()
_sender_thread = None
_sender_running = False
_sender_inflight = False       # a graph command is currently being sent

# Interval between parameter flushes (~30 updates/sec). Graph commands are
# sent as soon as the sender wakes up.
_PARAM_FLUSH_INTERVAL = 0.033

# Per-message retry count before a command is dropped (with a log line).
_SEND_RETRIES = 3

# How long the engine itself waits for one device switch. Must match
# `future.wait_for(std::chrono::seconds(N))` in runOnMessageThread() in
# engine/src/AudioEngine.cpp. A set_device command waits there exactly once
# (the whole fallback ladder runs inside that single wait), so this is the
# engine's worst case for one switch. Change both sides together; an offline
# test compares them.
ENGINE_DEVICE_SWITCH_LIMIT_S = 15

# Extra time on top of the engine's limit for the reply to arrive.
DEVICE_SWITCH_MARGIN_S = 5

# Reply timeout for slow (device) commands. Waiting less than the engine
# made a switch that took 10-15 s look like a failure even though it worked.
# tests/harness.py (Engine.SLOW_COMMANDS) uses the same 20 s.
SLOW_COMMAND_TIMEOUT_MS = (ENGINE_DEVICE_SWITCH_LIMIT_S + DEVICE_SWITCH_MARGIN_S) * 1000


def get_port():
    """Get the ZMQ port from preferences.

    Uses `__package__` so the lookup works whether the addon was installed
    from the shipped zip (module name "audio_nodes") or loaded from source
    during development/tests (module name "blender_addon").
    """
    try:
        import bpy
        prefs = bpy.context.preferences.addons.get(__package__)
        if prefs and hasattr(prefs, 'preferences'):
            return prefs.preferences.zmq_port
    except Exception:
        pass
    return 5555


def _create_socket(context, timeout_ms=2000, port=None):
    """Create and configure a ZMQ REQ socket."""
    socket = context.socket(zmq.REQ)
    socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
    socket.setsockopt(zmq.SNDTIMEO, timeout_ms)
    socket.setsockopt(zmq.LINGER, 0)  # Don't wait on close
    socket.connect(f"tcp://127.0.0.1:{get_port() if port is None else port}")
    return socket


def _ensure_main_connection():
    """Ensure main socket is connected. Must be called with _main_lock held."""
    global _main_context, _main_socket

    if not HAS_ZMQ:
        return False

    if _main_socket is not None:
        return True

    try:
        _main_context = zmq.Context()
        _main_socket = _create_socket(_main_context, timeout_ms=3000)
        return True
    except Exception as e:
        print(f"Audio Nodes: Failed to connect main socket: {e}")
        _main_socket = None
        return False


def _reset_main_socket():
    """Reset the main socket after an error. Must be called with _main_lock held."""
    global _main_socket

    if _main_socket is not None:
        try:
            _main_socket.close(linger=0)
        except Exception:
            pass
        _main_socket = None


def disconnect():
    """Disconnect all sockets and cleanup."""
    global _main_context, _main_socket

    # Stop sender thread first
    stop_sender_thread()

    with _main_lock:
        if _main_socket is not None:
            try:
                _main_socket.close(linger=0)
            except Exception:
                pass
            _main_socket = None

        if _main_context is not None:
            try:
                _main_context.term()
            except Exception:
                pass
            _main_context = None


def send_command(command: Dict[str, Any], timeout_ok: bool = False) -> Optional[Dict[str, Any]]:
    """Send a command synchronously and return the response.

    Blocks the calling thread (bounded by the socket timeout). Reserve this
    for queries whose result the caller actually needs; graph mutations
    should go through queue_command() instead.
    """
    global _main_socket

    if not HAS_ZMQ:
        return None

    # Hold lock for ENTIRE send-recv cycle
    with _main_lock:
        if not _ensure_main_connection():
            return None

        if _main_socket is None:
            return None

        try:
            message = json.dumps(command)
            _main_socket.send_string(message)
            response = _main_socket.recv_string()
            return json.loads(response)

        except zmq.error.Again:
            if not timeout_ok:
                print(f"Audio Nodes: Command timeout ({command.get('type', 'unknown')})")
            _reset_main_socket()
            return None

        except Exception as e:
            if not timeout_ok:
                print(f"Audio Nodes: Command error: {e}")
            _reset_main_socket()
            return None


# =============================================================================
# Sender thread internals
# =============================================================================

def _command_key(cmd):
    return (cmd.get("type"), cmd.get("node_id", cmd.get("dest_node", "")),
            cmd.get("param", cmd.get("dest_port", cmd.get("slot", ""))))


def _send_with_retries(sock, context, cmd, port=None):
    """Return (socket, response). An engine error is never a success.

    Sample loads wait on this worker, not Blender's UI. Stop is checked
    while polling, so unloading the add-on can join the sender promptly.
    """
    payload = json.dumps({k: v for k, v in cmd.items() if not k.startswith("_")})
    for attempt in range(_SEND_RETRIES):
        try:
            sock.send_string(payload)
            if cmd.get("type") in ("load_sample", "load_drum_sample"):
                deadline = time.monotonic() + 30.0
                while not sock.poll(50):
                    if not _sender_running or time.monotonic() >= deadline:
                        raise TimeoutError("Sample loading interrupted or timed out")
            response = json.loads(sock.recv_string())
            if not isinstance(response, dict) or response.get("status") not in ("ok", "error"):
                return sock, {"status": "error", "message": "Invalid engine response"}
            return sock, response
        except Exception as exc:
            try:
                sock.close(linger=0)
            except Exception:
                pass
            try:
                sock = _create_socket(context, timeout_ms=500, port=port)
            except Exception as connection_error:
                return sock, {"status": "error", "message": str(connection_error)}
            if not _sender_running or attempt == _SEND_RETRIES - 1:
                return sock, {"status": "error", "message": str(exc) or "Engine did not respond"}
    return sock, {"status": "error", "message": "Engine did not respond"}


def _record_response(cmd, response):
    """Publish acknowledgement under the sender lock; never access Blender here."""
    ok = response.get("status") == "ok"
    key = _command_key(cmd)
    node_id = cmd.get("node_id", "")
    with _sender_lock:
        if ok:
            _command_errors.pop(key, None)
            if cmd["type"] in ("connect", "disconnect"):
                _command_errors.pop(("connect", key[1], key[2]), None)
                _command_errors.pop(("disconnect", key[1], key[2]), None)
        else:
            _command_errors[key] = response.get("message") or "Engine rejected " + cmd["type"]
        if cmd["type"] == "create_node":
            _pending_creates.discard(node_id)
            if ok:
                _registered_nodes.add(node_id)
                _uncertain_creates.discard(node_id)
            else:
                _uncertain_creates.add(node_id)
        elif cmd["type"] == "delete_node":
            _pending_deletes.discard(node_id)
            if ok:
                _registered_nodes.discard(node_id)
                for error_key in list(_command_errors):
                    if error_key[1] == node_id:
                        _command_errors.pop(error_key, None)
        elif cmd["type"] in ("load_sample", "load_drum_sample"):
            state = _sample_loads.get((node_id, cmd.get("slot", -1)))
            if state and state["request_id"] == cmd.get("_request_id"):
                state.update(status="Loaded" if ok else "Error",
                             message="" if ok else response.get("message") or
                             "Could not load sample; check the file path and audio format")
    if not ok:
        print(f"Audio Nodes: {cmd['type']} failed: "
              f"{response.get('message') or 'engine rejected command'}")


def get_command_errors():
    with _sender_lock:
        return dict(_command_errors)


def clear_command_error(key):
    with _sender_lock:
        _command_errors.pop(key, None)


def get_sample_load_state(node_id, slot=-1):
    with _sender_lock:
        return dict(_sample_loads.get((node_id, slot), {}))


def registered_node_ids():
    with _sender_lock:
        return set(_registered_nodes)


def is_node_pending(node_id):
    with _sender_lock:
        return node_id in _pending_creates


def _sender_loop(port):
    """Background thread: drains graph commands in order, then flushes
    coalesced parameter updates."""
    global _sender_running

    if not HAS_ZMQ:
        return

    context = zmq.Context()
    sock = _create_socket(context, timeout_ms=500, port=port)

    try:
        while _sender_running:
            # 1) Graph commands - strictly in submission order.
            global _sender_inflight
            while _sender_running:
                with _sender_lock:
                    if not _cmd_queue:
                        break
                    cmd = _cmd_queue.popleft()
                    _sender_inflight = True
                try:
                    sock, response = _send_with_retries(sock, context, cmd, port)
                    _record_response(cmd, response)
                finally:
                    with _sender_lock:
                        _sender_inflight = False

            # 2) Parameter updates - coalesced, latest value wins.
            with _sender_lock:
                params_to_send = _pending_params.copy()
                _pending_params.clear()

            for node_id, params in params_to_send.items():
                with _sender_lock:
                    deleting = node_id in _pending_deletes
                if deleting:
                    continue
                for param_name, value in params.items():
                    if not _sender_running:
                        break
                    cmd = {
                        "type": "set_param",
                        "node_id": node_id,
                        "param": param_name,
                        "value": value,
                    }
                    sock, response = _send_with_retries(sock, context, cmd, port)
                    _record_response(cmd, response)
                    if response.get("status") != "ok":
                        # Engine unreachable: put the value back so it is
                        # retried when the engine returns, instead of
                        # silently losing the knob position.
                        with _sender_lock:
                            if node_id in _registered_nodes or node_id in _pending_creates:
                                _pending_params.setdefault(node_id, {}).setdefault(param_name, value)

            # Wait for either new graph commands (event) or the next
            # parameter flush window.
            _sender_wakeup.wait(_PARAM_FLUSH_INTERVAL)
            _sender_wakeup.clear()

    except Exception as e:
        print(f"Audio Nodes: sender thread error: {e!r}")

    finally:
        try:
            sock.close(linger=0)
        except Exception:
            pass
        try:
            context.term()
        except Exception:
            pass


def start_sender_thread():
    """Start the background sender thread."""
    global _sender_thread, _sender_running

    if not HAS_ZMQ:
        return

    if _sender_thread is not None and _sender_thread.is_alive():
        return

    _sender_running = True
    _sender_thread = threading.Thread(
        target=_sender_loop, args=(get_port(),), daemon=True, name="AudioNodes-Sender")
    _sender_thread.start()


def stop_sender_thread():
    """Stop the background sender thread."""
    global _sender_thread, _sender_running

    _sender_running = False
    _sender_wakeup.set()
    if _sender_thread is not None:
        _sender_thread.join(timeout=2.0)
        _sender_thread = None

    with _sender_lock:
        _cmd_queue.clear()
        _pending_params.clear()
        _pending_creates.clear()
        _uncertain_creates.clear()
        _pending_deletes.clear()
        _sample_loads.clear()
        _command_errors.clear()


# Backwards-compatible aliases (older code calls these names)
start_param_thread = start_sender_thread
stop_param_thread = stop_sender_thread


def queue_command(command: Dict[str, Any]):
    """Queue a graph command for ordered, asynchronous delivery."""
    if not HAS_ZMQ:
        return
    start_sender_thread()
    with _sender_lock:
        _cmd_queue.append(command)
    _sender_wakeup.set()


def flush_queue(timeout: float = 3.0) -> bool:
    """Wait (briefly) until all queued graph commands have been delivered.

    Use before synchronous calls that depend on queued commands having
    reached the engine (e.g. load_sample right after create_node).
    """
    if not HAS_ZMQ:
        return True
    deadline = time.monotonic() + timeout
    _sender_wakeup.set()
    while time.monotonic() < deadline:
        with _sender_lock:
            if not _cmd_queue and not _sender_inflight:
                return True
        time.sleep(0.01)
    return False


def set_parameter_batched(node_id: str, param: str, value: float):
    """Queue a parameter update to be sent by the background thread."""
    start_sender_thread()
    with _sender_lock:
        if node_id not in _pending_params:
            _pending_params[node_id] = {}
        _pending_params[node_id][param] = value


# =============================================================================
# Public API Functions
# =============================================================================

def ping() -> bool:
    """Ping the audio engine to check if it's running."""
    response = send_command({"type": "ping"}, timeout_ok=True)
    return response is not None and response.get("status") == "ok"


def is_node_registered(node_id: str) -> bool:
    """Has the engine acknowledged creation of this node?"""
    with _sender_lock:
        return node_id in _registered_nodes


def clear_registered_nodes():
    """Forget all registered ids (call when the engine stops/starts)."""
    with _sender_lock:
        _registered_nodes.clear()
        _pending_creates.clear()
        _uncertain_creates.clear()
        _pending_deletes.clear()
        _sample_loads.clear()
        _command_errors.clear()


def create_node(node_id: str, node_type: str, params: Optional[Dict[str, float]] = None) -> bool:
    """Create a new node in the audio engine (async, ordered)."""
    command = {
        "type": "create_node",
        "node_id": node_id,
        "node_type": node_type
    }
    if params:
        command["params"] = params

    if not HAS_ZMQ:
        return False
    with _sender_lock:
        if node_id in _registered_nodes or node_id in _pending_creates:
            return True
        uncertain = node_id in _uncertain_creates
        _pending_creates.add(node_id)
    if uncertain:
        # A create may have reached the engine before its reply was lost.
        # Reset only this unconfirmed id, then restore it through the FIFO.
        queue_command({"type": "delete_node", "node_id": node_id})
    queue_command(command)
    return True


def delete_node(node_id: str) -> bool:
    """Delete a node from the audio engine (async, ordered)."""
    with _sender_lock:
        if node_id in _pending_deletes:
            return True
        _pending_deletes.add(node_id)
        _pending_params.pop(node_id, None)
        for key in list(_sample_loads):
            if key[0] == node_id:
                _sample_loads.pop(key, None)
    queue_command({
        "type": "delete_node",
        "node_id": node_id
    })
    return True


def connect_nodes(source_node: str, dest_node: str,
                  source_port: str = "output", dest_port: str = "input") -> bool:
    """Connect two nodes in the audio engine (async, ordered)."""
    queue_command({
        "type": "connect",
        "source_node": source_node,
        "dest_node": dest_node,
        "source_port": source_port,
        "dest_port": dest_port
    })
    return True


def disconnect_nodes(dest_node: str, dest_port: str = "input") -> bool:
    """Disconnect a node input in the audio engine (async, ordered)."""
    queue_command({
        "type": "disconnect",
        "dest_node": dest_node,
        "dest_port": dest_port
    })
    return True


def set_parameter(node_id: str, param: str, value: float) -> bool:
    """Set a parameter on a node (coalesced, sent by background thread)."""
    set_parameter_batched(node_id, param, value)
    return True


def send_event(node_id: str, param: str, value: float = 1.0) -> bool:
    """Send a one-off parameter EVENT, in order, without coalescing.

    Triggers are events, not values: two drum pad hits, or two presses of
    a Bang's Fire, are two separate things that both have to reach the
    engine. set_parameter() coalesces per (node, param) inside a ~33ms
    flush window, so a fast double hit collapsed into a single message and
    the second one was simply lost. Events go through the ordered command
    queue instead, one message each.
    """
    queue_command({
        "type": "set_param",
        "node_id": node_id,
        "param": param,
        "value": float(value),
    })
    return True


def set_pattern(node_id: str, lane: int, pattern: int) -> bool:
    """Set a sequencer step pattern (async, ordered).

    Patterns are 64-bit masks and must be sent as JSON integers - the
    set_param float path only has 24 bits of mantissa and corrupts any
    pattern that uses steps beyond 24.
    """
    queue_command({
        "type": "set_pattern",
        "node_id": node_id,
        "lane": lane,
        "pattern": int(pattern)
    })
    return True


def send_slow_command(command: Dict[str, Any],
                      timeout_ms: int = SLOW_COMMAND_TIMEOUT_MS) -> Optional[Dict[str, Any]]:
    """Send a command that can take many seconds, on its own socket.

    Device switches tear down and reopen the audio device, which on
    CoreAudio regularly takes longer than the main socket's 3s timeout.
    The old code sent them on the shared socket, gave up early and
    reported a failure for a switch the engine went on to complete -
    the coin-flip "engine won't start on external headphones". A private
    socket also keeps the long wait off the shared REQ/REP alternation.

    The default timeout is longer than the engine's own device-switch
    limit (see SLOW_COMMAND_TIMEOUT_MS), so the engine always answers
    first - with success or with a real error message.
    """
    if not HAS_ZMQ:
        return None

    context = None
    socket = None
    try:
        context = zmq.Context()
        socket = _create_socket(context, timeout_ms=timeout_ms)
        socket.send_string(json.dumps(command))
        return json.loads(socket.recv_string())
    except zmq.error.Again:
        print(f"Audio Nodes: {command.get('type', 'command')} timed out after "
              f"{timeout_ms / 1000:.0f}s")
        return None
    except Exception as e:
        print(f"Audio Nodes: {command.get('type', 'command')} error: {e}")
        return None
    finally:
        if socket is not None:
            try:
                socket.close(linger=0)
            except Exception:
                pass
        if context is not None:
            try:
                context.term()
            except Exception:
                pass


def set_parameter_immediate(node_id: str, param: str, value: float) -> bool:
    """Set a parameter synchronously (blocking). Use sparingly."""
    response = send_command({
        "type": "set_param",
        "node_id": node_id,
        "param": param,
        "value": value
    })
    return response is not None and response.get("status") == "ok"


def get_devices() -> Optional[Dict[str, Any]]:
    """Get available audio devices from the engine."""
    response = send_command({"type": "get_devices"})
    if response and response.get("status") == "ok":
        return response.get("data", {})
    return None


def set_audio_settings(sample_rate: int = 0, buffer_size: int = 0,
                       defer: bool = False) -> Optional[Dict[str, Any]]:
    """Apply sample rate and/or buffer size (0 = keep current).

    These become the engine's PREFERRED settings, re-applied after every
    device switch. The device snaps to the nearest supported value -
    the returned dict contains what was actually negotiated
    (sample_rate, buffer_size, min_buffer_size), or None on failure.
    """
    command: Dict[str, Any] = {"type": "set_audio_settings"}
    if sample_rate:
        command["sample_rate"] = float(sample_rate)
    if buffer_size:
        command["buffer_size"] = int(buffer_size)
    if defer:
        # Remember the settings; let the next device open apply them.
        command["defer"] = True
    response = send_command(command)
    if response and response.get("status") == "ok":
        return response.get("data", {})
    return None


def set_device(device_type: str, device_name: str) -> Dict[str, Any]:
    """Set the audio input or output device.

    Returns {"ok": bool, "message": str, "data": {...}}. `data` carries the
    devices the engine is ACTUALLY using, which is what the caller should
    believe - a switch can succeed while JUCE substitutes another device,
    and a reply can be lost while the switch itself worked.
    """
    command = {
        "type": "set_device",
        "device_type": device_type,
        "device_name": device_name,
    }
    response = send_slow_command(command)

    if response is None:
        # No reply is not the same as no switch. Ask the engine what it is
        # running now; if it is what we wanted, the switch worked and only
        # the acknowledgement went missing.
        devices = get_devices()
        if devices:
            key = "current_input" if device_type == "input" else "current_output"
            if devices.get(key) == device_name:
                return {"ok": True, "message": "", "data": devices,
                        "timed_out": False}
        return {"ok": False,
                "message": "the audio engine did not answer in time",
                "data": devices or {},
                "timed_out": True}

    return {
        "ok": response.get("status") == "ok",
        "message": response.get("message") or "",
        "data": response.get("data") or {},
        "timed_out": False,
    }


def get_midi_devices() -> Optional[Dict[str, Any]]:
    """Get available MIDI devices from the engine."""
    response = send_command({"type": "get_midi_devices"})
    if response and response.get("status") == "ok":
        return response.get("data", {})
    return None


def set_midi_device(device_type: str, device_name: str) -> bool:
    """Set the MIDI input or output device."""
    response = send_command({
        "type": "set_midi_device",
        "device_type": device_type,
        "device_name": device_name
    })
    return response is not None and response.get("status") == "ok"


def _queue_sample(node_id, file_path, slot=-1):
    global _sample_request_id
    with _sender_lock:
        _sample_request_id += 1
        request_id = _sample_request_id
        _sample_loads[(node_id, slot)] = {
            "request_id": request_id, "path": file_path,
            "status": "Loading" if HAS_ZMQ else "Error",
            "message": "" if HAS_ZMQ else "Audio connection is unavailable",
        }
    command = {"type": "load_sample" if slot == -1 else "load_drum_sample",
               "node_id": node_id, "file_path": file_path, "_request_id": request_id}
    if slot != -1:
        command["slot"] = slot
    queue_command(command)
    return HAS_ZMQ  # Accepted for loading, not proof of a successful load.


def load_sample(node_id: str, file_path: str) -> bool:
    """Queue a sample after preceding creates; inspect get_sample_load_state for completion."""
    return _queue_sample(node_id, file_path)


def load_drum_sample(node_id: str, slot: int, file_path: str) -> bool:
    """Queue a drum sample without blocking Blender's main thread."""
    return _queue_sample(node_id, file_path, slot)
