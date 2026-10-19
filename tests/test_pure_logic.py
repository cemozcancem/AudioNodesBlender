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

# ======================================================================
# Issue #6: the add-on (and the test harness) must wait longer for a device
# switch than the engine does, or a slow switch that works is reported as
# an error. Read the limits from the sources so they can't drift apart.
print("\n[8] device-switch timeouts (issue #6)")
import ast, json
REPO = ROOT.parent
MIN_MARGIN_S = 3   # "a few seconds" on top of the engine's limit

cpp_src = (REPO/"engine/src/AudioEngine.cpp").read_text()
engine_waits = [int(s) for s in re.findall(
    r"wait_for\(\s*std::chrono::seconds\(\s*(\d+)\s*\)\s*\)", cpp_src)]
check("engine has exactly one device-switch wait", len(engine_waits), 1)
engine_limit_s = engine_waits[0] if engine_waits else None

cc_tree = ast.parse((ROOT/"command_client.py").read_text())
cc_consts = {}
for stmt in cc_tree.body:
    if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)):
        try:
            cc_consts[stmt.targets[0].id] = eval(
                compile(ast.Expression(stmt.value), "cc", "eval"),
                {"__builtins__": {}}, dict(cc_consts))
        except Exception:
            pass   # non-constant module globals (sockets, locks, ...)
check("ENGINE_DEVICE_SWITCH_LIMIT_S matches AudioEngine.cpp",
      cc_consts.get("ENGINE_DEVICE_SWITCH_LIMIT_S"), engine_limit_s)

slow_fn = next((n for n in cc_tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "send_slow_command"), None)
slow_default_ms = None
if slow_fn is not None:
    names = [a.arg for a in slow_fn.args.args]
    defaults = dict(zip(names[-len(slow_fn.args.defaults):], slow_fn.args.defaults)) \
        if slow_fn.args.defaults else {}
    if "timeout_ms" in defaults:
        slow_default_ms = eval(compile(ast.Expression(defaults["timeout_ms"]), "cc", "eval"),
                               {"__builtins__": {}}, dict(cc_consts))
need_ms = ((engine_limit_s or 0) + MIN_MARGIN_S) * 1000
check(f"send_slow_command default ({slow_default_ms} ms) >= engine limit + "
      f"{MIN_MARGIN_S} s ({need_ms} ms)",
      slow_default_ms is not None and slow_default_ms >= need_ms, True)

harness_src = (REPO/"tests/harness.py").read_text()
hm = re.search(r"RCVTIMEO,\s*(\d+)\s+if\s+slow\b", harness_src)
harness_slow_ms = int(hm.group(1)) if hm else None
check(f"harness slow-command timeout ({harness_slow_ms} ms) >= engine limit + "
      f"{MIN_MARGIN_S} s ({need_ms} ms)",
      harness_slow_ms is not None and harness_slow_ms >= need_ms, True)

# Behaviour: drive set_device() through a fake zmq whose "engine" answers
# after a simulated delay. A socket whose RCVTIMEO is shorter than the delay
# raises Again, exactly like pyzmq. No real waiting happens.
class _Again(Exception):
    pass

class _FakeEngine:
    def __init__(self, reply, delay_ms, devices):
        self.reply, self.delay_ms, self.devices = reply, delay_ms, devices

class _FakeSocket:
    def __init__(self, ctx):
        self.ctx, self.opts, self.cmd = ctx, {}, None
    def setsockopt(self, k, v): self.opts[k] = v
    def connect(self, addr): pass
    def send_string(self, s): self.cmd = json.loads(s)
    def recv_string(self):
        eng = self.ctx.engine
        if self.cmd["type"] == "set_device":
            if self.opts.get(_fzmq.RCVTIMEO, 0) < eng.delay_ms:
                raise _Again()
            return json.dumps(eng.reply)
        if self.cmd["type"] == "get_devices":
            return json.dumps({"status": "ok", "data": eng.devices})
        return json.dumps({"status": "ok"})
    def close(self, linger=0): pass

class _FakeContext:
    engine = None
    def __init__(self): pass
    def socket(self, kind): return _FakeSocket(self)
    def term(self): pass

_fzmq = types.ModuleType("zmq")
_fzmq.REQ, _fzmq.RCVTIMEO, _fzmq.SNDTIMEO, _fzmq.LINGER = 3, 27, 28, 17
_fzmq.Context = _FakeContext
_fzmq.error = types.SimpleNamespace(Again=_Again)

_saved = {k: sys.modules.get(k) for k in ("zmq", "an.command_client", "cc")}
sys.modules["zmq"] = _fzmq
try:
    cc = load("cc", "command_client.py")
    old_devs = {"current_input": "MacBook Pro Microphone",
                "current_output": "MacBook Pro Speakers"}
    new_devs = dict(old_devs, current_output="USB Interface")

    # A switch that takes 12 s and works: success. get_devices() still shows
    # the old device, so only the real reply can make this pass.
    _FakeContext.engine = _FakeEngine(
        {"status": "ok", "message": "", "data": new_devs}, 12000, old_devs)
    r = cc.set_device("output", "USB Interface")
    check("12 s switch reported as success", (r["ok"], r["timed_out"]), (True, False))
    check("12 s switch returns engine's devices", r["data"].get("current_output"),
          "USB Interface")

    # Right at the engine's own limit (+1 s for the reply): still success.
    _FakeContext.engine = _FakeEngine(
        {"status": "ok", "message": "", "data": new_devs},
        ((engine_limit_s or 15) + 1) * 1000, old_devs)
    r = cc.set_device("output", "USB Interface")
    check("switch at engine limit reported as success", r["ok"], True)

    # A real failure: the engine answers with an error. Clear message.
    _FakeContext.engine = _FakeEngine(
        {"status": "error", "message": "Could not open USB Interface"}, 1000, old_devs)
    r = cc.set_device("output", "USB Interface")
    check("engine error reported as failure", (r["ok"], r["timed_out"]), (False, False))
    check("engine error keeps its message", r["message"], "Could not open USB Interface")

    # Engine never answers and the device did not change: failure, with text.
    _FakeContext.engine = _FakeEngine({"status": "ok"}, 10**9, old_devs)
    r = cc.set_device("output", "USB Interface")
    check("no reply + unchanged device -> failure", (r["ok"], r["timed_out"]), (False, True))
    check("no reply has a message", bool(r["message"]), True)
finally:
    for k, v in _saved.items():
        if v is None: sys.modules.pop(k, None)
        else: sys.modules[k] = v

print("\n" + "="*60)
print("FAILURES:", len(FAILS))
for f in FAILS: print("  -", f)
sys.exit(1 if FAILS else 0)
