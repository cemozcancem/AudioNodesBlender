#include "BangNode.h"
#include <algorithm>
#include <cmath>

BangNode::BangNode(const std::string& id)
    : BaseNode(id)
{
}

void BangNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    sampleRate_ = sampleRate;
}

void BangNode::reset() {
    BaseNode::reset();
    output_.store(0.0f);
    samplesLeft_ = 0;
    armNextBlock_ = false;
    lastTriggerControl_ = 0.0f;
    // Deliberately NOT resetting fireRequests_/lastSeenFireRequests_ to
    // zero independently: they must stay in step, or a reset would look
    // like a pending fire (or swallow a real one).
    lastSeenFireRequests_ = fireRequests_.load(std::memory_order_acquire);
}

int BangNode::resolvePortIndex(const std::string& portName) const {
    if (portName == "trigger" || portName == "input") return PORT_TRIGGER;
    return -1;
}

int BangNode::resolveOutputLane(const std::string& portName) const {
    if (portName == "trigger" || portName == "output" || portName.empty()) {
        return 0;
    }
    return -1;
}

float BangNode::getControlOutput(int lane) const {
    (void)lane;
    return output_.load(std::memory_order_relaxed);
}

void BangNode::armPulse() {
    const float ms = gateMs_.load(std::memory_order_relaxed);
    const int samples = std::max(1, static_cast<int>(sampleRate_ * ms * 0.001));
    samplesLeft_ = samples;
    output_.store(1.0f, std::memory_order_relaxed);
    firedPulses_.fetch_add(1, std::memory_order_relaxed);
}

void BangNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    (void)buffer;

    if (isBypassed()) {
        output_.store(0.0f, std::memory_order_relaxed);
        samplesLeft_ = 0;
        armNextBlock_ = false;
        lastSeenFireRequests_ = fireRequests_.load(std::memory_order_acquire);
        return;
    }

    // Every press since the last block counts as one fire request. Reading
    // the counter (instead of exchanging a flag) is what makes repeated
    // presses work.
    bool wantFire = false;
    const uint32_t requests = fireRequests_.load(std::memory_order_acquire);
    if (requests != lastSeenFireRequests_) {
        lastSeenFireRequests_ = requests;
        wantFire = true;
    }

    if (BaseNode* triggerInput = getInput(PORT_TRIGGER)) {
        const float trigVal = triggerInput->getControlOutput(getInputLane(PORT_TRIGGER));
        if (trigVal > 0.5f && lastTriggerControl_ <= 0.5f) {
            wantFire = true;
        }
        lastTriggerControl_ = trigVal;
    }

    if (armNextBlock_) {
        // The gap block requested by a retrigger has been emitted.
        armNextBlock_ = false;
        armPulse();
    } else if (wantFire) {
        if (samplesLeft_ > 0) {
            // Still inside the previous pulse: go low for exactly one block
            // so every downstream edge detector sees 1 → 0 → 1.
            samplesLeft_ = 0;
            output_.store(0.0f, std::memory_order_relaxed);
            armNextBlock_ = true;
            return;
        }
        armPulse();
    }

    if (samplesLeft_ > 0) {
        samplesLeft_ -= numSamples;
        if (samplesLeft_ <= 0) {
            samplesLeft_ = 0;
            output_.store(0.0f, std::memory_order_relaxed);
        } else {
            output_.store(1.0f, std::memory_order_relaxed);
        }
    }
}

void BangNode::setParameter(const std::string& name, float value) {
    if (name == "fire" || name == "trigger") {
        if (value > 0.5f) {
            fireRequests_.fetch_add(1, std::memory_order_acq_rel);
        }
    } else if (name == "gate_ms") {
        gateMs_.store(std::clamp(value, 1.0f, 500.0f));
    }
}

float BangNode::getParameter(const std::string& name) const {
    if (name == "gate_ms") return gateMs_.load();
    if (name == "pulses") return static_cast<float>(firedPulses_.load());
    if (name == "trigger" || name == "output") {
        return output_.load();
    }
    return 0.0f;
}
