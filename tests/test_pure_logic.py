"""
Pure-logic tests for the add-on's reference/validation code — no Blender.

`bpy` is stubbed, so this runs anywhere Python does (CI included) and
covers the parts that used to break silently:

  * Audio<->Control link validation
  * stable analyzer node references (survive a rename, refuse a stale id)
  * tap reference resolution (regression cover for the 4.12 fix)
  * the driver-expression regex against both node-ref forms
  * find_node's tree scoping
  * LFO RANDOM sample & hold actually holding
  * stale-cache / stale-channel key detection

Usage:
    python3 tests/test_pure_logic.py
"""

import sys, types, importlib.util, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "blender_addon"

# ---- minimal bpy stub -------------------------------------------------
def _prop(*a, **k): return None
props = types.SimpleNamespace(**{n: _prop for n in (
    "StringProperty","FloatProperty","IntProperty","BoolProperty",
    "EnumProperty","CollectionProperty","PointerProperty",
    "FloatVectorProperty","BoolVectorProperty")})

class _Base:
    def __init_subclass__(cls, **kw): pass

bpy = types.ModuleType("bpy")
bpy.props = props
bpy.types = types.SimpleNamespace(
    PropertyGroup=_Base, UIList=_Base, Operator=_Base, Node=_Base,
    NodeTree=_Base, NodeSocket=_Base, Panel=_Base, Menu=_Base,
    Header=_Base, AddonPreferences=_Base, Object=object, NodeTree_=None)
bpy.data = types.SimpleNamespace(node_groups=[], scenes=[], objects=[],
    materials=[], worlds=[], lights=[], cameras=[], meshes=[], curves=[],
    shape_keys=[], speakers=[], armatures=[])
bpy.app = types.SimpleNamespace(
    handlers=types.SimpleNamespace(persistent=lambda f: f, load_post=[], load_pre=[]),
    timers=types.SimpleNamespace(register=lambda *a, **k: None),
    driver_namespace={})
bpy.context = types.SimpleNamespace(preferences=types.SimpleNamespace(addons={}))
sys.modules["bpy"] = bpy
sys.modules["bpy.props"] = props
sys.modules["bpy.types"] = bpy.types

# Register the add-on as a real package so relative imports resolve.
_pkg = types.ModuleType("an"); _pkg.__path__ = [str(ROOT)]; sys.modules["an"] = _pkg
for sub in ("nodes", "nodes.core", "nodes.effects", "nodes.timing",
            "nodes.samplers", "nodes.midi", "ui"):
    full = "an." + sub
    m = types.ModuleType(full); m.__path__ = [str(ROOT/sub.replace(".", "/"))]
    sys.modules[full] = m

def load(name, relpath):
    """Load a module under its real dotted name so `from ..x import y` works."""
    dotted = "an." + relpath[:-3].replace("/", ".")
    spec = importlib.util.spec_from_file_location(dotted, ROOT/relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[dotted] = mod
    spec.loader.exec_module(mod)
    sys.modules[name] = mod
    return mod

# ---- fake node graph --------------------------------------------------
class Sock:
    def __init__(self, name, kind): self.name, self.bl_idname = name, kind
class Link:
    def __init__(self, fn, fs, tn, ts): self.from_node, self.from_socket, self.to_node, self.to_socket = fn, fs, tn, ts
class Node:
    def __init__(self, name, blid, eid="", taps=None):
        self.name, self.bl_idname, self.engine_node_id = name, blid, eid
        self.taps = taps or []
class Coll(list):
    """Stand-in for a Blender collection: a list that also has .get()."""
    def get(self, n, d=None):
        for x in self:
            if getattr(x, "name", None) == n: return x
        return d
Nodes = Coll
class Tree:
    bl_idname = 'AudioNodeTree'
    def __init__(self, name, nodes): self.name, self.nodes = name, Nodes(nodes)

class Tap:
    def __init__(self, tap_id, name): self.tap_id, self.name = tap_id, name

FAILS = []
def check(label, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + f"{label}: got {got!r}")
    if not ok:
        FAILS.append(f"{label}: got {got!r}, want {want!r}")

# ======================================================================
print("\n[1] link type validation")
nt = load("nt", "nodes/core/node_tree.py")
a_out, a_in = Sock("Output", "AudioSocketType"), Sock("Input", "AudioSocketType")
c_out, c_in = Sock("Output", "ControlSocketType"), Sock("Value", "ControlSocketType")
n1, n2 = Node("Osc", "AudioOscillatorNode"), Node("Out", "AudioOutputNode")
check("audio -> audio",     nt._link_type_is_valid(Link(n1,a_out,n2,a_in)), True)
check("control -> control", nt._link_type_is_valid(Link(n1,c_out,n2,c_in)), True)
check("audio -> control",   nt._link_type_is_valid(Link(n1,a_out,n2,c_in)), False)
check("control -> audio",   nt._link_type_is_valid(Link(n1,c_out,n2,a_in)), False)
check("foreign socket",     nt._link_type_is_valid(Link(n1,Sock("x","NodeSocketFloat"),n2,a_in)), True)

# ======================================================================
print("\n[2] analyzer node refs")
ft = load("ft", "nodes/effects/fft_taps.py")
an1 = Node("FFT Analyzer", "AudioFFTAnalyzerNode", "fft_analyzer_7ac41f02",
           [Tap("61f03ca0","Lights"), Tap("aa11bb22","Bass")])
an2 = Node("FFT Analyzer.001", "AudioFFTAnalyzerNode", "fft_analyzer_deadbeef", [])
bpy.data.node_groups = Coll([Tree("Audio Nodes", [an1, an2])])

check("node_ref", ft.node_ref(an1), "FFT Analyzer#7ac41f02")
check("resolve by full ref",  ft.resolve_analyzer("FFT Analyzer#7ac41f02")[1] is an1, True)
check("resolve by bare id",   ft.resolve_analyzer("#7ac41f02")[1] is an1, True)
check("resolve by bare name", ft.resolve_analyzer("FFT Analyzer")[1] is an1, True)
check("id wins over stale name", ft.resolve_analyzer("Wrong Name#7ac41f02")[1] is an1, True)
check("deleted id -> nothing",   ft.resolve_analyzer("FFT Analyzer#99999999")[1], None)
check("unknown bare name",       ft.resolve_analyzer("Nope")[1], None)

# the whole point: rename the node, ref still resolves
an1.name = "Kick Analyzer"
check("survives rename", ft.resolve_analyzer("FFT Analyzer#7ac41f02")[1] is an1, True)
check("old bare name now fails", ft.resolve_analyzer("FFT Analyzer")[1], None)
an1.name = "FFT Analyzer"

# ambiguity
dup = Node("FFT Analyzer", "AudioFFTAnalyzerNode", "fft_analyzer_11112222", [])
bpy.data.node_groups = Coll([Tree("A", [an1]), Tree("B", [dup])])
check("ambiguous name refuses", ft.resolve_analyzer("FFT Analyzer")[1], None)
check("ids stay unambiguous",   ft.resolve_analyzer("FFT Analyzer#11112222")[1] is dup, True)
bpy.data.node_groups = Coll([Tree("Audio Nodes", [an1, an2])])

print("\n[3] tap refs still behave (regression)")
check("tap by full ref", ft.resolve_tap_index(an1.taps, "Lights#61f03ca0"), 0)
check("tap by bare id",  ft.resolve_tap_index(an1.taps, "61f03ca0"), 0)
check("tap by name",     ft.resolve_tap_index(an1.taps, "Bass"), 1)
check("deleted tap id",  ft.resolve_tap_index(an1.taps, "Lights#cccccccc"), -1)
check("legacy position", ft.resolve_tap_index(an1.taps, 1), 1)

# ======================================================================
print("\n[4] driver expression regex parses both node forms")
for expr, want_node in (
    ('audio_tap("FFT Analyzer#7ac41f02", "Lights#61f03ca0")', "FFT Analyzer#7ac41f02"),
    ('audio_tap("FFT Analyzer", "Lights#61f03ca0")',          "FFT Analyzer"),
    ('audio_tap("FFT Analyzer", 3)',                          "FFT Analyzer"),
):
    m = ft._TAP_EXPR.search(expr)
    check(f"parse {expr[:34]}...", m.group("node") if m else None, want_node)

# ======================================================================
print("\n[5] find_node scoping")
nb = load("nb", "nodes/core/node_base.py")
treeA = Tree("A", [Node("Sampler", "AudioSamplerNode", "sampler_1")])
treeB = Tree("B", [Node("Sampler", "AudioSamplerNode", "sampler_2")])
bpy.data.node_groups = Coll([treeA, treeB])
ctxA = types.SimpleNamespace(space_data=types.SimpleNamespace(edit_tree=treeA, node_tree=treeA))
ctxNone = types.SimpleNamespace(space_data=None)

check("editor tree wins",
      nb.find_node(ctxA, "Sampler", "AudioSamplerNode").engine_node_id, "sampler_1")
check("explicit tree_name wins",
      nb.find_node(ctxA, "Sampler", "AudioSamplerNode", tree_name="B").engine_node_id, "sampler_2")
check("wrong type refused",
      nb.find_node(ctxA, "Sampler", "AudioADSRNode"), None)
check("no context + ambiguous -> None",
      nb.find_node(ctxNone, "Sampler", "AudioSamplerNode"), None)
bpy.data.node_groups = Coll([treeA])
check("no context + unambiguous -> found",
      nb.find_node(ctxNone, "Sampler", "AudioSamplerNode").engine_node_id, "sampler_1")
check("missing name -> None",
      nb.find_node(ctxNone, "Nope", "AudioSamplerNode"), None)

# ======================================================================
print("\n[6] LFO random sample & hold")
lfo = load("lfo", "nodes/timing/lfo_node.py")
class FakeLFO:
    waveform, depth, offset, sync_to_bpm, rate = 'RANDOM', 1.0, 0.0, False, 1.0
    engine_node_id = "lfo_abc12345"
    get_output_value = lfo.AudioLFONode.get_output_value
vals = [round(FakeLFO().get_output_value(10.0 + i*0.0166, 120.0), 6) for i in range(8)]
check("held within one cycle", len(set(vals)), 1)
nxt = FakeLFO().get_output_value(11.5, 120.0)
check("changes on next cycle", round(nxt,6) != vals[0], True)

# ======================================================================
print("\n[7] stale-key detection")
smr_src = (ROOT/"shared_memory_reader.py").read_text()
ns = {}
exec(compile(re.search(r"def _is_dead_key.*?return True\n", smr_src, re.S).group(0), "x", "exec"), ns)
dead = ns["_is_dead_key"]
live = {"fft_analyzer_7ac41f02", "lfo_abc12345"}
check("live exact",       dead("lfo_abc12345", live), False)
check("live prefixed",    dead("fft_analyzer_7ac41f02_tap_61f03ca0", live), False)
check("dead node",        dead("sampler_deadbeef_volume", live), True)
check("global untouched", dead("peak_left", live), True)  # never passed to it

chan_re = re.compile(r'^([a-z_]+_[0-9a-f]{8})_')
for name, should_match in (("fft_analyzer_7ac41f02_tap_61f03ca0", True),
                           ("value_output_11aa22bb_value", True),
                           ("peak_left", False), ("midi_cc_5", False),
                           ("seq_bpm", False), ("input_peak_3", False),
                           ("midi_last_note", False)):
    check(f"channel regex {name}", bool(chan_re.match(name)), should_match)

print("\n" + "="*60)
print("FAILURES:", len(FAILS))
for f in FAILS: print("  -", f)
sys.exit(1 if FAILS else 0)
