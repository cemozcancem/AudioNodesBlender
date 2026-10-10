#include "InputNode.h"

InputNode::InputNode(const std::string& id)
    : BaseNode(id)
{
    smoothedGain_.setCurrentAndTargetValue(1.0f);
}

void InputNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    smoothedGain_.reset(sampleRate, 0.02); // 20ms smoothing
}

void InputNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    if (isBypassed() || inputBufferPtr_ == nullptr) {
        buffer.clear();
        return;
    }

    // Smoothed target set on the audio thread (SmoothedValue is not
    // thread-safe against setParameter on the command thread).
    smoothedGain_.setTargetValue(gain_.load(std::memory_order_acquire));

    const int inputChannels = inputBufferPtr_->getNumChannels();
    const int outputChannels = buffer.getNumChannels();
    
    const int leftChan = leftChannel_.load(std::memory_order_relaxed);
    const bool mono = channelMode_.load(std::memory_order_relaxed) == 1;
    // Mono: one device channel feeds both outputs (a single mic/DI on a
    // multi-channel interface). Stereo: independent L/R channel picks.
    const int rightChan = mono ? leftChan
                               : rightChannel_.load(std::memory_order_relaxed);

    // Copy from input device buffer to output
    for (int sample = 0; sample < numSamples; ++sample) {
        float gain = smoothedGain_.getNextValue();

        if (outputChannels >= 1 && leftChan < inputChannels) {
            buffer.setSample(0, sample, inputBufferPtr_->getSample(leftChan, sample) * gain);
        }

        if (outputChannels >= 2 && rightChan < inputChannels) {
            buffer.setSample(1, sample, inputBufferPtr_->getSample(rightChan, sample) * gain);
        }
    }
    
    applyFade(buffer, numSamples);
}

void InputNode::setInputBuffer(const juce::AudioBuffer<float>& inputBuffer) {
    inputBufferPtr_ = &inputBuffer;
}

void InputNode::setParameter(const std::string& name, float value) {
    if (name == "gain") {
        gain_.store(value, std::memory_order_release);
    } else if (name == "left_channel") {
        leftChannel_.store(static_cast<int>(value), std::memory_order_release);
    } else if (name == "right_channel") {
        rightChannel_.store(static_cast<int>(value), std::memory_order_release);
    } else if (name == "channel_mode") {
        channelMode_.store(static_cast<int>(value), std::memory_order_release);
    }
}

float InputNode::getParameter(const std::string& name) const {
    if (name == "gain") {
        return gain_.load(std::memory_order_acquire);
    } else if (name == "left_channel") {
        return static_cast<float>(leftChannel_.load(std::memory_order_acquire));
    } else if (name == "right_channel") {
        return static_cast<float>(rightChannel_.load(std::memory_order_acquire));
    } else if (name == "channel_mode") {
        return static_cast<float>(channelMode_.load(std::memory_order_acquire));
    }
    return 0.0f;
}

