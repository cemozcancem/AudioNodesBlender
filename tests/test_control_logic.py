"""
Pure-logic tests for the Control nodes (Trigger, Accumulator, Decay,
Camera Cutter). No Blender and no bpy stub needed: the state machines
live in blender_addon/nodes/control/control_logic.py, which imports
nothing from Blender.

Usage:
    python3 tests/test_control_logic.py
"""

import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "blender_addon"
_spec = importlib.util.spec_from_file_location(
    "control_logic", ROOT / "nodes" / "control" / "control_logic.py")
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)

DT = 1.0 / 60.0
_failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def run(logic_step, signal):
    return [logic_step(x) for x in signal]


# ---- Trigger ------------------------------------------------------------
def test_trigger():
    print("Trigger")
    t = cl.TriggerLogic(seed=1)
    # a kick that rings above threshold for 10 ticks fires once
    sig = [0.0] * 5 + [0.9] * 10 + [0.0] * 5
    out = [t.step(x, DT, 0.6, 0.3) for x in sig]
    fires = sum(1 for i in range(1, len(out)) if out[i][0] == 1.0 and out[i - 1][0] == 0.0)
    check("ringing hit fires once", fires == 1, fires)
    check("count == 1", out[-1][2] == 1.0)
    check("gate follows threshold", out[7][1] == 1.0 and out[-1][1] == 0.0)

    # hysteresis: dipping to 0.5 (above re-arm 0.3) does not re-fire
    t = cl.TriggerLogic(seed=1)
    sig = [0.0, 0.9, 0.5, 0.9, 0.5, 0.9, 0.0]
    for x in sig:
        t.step(x, DT, 0.6, 0.3)
    check("hysteresis blocks chatter", t.count == 1, t.count)

    # cooldown: two hits 0.1s apart with 0.25s cooldown -> one fire
    t = cl.TriggerLogic(seed=1)
    sig = [0.9] + [0.0] * 5 + [0.9] + [0.0] * 30 + [0.9]
    for x in sig:
        t.step(x, DT, 0.6, 0.3, cooldown=0.25)
    check("cooldown drops fast repeat", t.count == 2, t.count)

    # pulse lasts at least one tick even with pulse_length 0
    t = cl.TriggerLogic(seed=1)
    p = t.step(0.9, DT, 0.6, 0.3, pulse_length=0.0)[0]
    check("zero-length pulse still visible for one tick", p == 1.0)

    # probability 0 never fires, 1 always fires
    t = cl.TriggerLogic(seed=1)
    for _ in range(50):
        t.step(0.9, DT, 0.6, 0.3, probability=0.0)
        t.step(0.0, DT, 0.6, 0.3, probability=0.0)
    check("probability 0 never fires", t.count == 0)
    t = cl.TriggerLogic(seed=3)
    for _ in range(400):
        t.step(0.9, DT, 0.6, 0.3, probability=0.5)
        t.step(0.0, DT, 0.6, 0.3, probability=0.5)
    check("probability 0.5 ~ half", 140 < t.count < 260, t.count)


# ---- Accumulator --------------------------------------------------------
def test_accumulator():
    print("Accumulator")
    a = cl.AccumulatorLogic()
    wraps = 0
    for _ in range(150):  # 2.5 s at rate 1 -> wraps twice, ends near 0.5
        v, w = a.step(1.0, DT, mode='WRAP')
        wraps += w
    check("wrap value", abs(v - 0.5) < 0.02, v)
    check("wrap pulses", wraps == 2, wraps)

    a = cl.AccumulatorLogic()
    for _ in range(90):  # 1.5 s -> ping-pong back to 0.5
        v, _ = a.step(1.0, DT, mode='PINGPONG')
    check("ping-pong comes back", abs(v - 0.5) < 0.02, v)

    a = cl.AccumulatorLogic()
    for _ in range(600):
        v, _ = a.step(1.0, DT, mode='CLAMP', low=0.0, high=2.0)
    check("clamp stops at high", abs(v - 2.0) < 1e-9, v)

    a = cl.AccumulatorLogic()
    for _ in range(60):
        v, _ = a.step(0.0, DT, base=2.0, gain=1.0, mode='NONE')
    check("base rate w/o input", abs(v - 2.0) < 0.02, v)

    a = cl.AccumulatorLogic()
    for _ in range(30):
        a.step(1.0, DT, mode='NONE')
    v, _ = a.step(0.0, DT, mode='NONE', reset=1.0, reset_value=0.25)
    check("reset edge jumps", abs(v - 0.25) < 1e-9, v)
    v, _ = a.step(0.0, DT, mode='NONE', reset=1.0, reset_value=0.25)
    check("held reset is edge-only", abs(v - 0.25) < 1e-9, v)

    # louder = faster: rate 2 covers twice the distance
    a1, a2 = cl.AccumulatorLogic(), cl.AccumulatorLogic()
    for _ in range(30):
        v1, _ = a1.step(0.5, DT, mode='NONE')
        v2, _ = a2.step(1.0, DT, mode='NONE')
    check("rate scales speed", abs(v2 - 2 * v1) < 1e-9)


# ---- Decay --------------------------------------------------------------
def test_decay():
    print("Decay")
    d = cl.DecayLogic()
    v0 = d.step(DT, trigger=1.0, peak=1.0, decay_time=0.5)
    check("trigger jumps to peak", abs(v0 - 1.0) < 1e-9, v0)
    v = v0
    for _ in range(30):  # 0.5 s
        v = d.step(DT, trigger=1.0, peak=1.0, decay_time=0.5)
    check("falls to 10% after decay_time", abs(v - 0.1) < 0.01, v)
    d = cl.DecayLogic()
    d.step(DT, signal=0.7, decay_time=1.0)
    v = d.step(DT, signal=0.2, decay_time=1.0)
    check("follower holds peak, then releases", 0.6 < v < 0.7, v)


# ---- Camera Cutter ------------------------------------------------------
def test_cutter():
    print("Camera Cutter")
    c = cl.CutterLogic(seed=1)
    # speed 0, base 0.5, length 4 s -> 8 s per shot
    for _ in range(240):  # 4 s
        slot, p, pulse, t = c.step(DT, 3, speed=0.0, base_speed=0.5, speed_gain=1.0,
                                   shot_duration=4.0)
    check("progress speed", abs(p - 0.5) < 0.01, p)
    check("still first shot", slot == 0)

    # louder music moves faster
    c1, c2 = cl.CutterLogic(seed=1), cl.CutterLogic(seed=1)
    for _ in range(60):
        _, p1, _, _ = c1.step(DT, 3, speed=0.0)
        _, p2, _, _ = c2.step(DT, 3, speed=1.0)
    check("speed input accelerates", p2 > 2 * p1, (p1, p2))

    # cut ignored before min_shot, honoured after
    c = cl.CutterLogic(seed=1)
    c.step(DT, 3, cut=0.0, min_shot=1.0)
    s, _, pulse, _ = c.step(DT, 3, cut=1.0, min_shot=1.0)
    check("early cut ignored", s == 0 and pulse == 0.0)
    for _ in range(70):
        c.step(DT, 3, cut=0.0, min_shot=1.0)
    s, p, pulse, t = c.step(DT, 3, cut=1.0, min_shot=1.0)
    check("cut after min_shot", s == 1 and pulse == 1.0 and p == 0.0 and t == 0.0, (s, pulse, p))

    # auto advance at end of move
    c = cl.CutterLogic(seed=1)
    seen = set()
    for _ in range(60 * 30):
        s, *_ = c.step(DT, 3, speed=1.0, base_speed=0.0, shot_duration=1.0)
        seen.add(s)
    check("auto advance cycles all shots", seen == {0, 1, 2}, seen)

    # hold instead of advance
    c = cl.CutterLogic(seed=1)
    for _ in range(600):
        s, p, *_ = c.step(DT, 3, speed=1.0, base_speed=0.0, shot_duration=1.0,
                          auto_advance=False)
    check("hold at end", s == 0 and p == 1.0)

    # shuffle: every shot once per round, never the same twice in a row
    c = cl.CutterLogic(seed=7)
    order = [c.slot]
    for _ in range(24):
        order.append(c.force_cut(5, 'SHUFFLE'))
    no_repeat = all(a != b for a, b in zip(order, order[1:]))
    check("shuffle never repeats back-to-back", no_repeat, order)
    first_round = order[1:5]
    check("shuffle round covers others", sorted(first_round) == [1, 2, 3, 4], first_round)

    c = cl.CutterLogic(seed=7)
    picks = [c.force_cut(4, 'RANDOM') for _ in range(100)]
    check("random never repeats back-to-back",
          all(a != b for a, b in zip(picks, picks[1:])))

    # shot list shrinking under a running cutter
    c = cl.CutterLogic(seed=1)
    c.force_cut(5)
    c.force_cut(5)
    s, *_ = c.step(DT, 2)
    check("survives shot list shrinking", 0 <= s < 2, s)
    s, p, pulse, t = cl.CutterLogic().step(DT, 0)
    check("no shots -> zeros", (s, p, pulse, t) == (0, 0.0, 0.0, 0.0))


if __name__ == "__main__":
    test_trigger()
    test_accumulator()
    test_decay()
    test_cutter()
    if _failures:
        print(f"\n{len(_failures)} failure(s): {_failures}")
        sys.exit(1)
    print("\nAll control-logic tests passed.")
