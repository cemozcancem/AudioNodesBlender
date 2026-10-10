"""Pure-Python state machines behind the Control nodes.

No bpy in here on purpose: the Trigger, Accumulator, Decay and Camera
Cutter nodes are thin Blender wrappers around these classes, and the
classes are what tests/test_pure_logic.py exercises without Blender.

Every step() takes `dt` in seconds (real time between visualization
ticks) so behaviour is frame-rate independent.
"""

import math
import random


class TriggerLogic:
    """Rising-edge detector with hysteresis, cooldown and probability.

    Fires when the signal crosses `threshold` upward while armed. It only
    re-arms once the signal has dropped below `rearm` (hysteresis), so a
    kick that rings above the threshold for 80 ms fires once, not five
    times. `cooldown` is the minimum time between fires; `probability`
    lets only some onsets through.

    Outputs per step: (pulse, gate, count)
      pulse - 1.0 for `pulse_length` seconds after a fire (at least one
              tick, so a consumer polling at 60 fps can never miss it)
      gate  - 1.0 while the signal is above the threshold
      count - number of fires so far
    """

    def __init__(self, seed=None):
        self.armed = True
        self.since_fire = 1.0e9
        self.pulse_left = 0.0
        self.count = 0
        self.rng = random.Random(seed)

    def reset(self):
        self.armed = True
        self.since_fire = 1.0e9
        self.pulse_left = 0.0
        self.count = 0

    def step(self, x, dt, threshold=0.5, rearm=0.3, cooldown=0.0,
             probability=1.0, pulse_length=0.05):
        rearm = min(rearm, threshold)
        self.since_fire += dt
        fired = False
        if self.armed and x >= threshold:
            self.armed = False
            if self.since_fire >= cooldown and self.rng.random() < probability:
                fired = True
                self.count += 1
                self.since_fire = 0.0
                self.pulse_left = max(pulse_length, 1.0e-6)
        elif not self.armed and x < rearm:
            self.armed = True

        if fired:
            pulse = 1.0
        else:
            self.pulse_left -= dt
            pulse = 1.0 if self.pulse_left > 0.0 else 0.0
        gate = 1.0 if x >= threshold else 0.0
        return pulse, gate, float(self.count)


class EdgeDetector:
    """Minimal rising-edge detector for trigger-style inputs (>= 0.5)."""

    def __init__(self):
        self.prev = 0.0

    def step(self, x, level=0.5):
        rose = x >= level and self.prev < level
        self.prev = x
        return rose


class AccumulatorLogic:
    """Integrates a rate over time: value += (base + gain * rate) * dt.

    Modes:
      'WRAP'     - wraps into [low, high); emits a Wrap pulse each time
      'PINGPONG' - bounces between low and high
      'CLAMP'    - stops at low / high
      'NONE'     - unbounded
    A rising edge on the reset input jumps back to `reset_value`.

    Outputs per step: (value, wrap_pulse)
    """

    def __init__(self):
        self.raw = 0.0
        self.reset_edge = EdgeDetector()

    def reset(self, value=0.0):
        self.raw = value

    def step(self, rate, dt, base=0.0, gain=1.0, mode='WRAP', low=0.0,
             high=1.0, reset=0.0, reset_value=0.0):
        if self.reset_edge.step(reset):
            self.raw = reset_value
        before = self.raw
        self.raw += (base + gain * rate) * dt

        span = high - low
        wrapped = False
        if mode == 'NONE' or span <= 1.0e-9:
            return self.raw, 0.0
        if mode == 'CLAMP':
            self.raw = max(low, min(high, self.raw))
            return self.raw, 0.0
        if mode == 'WRAP':
            if self.raw >= high or self.raw < low:
                wrapped = True
                self.raw = low + math.fmod(self.raw - low, span)
                if self.raw < low:
                    self.raw += span
            return self.raw, 1.0 if wrapped else 0.0
        # PINGPONG: every crossing of a multiple of `span` is a bounce
        k_before = math.floor((before - low) / span)
        k_after = math.floor((self.raw - low) / span)
        wrapped = k_before != k_after
        pos = (self.raw - low) - k_after * span
        value = low + pos if k_after % 2 == 0 else high - pos
        # keep raw bounded so it never loses float precision
        self.raw = low + (self.raw - low) - 2.0 * span * math.floor(
            (self.raw - low) / (2.0 * span))
        return value, 1.0 if wrapped else 0.0


class DecayLogic:
    """Jump-and-decay envelope, also usable as a peak follower.

    A rising edge on `trigger` jumps the envelope to `peak`. A `signal`
    input pulls the envelope up to itself (instant attack). Either way it
    falls exponentially: `decay_time` is the time to drop to 10 %.
    """

    def __init__(self):
        self.env = 0.0
        self.edge = EdgeDetector()

    def reset(self):
        self.env = 0.0

    def step(self, dt, trigger=0.0, signal=0.0, peak=1.0, decay_time=0.3):
        if decay_time <= 1.0e-4:
            self.env = 0.0
        else:
            self.env *= math.exp(-dt * math.log(10.0) / decay_time)
        if self.edge.step(trigger):
            self.env = max(self.env, peak)
        if signal > self.env:
            self.env = signal
        return self.env


class CutterLogic:
    """Shot timing for the Camera Cutter node.

    `progress` runs 0 -> 1 through the current shot at
        (base_speed + speed_gain * speed) / shot_duration   per second
    so louder music moves the shot along faster. A rising edge on `cut`
    switches shot, but only after `min_shot` seconds; reaching the end of
    a shot switches too when `auto_advance` is on (otherwise it holds).

    Order:
      'SEQUENCE' - 0, 1, 2, ... wrapping
      'RANDOM'   - any shot except the current one
      'SHUFFLE'  - every shot once per round, in random order
    """

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.reset()

    def reset(self):
        self.slot = 0            # position in the enabled-shot list
        self.progress = 0.0
        self.time_in_shot = 0.0
        self.cut_edge = EdgeDetector()
        self.bag = []
        self.cut_pulse_left = 0.0
        self.cuts = 0

    def _next(self, n, order):
        if n <= 1:
            return 0
        if order == 'RANDOM':
            choice = self.rng.randrange(n - 1)
            return choice if choice < self.slot else choice + 1
        if order == 'SHUFFLE':
            if not self.bag:
                self.bag = [i for i in range(n) if i != self.slot]
                self.rng.shuffle(self.bag)
            nxt = self.bag.pop()
            return nxt if nxt < n else 0
        return (self.slot + 1) % n

    def force_cut(self, n, order='SEQUENCE'):
        self.slot = self._next(n, order)
        self.progress = 0.0
        self.time_in_shot = 0.0
        self.cuts += 1
        self.cut_pulse_left = 0.05
        return self.slot

    def step(self, dt, n, speed=0.0, cut=0.0, shot_duration=4.0,
             base_speed=0.5, speed_gain=1.0, min_shot=1.5,
             order='SEQUENCE', auto_advance=True):
        """Returns (slot, progress, cut_pulse, time_in_shot)."""
        if n <= 0:
            self.cut_edge.step(cut)
            return 0, 0.0, 0.0, 0.0
        if self.slot >= n:
            self.slot = 0
            self.bag = []

        self.time_in_shot += dt
        rate = max(0.0, base_speed + speed_gain * speed)
        self.progress += dt * rate / max(shot_duration, 1.0e-3)

        cut_now = False
        if self.cut_edge.step(cut) and self.time_in_shot >= min_shot:
            cut_now = True
        if self.progress >= 1.0:
            if auto_advance:
                cut_now = True
            else:
                self.progress = 1.0

        if cut_now:
            self.force_cut(n, order)
            pulse = 1.0
        else:
            self.cut_pulse_left -= dt
            pulse = 1.0 if self.cut_pulse_left > 0.0 else 0.0
        return self.slot, min(self.progress, 1.0), pulse, self.time_in_shot
