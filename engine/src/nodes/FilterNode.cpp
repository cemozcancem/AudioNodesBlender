#include "FilterNode.h"

FilterNode::FilterNode(const std::string& id)
    : BaseNode(id)
{
    smoothedCutoff_.setCurrentAndTargetValue(1000.0f);
    smoothedResonance_.setCurrentAndTargetValue(0.707f);
}

void FilterNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);

    juce::dsp::ProcessSpec spec;
    spec.sampleRate = sampleRate;
    spec.maximumBlockSize = static_cast<juce::uint32>(maxBlockSize);
    spec.numChannels = 2;

    filter_.prepare(spec);
    filter_.setType(juce::dsp::StateVariableTPTFilterType::lowpass);
    appliedType_ = 0;

    smoothedCutoff_.reset(sampleRate, 0.02); // 20ms smoothing
    smoothedResonance_.reset(sampleRate, 0.02);
}

void FilterNode::reset() {
    BaseNode::reset();
    filter_.reset();
}

void FilterNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Pull input from connected node
    pullInput(0, buffer, numSamples);

    if (isBypassed()) {
        return;
    }

    // Update filter type only when it actually changed (setType resets the
    // filter state, and re-running it every block is wasted work).
    const int type = filterType_.load(std::memory_order_acquire);
    if (type != appliedType_) {
        appliedType_ = type;
        switch (type) {
            case 0: filter_.setType(juce::dsp::StateVariableTPTFilterType::lowpass); break;
            case 1: filter_.setType(juce::dsp::StateVariableTPTFilterType::highpass); break;
            case 2: filter_.setType(juce::dsp::StateVariableTPTFilterType::bandpass); break;
            default: break;
        }
    }

    smoothedCutoff_.setTargetValue(cutoff_.load(std::memory_order_acquire));
    smoothedResonance_.setTargetValue(resonance_.load(std::memory_order_acquire));

    const int numChannels = std::min(buffer.getNumChannels(), 2);

    // Recompute coefficients once per sub-block (~0.7ms at 48kHz) instead of
    // per sample - inaudible difference, large CPU saving.
    for (int start = 0; start < numSamples; start += PARAM_UPDATE_INTERVAL) {
        const int len = std::min(PARAM_UPDATE_INTERVAL, numSamples - start);

        const float currentCutoff = smoothedCutoff_.getNextValue();
        const float currentResonance = smoothedResonance_.getNextValue();
        if (len > 1) {
            smoothedCutoff_.skip(len - 1);
            smoothedResonance_.skip(len - 1);
        }

        filter_.setCutoffFrequency(currentCutoff);
        filter_.setResonance(currentResonance);

        for (int sample = start; sample < start + len; ++sample) {
            for (int channel = 0; channel < numChannels; ++channel) {
                const float inputSample = buffer.getSample(channel, sample);
                buffer.setSample(channel, sample, filter_.processSample(channel, inputSample));
            }
        }
    }

    applyFade(buffer, numSamples);
}

void FilterNode::setParameter(const std::string& name, float value) {
    if (name == "cutoff") {
        cutoff_.store(std::clamp(value, 20.0f, 20000.0f), std::memory_order_release);
    } else if (name == "resonance") {
        resonance_.store(std::clamp(value, 0.1f, 10.0f), std::memory_order_release);
    } else if (name == "type") {
        filterType_.store(static_cast<int>(value), std::memory_order_release);
    }
}

float FilterNode::getParameter(const std::string& name) const {
    if (name == "cutoff") {
        return cutoff_.load(std::memory_order_acquire);
    } else if (name == "resonance") {
        return resonance_.load(std::memory_order_acquire);
    } else if (name == "type") {
        return static_cast<float>(filterType_.load(std::memory_order_acquire));
    }
    return 0.0f;
}
