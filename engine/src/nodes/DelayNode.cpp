#include "DelayNode.h"

DelayNode::DelayNode(const std::string& id)
    : BaseNode(id)
{
    smoothedDelayTime_.setCurrentAndTargetValue(250.0f);
    smoothedFeedback_.setCurrentAndTargetValue(0.3f);
    smoothedMix_.setCurrentAndTargetValue(0.5f);
}

void DelayNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);

    juce::dsp::ProcessSpec spec;
    spec.sampleRate = sampleRate;
    spec.maximumBlockSize = static_cast<juce::uint32>(maxBlockSize);
    spec.numChannels = 2;

    // Set max delay to 2 seconds
    delayLine_.setMaximumDelayInSamples(static_cast<int>(sampleRate * 2.0));
    delayLine_.prepare(spec);

    smoothedDelayTime_.reset(sampleRate, 0.05); // 50ms smoothing for delay time
    smoothedFeedback_.reset(sampleRate, 0.02);
    smoothedMix_.reset(sampleRate, 0.02);

    feedbackSample_.fill(0.0f);
}

void DelayNode::reset() {
    BaseNode::reset();
    delayLine_.reset();
    feedbackSample_.fill(0.0f);
}

void DelayNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Pull input from connected node
    pullInput(0, buffer, numSamples);

    if (isBypassed()) {
        return;
    }

    // Update smoothed parameters
    smoothedDelayTime_.setTargetValue(delayTimeMs_.load(std::memory_order_acquire));
    smoothedFeedback_.setTargetValue(feedback_.load(std::memory_order_acquire));
    smoothedMix_.setTargetValue(mix_.load(std::memory_order_acquire));

    const int numChannels = std::min(buffer.getNumChannels(), 2);

    for (int sample = 0; sample < numSamples; ++sample) {
        float currentDelayMs = smoothedDelayTime_.getNextValue();
        float currentFeedback = smoothedFeedback_.getNextValue();
        float currentMix = smoothedMix_.getNextValue();

        // Convert ms to samples
        float delaySamples = currentDelayMs * static_cast<float>(sampleRate_) / 1000.0f;

        for (int channel = 0; channel < numChannels; ++channel) {
            float inputSample = buffer.getSample(channel, sample);

            // Read from delay line
            float delayedSample = delayLine_.popSample(channel, delaySamples);

            // Write to delay line with feedback
            float toDelay = inputSample + (delayedSample * currentFeedback);
            delayLine_.pushSample(channel, toDelay);

            // Mix dry and wet
            float outputSample = inputSample * (1.0f - currentMix) + delayedSample * currentMix;
            buffer.setSample(channel, sample, outputSample);
        }
    }

    applyFade(buffer, numSamples);
}

void DelayNode::setParameter(const std::string& name, float value) {
    if (name == "time" || name == "delay_time") {
        delayTimeMs_.store(std::clamp(value, 1.0f, 2000.0f), std::memory_order_release);
    } else if (name == "feedback") {
        feedback_.store(std::clamp(value, 0.0f, 0.95f), std::memory_order_release);
    } else if (name == "mix") {
        mix_.store(std::clamp(value, 0.0f, 1.0f), std::memory_order_release);
    }
}

float DelayNode::getParameter(const std::string& name) const {
    if (name == "time" || name == "delay_time") {
        return delayTimeMs_.load(std::memory_order_acquire);
    } else if (name == "feedback") {
        return feedback_.load(std::memory_order_acquire);
    } else if (name == "mix") {
        return mix_.load(std::memory_order_acquire);
    }
    return 0.0f;
}
