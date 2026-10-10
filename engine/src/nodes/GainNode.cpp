#include "GainNode.h"

GainNode::GainNode(const std::string& id)
    : BaseNode(id)
{
    smoothedGain_.setCurrentAndTargetValue(1.0f);
}

void GainNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);

    juce::dsp::ProcessSpec spec;
    spec.sampleRate = sampleRate;
    spec.maximumBlockSize = static_cast<juce::uint32>(maxBlockSize);
    spec.numChannels = 2;

    gain_.prepare(spec);
    smoothedGain_.reset(sampleRate, 0.02); // 20ms smoothing
}

void GainNode::reset() {
    BaseNode::reset();
    gain_.reset();
}

void GainNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Pull input from connected node
    pullInput(0, buffer, numSamples);

    if (isBypassed()) {
        return;
    }

    // Calculate linear gain from dB
    float targetGain = muted_.load(std::memory_order_acquire)
        ? 0.0f
        : juce::Decibels::decibelsToGain(gainDb_.load(std::memory_order_acquire));

    smoothedGain_.setTargetValue(targetGain);

    // Apply gain sample by sample for smooth changes
    for (int sample = 0; sample < numSamples; ++sample) {
        float currentGain = smoothedGain_.getNextValue();

        for (int channel = 0; channel < buffer.getNumChannels(); ++channel) {
            buffer.setSample(channel, sample, buffer.getSample(channel, sample) * currentGain);
        }
    }

    applyFade(buffer, numSamples);
}

void GainNode::setParameter(const std::string& name, float value) {
    if (name == "gain_db" || name == "gain") {
        gainDb_.store(std::clamp(value, -60.0f, 24.0f), std::memory_order_release);
    } else if (name == "mute") {
        muted_.store(value > 0.5f, std::memory_order_release);
    }
}

float GainNode::getParameter(const std::string& name) const {
    if (name == "gain_db" || name == "gain") {
        return gainDb_.load(std::memory_order_acquire);
    } else if (name == "mute") {
        return muted_.load(std::memory_order_acquire) ? 1.0f : 0.0f;
    }
    return 0.0f;
}
