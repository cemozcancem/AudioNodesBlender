#include "BaseNode.h"

uint64_t BaseNode::currentEpoch_ = 1;

BaseNode::BaseNode(const std::string& id)
    : id_(id)
{
    fadeGain_.setCurrentAndTargetValue(1.0f);
    inputs_.fill(nullptr);
    inputLanes_.fill(0);
}

void BaseNode::prepare(double sampleRate, int maxBlockSize) {
    sampleRate_ = sampleRate;
    maxBlockSize_ = maxBlockSize;

    // Configure fade smoother
    fadeGain_.reset(sampleRate, FADE_TIME_SECONDS);
    fadeGain_.setCurrentAndTargetValue(1.0f);

    // Pre-allocate the per-block output cache (stereo). All allocation
    // happens here, never in process()/pullOutput().
    cache_.setSize(2, maxBlockSize);
    cache_.clear();
}

void BaseNode::reset() {
    fadeGain_.setCurrentAndTargetValue(1.0f);
    fadingOut_.store(false, std::memory_order_release);
    fadeComplete_.store(false, std::memory_order_release);
    inputs_.fill(nullptr);
    inputLanes_.fill(0);
    cache_.clear();
    lastEpoch_ = 0;
}

void BaseNode::startFadeOut() {
    fadingOut_.store(true, std::memory_order_release);
    fadeGain_.setTargetValue(0.0f);
}

int BaseNode::resolvePortIndex(const std::string& portName) const {
    if (portName == "input" || portName == "audio_in" || portName.empty()) {
        return 0;
    }
    return -1;
}

int BaseNode::resolveOutputLane(const std::string& portName) const {
    (void)portName;
    return 0;
}

int BaseNode::parseIndex(const std::string& s, size_t pos, size_t endPos) {
    if (endPos == std::string::npos || endPos > s.size()) {
        endPos = s.size();
    }
    if (pos >= endPos) {
        return -1;
    }
    int value = 0;
    for (size_t i = pos; i < endPos; ++i) {
        const char c = s[i];
        if (c < '0' || c > '9') {
            return -1;
        }
        value = value * 10 + (c - '0');
        if (value > 1000000) {
            return -1;  // absurd index, refuse
        }
    }
    return value;
}

void BaseNode::setInput(int portIndex, BaseNode* node, int sourceLane) {
    if (portIndex < 0 || portIndex >= MAX_PORTS) {
        return;
    }
    inputs_[static_cast<size_t>(portIndex)] = node;
    inputLanes_[static_cast<size_t>(portIndex)] = sourceLane;
}

BaseNode* BaseNode::getInput(int portIndex) const {
    if (portIndex < 0 || portIndex >= MAX_PORTS) {
        return nullptr;
    }
    return inputs_[static_cast<size_t>(portIndex)];
}

int BaseNode::getInputLane(int portIndex) const {
    if (portIndex < 0 || portIndex >= MAX_PORTS) {
        return 0;
    }
    return inputLanes_[static_cast<size_t>(portIndex)];
}

void BaseNode::clearInput(int portIndex) {
    if (portIndex < 0 || portIndex >= MAX_PORTS) {
        return;
    }
    inputs_[static_cast<size_t>(portIndex)] = nullptr;
    inputLanes_[static_cast<size_t>(portIndex)] = 0;
}

void BaseNode::clearInputsReferencing(const BaseNode* node) {
    for (size_t i = 0; i < inputs_.size(); ++i) {
        if (inputs_[i] == node) {
            inputs_[i] = nullptr;
            inputLanes_[i] = 0;
        }
    }
}

void BaseNode::pullOutput(juce::AudioBuffer<float>& dest, int numSamples) {
    const int cacheChannels = cache_.getNumChannels();
    const int destChannels = dest.getNumChannels();
    const int copyChannels = juce::jmin(cacheChannels, destChannels);
    const int samples = juce::jmin(numSamples, cache_.getNumSamples());

    // Already rendered this block (diamond fan-out), or we hit ourselves
    // through a feedback cycle: hand out the cached output. In the cycle
    // case that is the previous block's output - a one-block feedback
    // delay instead of infinite recursion.
    if (lastEpoch_ != currentEpoch_) {
        // Mark BEFORE recursing so cycles terminate.
        lastEpoch_ = currentEpoch_;
        cache_.clear();
        process(cache_, samples);
    }

    for (int ch = 0; ch < copyChannels; ++ch) {
        dest.copyFrom(ch, 0, cache_, ch, 0, samples);
    }
    // Clear any extra destination channels so stale data never leaks out.
    for (int ch = copyChannels; ch < destChannels; ++ch) {
        dest.clear(ch, 0, samples);
    }
}

bool BaseNode::pullInput(int portIndex, juce::AudioBuffer<float>& dest, int numSamples) {
    BaseNode* source = getInput(portIndex);
    if (source == nullptr) {
        return false;
    }
    source->pullOutput(dest, numSamples);
    return true;
}

void BaseNode::applyFade(juce::AudioBuffer<float>& buffer, int numSamples) {
    if (!fadingOut_.load(std::memory_order_acquire)) {
        return;
    }

    for (int sample = 0; sample < numSamples; ++sample) {
        float gain = fadeGain_.getNextValue();

        for (int channel = 0; channel < buffer.getNumChannels(); ++channel) {
            buffer.setSample(channel, sample, buffer.getSample(channel, sample) * gain);
        }
    }

    // Check if fade is complete
    if (fadeGain_.getCurrentValue() < 0.001f) {
        fadeComplete_.store(true, std::memory_order_release);
    }
}
