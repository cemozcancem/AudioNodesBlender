#include "OutputNode.h"

OutputNode::OutputNode(const std::string& id)
    : BaseNode(id)
{
    smoothedGain_.setCurrentAndTargetValue(1.0f);
}

void OutputNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    smoothedGain_.reset(sampleRate, 0.02); // 20ms smoothing
}

void OutputNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Pull input from connected node
    if (!isBypassed()) {
        pullInput(0, buffer, numSamples);
    }

    // Apply output gain
    const bool muted = muted_.load(std::memory_order_acquire);
    smoothedGain_.setTargetValue(muted ? 0.0f : gain_.load(std::memory_order_acquire));

    for (int sample = 0; sample < numSamples; ++sample) {
        const float gain = smoothedGain_.getNextValue();

        for (int channel = 0; channel < buffer.getNumChannels(); ++channel) {
            buffer.setSample(channel, sample, buffer.getSample(channel, sample) * gain);
        }
    }

    applyFade(buffer, numSamples);
}

void OutputNode::setParameter(const std::string& name, float value) {
    if (name == "gain") {
        gain_.store(value, std::memory_order_release);
    } else if (name == "mute") {
        muted_.store(value > 0.5f, std::memory_order_release);
    }
}

float OutputNode::getParameter(const std::string& name) const {
    if (name == "gain") {
        return gain_.load(std::memory_order_acquire);
    } else if (name == "mute") {
        return muted_.load(std::memory_order_acquire) ? 1.0f : 0.0f;
    }
    return 0.0f;
}
