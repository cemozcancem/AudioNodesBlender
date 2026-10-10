#include "OscillatorNode.h"
#include <cmath>

OscillatorNode::OscillatorNode(const std::string& id)
    : BaseNode(id)
{
    smoothedFrequency_.setCurrentAndTargetValue(440.0f);
    smoothedGain_.setCurrentAndTargetValue(0.5f);
}

void OscillatorNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);

    smoothedFrequency_.reset(sampleRate, 0.01); // 10ms frequency smoothing
    smoothedGain_.reset(sampleRate, 0.01);
    smoothedFrequency_.setCurrentAndTargetValue(frequency_.load(std::memory_order_acquire));
    smoothedGain_.setCurrentAndTargetValue(gainValue_.load(std::memory_order_acquire));
}

void OscillatorNode::reset() {
    BaseNode::reset();
    phase_ = 0.0;
}

void OscillatorNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    if (isBypassed()) {
        buffer.clear();
        return;
    }

    smoothedFrequency_.setTargetValue(frequency_.load(std::memory_order_acquire));
    smoothedGain_.setTargetValue(gainValue_.load(std::memory_order_acquire));

    const int waveform = waveformType_.load(std::memory_order_relaxed);
    const double invSampleRate = 1.0 / sampleRate_;
    const int numChannels = buffer.getNumChannels();

    for (int sample = 0; sample < numSamples; ++sample) {
        const float freq = smoothedFrequency_.getNextValue();
        const float gain = smoothedGain_.getNextValue();

        float value;
        const auto p = static_cast<float>(phase_);
        switch (static_cast<Waveform>(waveform)) {
            case Waveform::Saw:
                value = 2.0f * p - 1.0f;
                break;
            case Waveform::Square:
                value = (p < 0.5f) ? 1.0f : -1.0f;
                break;
            case Waveform::Triangle:
                value = 2.0f * std::abs(2.0f * p - 1.0f) - 1.0f;
                break;
            case Waveform::Sine:
            default:
                value = std::sin(juce::MathConstants<float>::twoPi * p);
                break;
        }

        value *= gain;

        for (int channel = 0; channel < numChannels; ++channel) {
            buffer.setSample(channel, sample, value);
        }

        phase_ += static_cast<double>(freq) * invSampleRate;
        if (phase_ >= 1.0) {
            phase_ -= 1.0;
        }
    }

    applyFade(buffer, numSamples);
}

void OscillatorNode::setParameter(const std::string& name, float value) {
    if (name == "frequency") {
        frequency_.store(std::clamp(value, 20.0f, 20000.0f), std::memory_order_release);
    } else if (name == "gain") {
        gainValue_.store(std::clamp(value, 0.0f, 1.0f), std::memory_order_release);
    } else if (name == "waveform") {
        waveformType_.store(static_cast<int>(value), std::memory_order_release);
    }
}

float OscillatorNode::getParameter(const std::string& name) const {
    if (name == "frequency") {
        return frequency_.load(std::memory_order_acquire);
    } else if (name == "gain") {
        return gainValue_.load(std::memory_order_acquire);
    } else if (name == "waveform") {
        return static_cast<float>(waveformType_.load(std::memory_order_acquire));
    }
    return 0.0f;
}
