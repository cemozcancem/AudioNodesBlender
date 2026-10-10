#include "MathNode.h"
#include <cmath>

MathNode::MathNode(const std::string& id)
    : BaseNode(id)
{
}

void MathNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    inABuffer_.setSize(2, maxBlockSize);
    inBBuffer_.setSize(2, maxBlockSize);
    inABuffer_.clear();
    inBBuffer_.clear();
}

void MathNode::reset() {
    BaseNode::reset();
    inABuffer_.clear();
    inBBuffer_.clear();
}

int MathNode::resolvePortIndex(const std::string& portName) const {
    if (portName == "audio_a" || portName == "input") return PORT_A;
    if (portName == "audio_b") return PORT_B;
    // "a" / "b" control ports are driven via set_param from Python
    return -1;
}

float MathNode::applyOperation(Operation op, float a, float b, float mn, float mx) {
    switch (op) {
        case Operation::Add: return a + b;
        case Operation::Subtract: return a - b;
        case Operation::Multiply: return a * b;
        case Operation::Divide: return (std::abs(b) > 1.0e-6f) ? (a / b) : 0.0f;
        case Operation::Min: return std::min(a, b);
        case Operation::Max: return std::max(a, b);
        case Operation::Abs: return std::abs(a);
        case Operation::Clamp: {
            if (mn > mx) std::swap(mn, mx);
            return std::clamp(a, mn, mx);
        }
    }
    return 0.0f;
}

void MathNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Muted (bypassed): pass input A through untouched.
    if (isBypassed()) {
        pullInput(PORT_A, buffer, numSamples);
        return;
    }

    BaseNode* sourceA = getInput(PORT_A);
    BaseNode* sourceB = getInput(PORT_B);
    const bool hasAudio = (sourceA != nullptr) || (sourceB != nullptr);

    // Hoist atomics out of the per-sample loop.
    const auto op = static_cast<Operation>(operation_.load(std::memory_order_relaxed));
    const float mn = clampMin_.load(std::memory_order_relaxed);
    const float mx = clampMax_.load(std::memory_order_relaxed);

    const int numChannels = std::min(buffer.getNumChannels(), 2);

    if (hasAudio) {
        inABuffer_.clear();
        inBBuffer_.clear();
        if (sourceA) sourceA->pullOutput(inABuffer_, numSamples);
        if (sourceB) sourceB->pullOutput(inBBuffer_, numSamples);

        for (int ch = 0; ch < numChannels; ++ch) {
            auto* dst = buffer.getWritePointer(ch);
            const auto* a = inABuffer_.getReadPointer(ch);
            const auto* b = inBBuffer_.getReadPointer(ch);
            for (int i = 0; i < numSamples; ++i) {
                dst[i] = applyOperation(op, a[i], b[i], mn, mx);
            }
        }
    } else {
        const float a = controlA_.load(std::memory_order_relaxed);
        const float b = controlB_.load(std::memory_order_relaxed);
        const float out = applyOperation(op, a, b, mn, mx);
        for (int ch = 0; ch < numChannels; ++ch) {
            auto* dst = buffer.getWritePointer(ch);
            for (int i = 0; i < numSamples; ++i) {
                dst[i] = out;
            }
        }
    }

    applyFade(buffer, numSamples);
}

void MathNode::setParameter(const std::string& name, float value) {
    if (name == "operation") {
        operation_.store(static_cast<int>(value), std::memory_order_relaxed);
    } else if (name == "control_a") {
        controlA_.store(value, std::memory_order_relaxed);
    } else if (name == "control_b") {
        controlB_.store(value, std::memory_order_relaxed);
    } else if (name == "clamp_min") {
        clampMin_.store(value, std::memory_order_relaxed);
    } else if (name == "clamp_max") {
        clampMax_.store(value, std::memory_order_relaxed);
    }
}

float MathNode::getParameter(const std::string& name) const {
    if (name == "operation") return static_cast<float>(operation_.load(std::memory_order_relaxed));
    if (name == "control_a") return controlA_.load(std::memory_order_relaxed);
    if (name == "control_b") return controlB_.load(std::memory_order_relaxed);
    if (name == "clamp_min") return clampMin_.load(std::memory_order_relaxed);
    if (name == "clamp_max") return clampMax_.load(std::memory_order_relaxed);
    return 0.0f;
}
