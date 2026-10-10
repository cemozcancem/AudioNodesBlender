#include "SequencerNode.h"
#include <algorithm>
#include <cmath>

SequencerNode::SequencerNode(const std::string& id)
    : BaseNode(id)
{
    for (auto& pattern : stepPatterns_) {
        pattern.store(0);
    }
    for (auto& trigger : triggerValues_) {
        trigger.store(0.0f);
    }
    laneGateCounter_.fill(0.0);
    laneGateActive_.fill(false);
}

void SequencerNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    sampleRate_ = sampleRate;
    calculateTiming();
    updateStepInterval();
}

void SequencerNode::reset() {
    BaseNode::reset();
    currentStep_.store(0);
    sampleCounter_ = 0.0;
    laneGateCounter_.fill(0.0);
    laneGateActive_.fill(false);
    pendingTransport_.store(-1);
    lastPlayControl_ = 0.0f;
    lastStopControl_ = 0.0f;
    clearAllTriggers();
}

void SequencerNode::clearAllTriggers() {
    for (auto& trigger : triggerValues_) {
        trigger.store(0.0f);
    }
}

int SequencerNode::resolvePortIndex(const std::string& portName) const {
    if (portName == "play" || portName == "start") return PORT_PLAY;
    if (portName == "stop") return PORT_STOP;
    return -1;
}

int SequencerNode::resolveOutputLane(const std::string& portName) const {
    // Only trigger-style ports are valid control sources for Sampler/Drum.
    // "step" / "playing" are UI/monitor sockets - mapping them to lane 0
    // made Step→Trig fire on every non-zero step index.
    if (portName == "trigger" || portName == "output" || portName.empty()) {
        return 0;
    }
    if (portName.rfind("trig_", 0) == 0) {
        const int n = parseIndex(portName, 5);
        if (n >= 1 && n <= MAX_LANES) return n - 1;
    }
    if (portName.rfind("lane_", 0) == 0) {
        const int n = parseIndex(portName, 5);
        if (n >= 1 && n <= MAX_LANES) return n - 1;
    }
    return -1;
}

int SequencerNode::derivedStepCount() const {
    const int beats = std::clamp(beatsPerBar_.load(std::memory_order_relaxed), 1, 16);
    const int perBeat = std::clamp(stepsPerBeat_.load(std::memory_order_relaxed), 1, 16);
    const int bars = std::clamp(bars_.load(std::memory_order_relaxed), 1, 8);
    return std::clamp(beats * perBeat * bars, 1, MAX_STEPS);
}

void SequencerNode::calculateTiming() {
    // Audio thread only (or prepare, while the callback is stopped).
    //
    // BPM is quarter-note based, the way every DAW displays it. One BEAT is
    // the note value named by the time signature's denominator, so in 7/8 a
    // beat is an eighth note and lasts half a quarter; in 4/4 it is the
    // quarter itself. Steps subdivide that beat stepsPerBeat_ ways.
    //
    //   4/4 at 4 steps/beat -> 16 steps of a 16th note  (the old default)
    //   7/8 at 2 steps/beat -> 14 steps of a 16th note, a real 7/8 bar
    const double bpm = std::max(1.0,
        static_cast<double>(bpm_.load(std::memory_order_acquire)));
    const int beatUnit = std::max(1, beatUnit_.load(std::memory_order_acquire));
    const int stepsPerBeat = std::clamp(
        stepsPerBeat_.load(std::memory_order_acquire), 1, 16);

    const double samplesPerQuarter = (sampleRate_ * 60.0) / bpm;
    const double samplesPerBeat = samplesPerQuarter * (4.0 / static_cast<double>(beatUnit));

    // The loop length follows the meter. Keep the playhead inside it when
    // the signature shrinks the bar under our feet.
    const int count = derivedStepCount();
    stepCount_.store(count, std::memory_order_relaxed);

    const double fitBeats =
        static_cast<double>(fitBeats_.load(std::memory_order_acquire));
    if (fitBeats > 0.0) {
        // Polyrhythm: the whole loop spans fitBeats quarter notes, so the
        // steps divide that span instead of following the pulse. 7 steps
        // into 4 beats gives 7 evenly spaced hits in the time another
        // sequencer plays 4 - a 7:4 cross-rhythm, not just a longer bar.
        samplesPerStep_ = std::max(
            1.0, samplesPerQuarter * fitBeats / static_cast<double>(count));
    } else {
        samplesPerStep_ = std::max(
            1.0, samplesPerBeat / static_cast<double>(stepsPerBeat));
    }
    if (currentStep_.load(std::memory_order_relaxed) >= count) {
        currentStep_.store(0, std::memory_order_relaxed);
        sampleCounter_ = 0.0;
    }
}

void SequencerNode::updateStepInterval() {
    // Swing: the interval leading INTO an odd (off-beat) step is lengthened,
    // the interval into the following even step is shortened by the same
    // amount, keeping the bar length constant.
    const float swing = swing_.load(std::memory_order_relaxed);
    const int step = currentStep_.load(std::memory_order_relaxed);
    const int count = std::max(1, stepCount_.load(std::memory_order_relaxed));
    const int nextStep = (step + 1) % count;

    double interval = samplesPerStep_;
    if (swing > 0.0f) {
        if (nextStep % 2 == 1) {
            interval *= (1.0 + swing * 0.5);
        } else {
            interval *= (1.0 - swing * 0.5);
        }
    }
    stepInterval_ = std::max(1.0, interval);
}

void SequencerNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    (void)buffer;

    // Muted (bypassed): emit no triggers, hold position.
    if (isBypassed()) {
        laneGateActive_.fill(false);
        clearAllTriggers();
        return;
    }

    // Apply staged timing changes (BPM / time signature)
    if (timingDirty_.exchange(false, std::memory_order_acq_rel)) {
        calculateTiming();
        updateStepInterval();
    }

    // Play / Stop rising edges from connected control sources (e.g. Bang)
    if (BaseNode* playInput = getInput(PORT_PLAY)) {
        const float playVal = playInput->getControlOutput(getInputLane(PORT_PLAY));
        if (playVal > 0.5f && lastPlayControl_ <= 0.5f) {
            pendingTransport_.store(1, std::memory_order_relaxed);
        }
        lastPlayControl_ = playVal;
    }
    if (BaseNode* stopInput = getInput(PORT_STOP)) {
        const float stopVal = stopInput->getControlOutput(getInputLane(PORT_STOP));
        if (stopVal > 0.5f && lastStopControl_ <= 0.5f) {
            pendingTransport_.store(0, std::memory_order_relaxed);
        }
        lastStopControl_ = stopVal;
    }

    // Apply staged transport changes
    const int transport = pendingTransport_.exchange(-1, std::memory_order_acq_rel);
    if (transport == 1) {
        // A Play edge ALWAYS restarts from step 0, even while already
        // playing. It used to be ignored unless the sequencer was stopped,
        // so a Bang wired to Play fired once and every later press did
        // nothing at all.
        currentStep_.store(0);
        sampleCounter_ = 0.0;
        laneGateActive_.fill(false);
        clearAllTriggers();
        isPlaying_.store(true);
        updateStepInterval();
        triggerStep();
    } else if (transport == 0) {
        isPlaying_.store(false);
        laneGateActive_.fill(false);
        clearAllTriggers();
    }

    if (!isPlaying_.load(std::memory_order_relaxed)) {
        return;
    }

    const float gateLen = gateLength_.load(std::memory_order_relaxed);
    // Guarantee a falling edge before the next step so downstream samplers
    // always see a fresh rising edge, even at gate length 1.0.
    const double gateSamples =
        std::min(samplesPerStep_ * static_cast<double>(gateLen), stepInterval_ * 0.9);

    for (int sample = 0; sample < numSamples; ++sample) {
        sampleCounter_ += 1.0;

        // Per-lane gate release
        for (int lane = 0; lane < MAX_LANES; ++lane) {
            if (laneGateActive_[lane]) {
                laneGateCounter_[lane] += 1.0;
                if (laneGateCounter_[lane] >= gateSamples) {
                    laneGateActive_[lane] = false;
                    triggerValues_[lane].store(0.0f, std::memory_order_relaxed);
                }
            }
        }

        // Advance to next step
        if (sampleCounter_ >= stepInterval_) {
            sampleCounter_ -= stepInterval_;
            advanceStep();
            updateStepInterval();
            triggerStep();
        }
    }
}

void SequencerNode::advanceStep() {
    const int count = std::max(1, stepCount_.load(std::memory_order_relaxed));
    const int step = (currentStep_.load(std::memory_order_relaxed) + 1) % count;
    currentStep_.store(step, std::memory_order_relaxed);
}

void SequencerNode::triggerStep() {
    const int step = currentStep_.load(std::memory_order_relaxed);

    for (int lane = 0; lane < MAX_LANES; ++lane) {
        if (getStep(lane, step)) {
            triggerValues_[lane].store(1.0f, std::memory_order_relaxed);
            laneGateActive_[lane] = true;
            laneGateCounter_[lane] = 0.0;
        }
    }
}

void SequencerNode::setStep(int lane, int step, bool active) {
    if (lane < 0 || lane >= MAX_LANES || step < 0 || step >= MAX_STEPS) {
        return;
    }

    uint64_t pattern = stepPatterns_[lane].load();
    const uint64_t mask = 1ULL << step;

    if (active) {
        pattern |= mask;
    } else {
        pattern &= ~mask;
    }

    stepPatterns_[lane].store(pattern);
}

bool SequencerNode::getStep(int lane, int step) const {
    if (lane < 0 || lane >= MAX_LANES || step < 0 || step >= MAX_STEPS) {
        return false;
    }

    const uint64_t pattern = stepPatterns_[lane].load(std::memory_order_relaxed);
    return (pattern & (1ULL << step)) != 0;
}

void SequencerNode::setPattern(int lane, uint64_t pattern) {
    if (lane >= 0 && lane < MAX_LANES) {
        stepPatterns_[lane].store(pattern);
    }
}

uint64_t SequencerNode::getPattern(int lane) const {
    if (lane >= 0 && lane < MAX_LANES) {
        return stepPatterns_[lane].load();
    }
    return 0;
}

bool SequencerNode::isTriggerActive(int lane) const {
    if (lane >= 0 && lane < MAX_LANES) {
        return triggerValues_[lane].load() > 0.5f;
    }
    return false;
}

float SequencerNode::getTriggerValue(int lane) const {
    if (lane >= 0 && lane < MAX_LANES) {
        return triggerValues_[lane].load();
    }
    return 0.0f;
}

void SequencerNode::setParameter(const std::string& name, float value) {
    if (name == "bpm") {
        bpm_.store(std::clamp(value, 20.0f, 300.0f));
        timingDirty_.store(true, std::memory_order_release);
    }
    else if (name == "steps" || name == "step_count") {
        // Legacy: the loop length is derived from the time signature now.
        // Accepted (and ignored) so an add-on older than this engine does
        // not get an error back for every sequencer it syncs.
    }
    else if (name == "beats_per_bar") {
        beatsPerBar_.store(static_cast<int>(std::clamp(value, 1.0f, 16.0f)));
        timingDirty_.store(true, std::memory_order_release);
    }
    else if (name == "beat_unit") {
        beatUnit_.store(static_cast<int>(std::clamp(value, 1.0f, 32.0f)));
        timingDirty_.store(true, std::memory_order_release);
    }
    else if (name == "steps_per_beat") {
        stepsPerBeat_.store(static_cast<int>(std::clamp(value, 1.0f, 16.0f)));
        timingDirty_.store(true, std::memory_order_release);
    }
    else if (name == "bars") {
        bars_.store(static_cast<int>(std::clamp(value, 1.0f, 8.0f)));
        timingDirty_.store(true, std::memory_order_release);
    }
    else if (name == "fit_beats") {
        fitBeats_.store(std::clamp(value, 0.0f, 64.0f));
        timingDirty_.store(true, std::memory_order_release);
    }
    else if (name == "swing") {
        swing_.store(std::clamp(value, 0.0f, 1.0f));
    }
    else if (name == "gate_length") {
        gateLength_.store(std::clamp(value, 0.01f, 1.0f));
    }
    else if (name == "play") {
        if (value > 0.5f) play();
    }
    else if (name == "stop") {
        if (value > 0.5f) stop();
    }
    // NOTE: step patterns are deliberately NOT settable through the float
    // set_param path - float32 only carries 24 mantissa bits and corrupts
    // any pattern using steps >= 25. Use the "set_pattern" ZMQ command,
    // which delivers the mask as an exact 64-bit integer.
}

float SequencerNode::getParameter(const std::string& name) const {
    if (name == "bpm") return bpm_.load();
    if (name == "steps" || name == "step_count") return static_cast<float>(stepCount_.load());
    if (name == "beats_per_bar") return static_cast<float>(beatsPerBar_.load());
    if (name == "beat_unit") return static_cast<float>(beatUnit_.load());
    if (name == "steps_per_beat") return static_cast<float>(stepsPerBeat_.load());
    if (name == "bars") return static_cast<float>(bars_.load());
    if (name == "fit_beats") return fitBeats_.load();
    if (name == "swing") return swing_.load();
    if (name == "gate_length") return gateLength_.load();
    if (name == "current_step") return static_cast<float>(currentStep_.load());
    if (name == "is_playing") return isPlaying_.load() ? 1.0f : 0.0f;
    if (name == "trigger") return getTriggerValue(0);

    return 0.0f;
}
