"""Sample status is runtime state; filenames alone never mean loaded."""
import bpy
from . import command_client, engine_manager


def _slot_label(node, slot):
    """Human name for a drum pad, or "" for a plain sampler."""
    if slot < 0:
        return ""
    namer = getattr(node, "slot_name", None)
    if callable(namer):
        return f"{namer(slot)}: "
    names = getattr(node, "slot_names", None)
    if names and slot < len(names):
        return f"{names[slot]}: "
    return f"Pad {slot + 1}: "


def get_status(node, path, slot=-1):
    """(status, message) for a sample slot. status: Loaded/Loading/Error/Not loaded."""
    if not path:
        return "Not loaded", ""
    state = command_client.get_sample_load_state(node.engine_node_id, slot)
    if not engine_manager.is_engine_running():
        return "Not loaded", "Start the audio engine"
    if state.get("path") != bpy.path.abspath(path):
        return "Not loaded", "Select or reload the sample"
    return state.get("status", "Not loaded"), state.get("message", "")


def slot_failed(node, slot):
    """Did this drum pad's last load fail?

    Lets the Drum Sampler surface a broken pad it is not currently showing,
    without drawing a status line for all eight.
    """
    path = getattr(node, f"slot_{slot}_path", "")
    if not path:
        return False
    return get_status(node, path, slot)[0] == "Error"


def draw_status(layout, node, path, slot=-1):
    if not path:
        return
    status, message = get_status(node, path, slot)
    row = layout.row()
    row.alert = status == "Error"
    row.label(text=_slot_label(node, slot) + status,
              icon='ERROR' if status == "Error" else 'CHECKMARK' if status == "Loaded" else 'TIME')
    if message:
        layout.label(text=message)
        if status == "Error":
            layout.label(text="Previous sample may still be active")
