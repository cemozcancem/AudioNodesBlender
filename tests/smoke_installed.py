"""Headless smoke test of the INSTALLED audio_nodes package.

Runs a separate, factory-startup Blender against whatever is currently in
the add-ons folder - so it tests the packaged artifact, not the working
tree, and cannot be fooled by a partial dev copy or by module state left
over in a running session.

    /Applications/Blender.app/Contents/MacOS/Blender --background \
        --factory-startup --python tests/smoke_installed.py

Exits non-zero if any check fails. Needs an audio device (it starts the
engine and pushes a 440 Hz tone through a real FFT).
"""
import bpy, sys, time

def ok(label, cond):
    print(("PASS  " if cond else "FAIL  ") + label)
    return bool(cond)

results = []
bpy.ops.preferences.addon_enable(module="audio_nodes")
import audio_nodes as an
from audio_nodes import engine_manager, command_client, shared_memory_reader as smr
from audio_nodes.nodes.core import node_tree as nt
from audio_nodes.nodes.effects import fft_taps

results.append(ok(f"addon registers (v{an.bl_info['version']})", an.bl_info["version"] >= (4,13,0)))
results.append(ok("send_midi_note registered", hasattr(bpy.types, "AUDIONODES_OT_send_midi_note")))
results.append(ok("cancel_pending_sync present", hasattr(nt, "cancel_pending_sync")))

tree = bpy.data.node_groups.new("Smoke", 'AudioNodeTree')
made = {}
for t in ["AudioInputNode","AudioOutputNode","AudioOscillatorNode","AudioLFONode",
          "AudioFilterNode","AudioGainNode","AudioDelayNode","AudioMixerNode",
          "AudioMathNode","AudioFFTAnalyzerNode","AudioValueOutputNode","AudioNLAScrubNode",
          "AudioMIDIInputNode","AudioMIDICCNode","AudioMIDIClockNode","AudioMIDIOutputNode",
          "AudioMIDIMonitorNode","AudioSequencerNode","AudioBangNode","AudioSamplerNode",
          "AudioDrumSamplerNode","AudioADSRNode"]:
    made[t] = tree.nodes.new(t)
results.append(ok(f"all {len(made)} node types instantiate", len(made) == 22))

# cross-type link rejection
osc, vo = made["AudioOscillatorNode"], made["AudioValueOutputNode"]
tree.links.new(osc.outputs["Output"], vo.inputs["Value"])
before = len(tree.links); nt.prune_invalid_links(tree)
results.append(ok("audio->control link rejected", before == 1 and len(tree.links) == 0))

# engine: real signal path
fft, out = made["AudioFFTAnalyzerNode"], made["AudioOutputNode"]
osc.frequency = 440.0; out.mute = True
tree.links.new(osc.outputs["Output"], fft.inputs["Input"])
tree.links.new(fft.outputs["Output"], out.inputs["Input"])
started = engine_manager.start_engine()
results.append(ok("engine starts from packaged .app", started))
if started:
    results.append(ok("engine answers ZMQ ping", command_client.ping()))
    time.sleep(1.5)
    d = smr.read_visualization_data()
    results.append(ok("shared memory readable", d is not None))
    if d:
        slot = d['fft_nodes'].get(fft.engine_node_id)
        if slot:
            b = slot['fft_left']
            pk = max(range(len(b)), key=lambda i: b[i]); hz = pk*44100/2048
            results.append(ok(f"440Hz tone -> FFT peak {hz:.0f}Hz (mag {b[pk]:.2f})", 380 < hz < 500))
        else:
            results.append(ok("FFT slot published", False))

    # stable analyzer ref survives rename
    fft.add_tap(120.0, 0.6); fft.taps[-1].name = "Bass"
    ref = fft_taps.node_ref(fft); fft.name = "Renamed"
    results.append(ok("analyzer driver ref survives rename",
                      fft_taps.resolve_analyzer(ref)[1] is not None))

    # two sequencers must report independent transports (4.14)
    s1, s2 = made["AudioSequencerNode"], tree.nodes.new("AudioSequencerNode")
    s1.bpm, s2.bpm = 200.0, 60.0
    command_client.flush_queue()
    bpy.ops.audionodes.sequencer_play(node_name=s1.name, tree_name=tree.name)
    command_client.flush_queue()
    slot1 = slot2 = None
    import time as _t
    deadline = _t.time() + 8.0
    while _t.time() < deadline:
        d = smr.read_visualization_data() or {}
        slots = d.get("sequencer_nodes", {})
        slot1, slot2 = slots.get(s1.engine_node_id), slots.get(s2.engine_node_id)
        if slot1 and slot1["is_playing"]:
            break
        _t.sleep(0.1)
    results.append(ok("sequencer A reports playing in its own slot",
                      bool(slot1 and slot1["is_playing"])))
    results.append(ok("sequencer B stays stopped (no shared transport)",
                      bool(slot2 and not slot2["is_playing"])))
    results.append(ok("each sequencer slot carries its own BPM",
                      bool(slot1 and slot2 and round(slot1["bpm"]) == 200
                           and round(slot2["bpm"]) == 60)))
    bpy.ops.audionodes.sequencer_stop(node_name=s1.name, tree_name=tree.name)

    # mixer collapses its per-channel control sockets
    mixer = made["AudioMixerNode"]
    ctrl = [x for x in mixer.inputs if x.bl_idname == "ControlSocketType"]
    results.append(ok(f"mixer hides all {len(ctrl)} per-channel control inputs",
                      len(ctrl) == 16 and all(x.hide for x in ctrl)))

    engine_manager.stop_engine()

print("=" * 50)
print(f"{sum(results)}/{len(results)} PASSED")
sys.exit(0 if all(results) else 1)
