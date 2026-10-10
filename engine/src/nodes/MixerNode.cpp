#include "MixerNode.h"
#include <cmath>

MixerNode::MixerNode(const std::string& id)
    : BaseNode(id)
{
    for (int i = 0; i < NUM_CHANNELS; ++i) {
        channelLevels_[i].store(1.0f);
        channelPans_[i].store(0.0f);
        channelMutes_[i].store(false);
    }
}

void MixerNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    scratchBuffer_.setSize(2, maxBlockSize);
    scratchBuffer_.clear();
}

void MixerNode::reset() {
    BaseNode::reset();
    scratchBuffer_.clear();
}

int MixerNode::resolvePortIndex(const std::string& portName) const {
    // "input_1".."input_8" -> 0..7
    if (portName.rfind("input_", 0) == 0) {
        const int n = parseIndex(portName, 6);
        if (n >= 1 && n <= NUM_CHANNELS) {
            return n - 1;
        }
        return -1;
    }
    if (portName == "input") {
        return 0;
    }
    // chN_level / chN_pan control ports are driven via set_param
    return -1;
}

void MixerNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Muted (bypassed): a mixer has no single pass-through input - silence.
    if (isBypassed()) {
        return;  // buffer arrives cleared from pullOutput()
    }

    // `buffer` arrives cleared from pullOutput().
    const int outputChannels = std::min(buffer.getNumChannels(), 2);

    for (int chIdx = 0; chIdx < NUM_CHANNELS; ++chIdx) {
        BaseNode* source = getInput(chIdx);
        if (source == nullptr) {
            continue;
        }
        if (channelMutes_[chIdx].load(std::memory_order_relaxed)) {
            continue;
        }

        // Pre-allocated stereo scratch; pullOutput clears what it writes.
        source->pullOutput(scratchBuffer_, numSamples);

        const float level = channelLevels_[chIdx].load(std::memory_order_relaxed);
        const float pan = channelPans_[chIdx].load(std::memory_order_relaxed);
        const float panAngle = (pan + 1.0f) * 0.25f * juce::MathConstants<float>::pi;
        const float leftGain = std::cos(panAngle) * level;
        const float rightGain = std::sin(panAngle) * level;

        for (int sample = 0; sample < numSamples; ++sample) {
            if (outputChannels > 0) {
                buffer.addSample(0, sample, scratchBuffer_.getSample(0, sample) * leftGain);
            }
            if (outputChannels > 1) {
                buffer.addSample(1, sample, scratchBuffer_.getSample(1, sample) * rightGain);
            }
        }
    }

    buffer.applyGain(masterLevel_.load(std::memory_order_relaxed));
    applyFade(buffer, numSamples);
}

void MixerNode::setParameter(const std::string& name, float value) {
    if (name == "master_level") {
        masterLevel_.store(std::clamp(value, 0.0f, 2.0f), std::memory_order_relaxed);
        return;
    }

    // chN_level / chN_pan / chN_mute
    if (name.rfind("ch", 0) != 0) {
        return;
    }
    const auto underscore = name.find('_');
    if (underscore == std::string::npos) {
        return;
    }

    const int channel = parseIndex(name, 2, underscore) - 1;
    if (channel < 0 || channel >= NUM_CHANNELS) {
        return;
    }

    const std::string param = name.substr(underscore + 1);
    if (param == "level") {
        channelLevels_[channel].store(std::clamp(value, 0.0f, 2.0f), std::memory_order_relaxed);
    } else if (param == "pan") {
        channelPans_[channel].store(std::clamp(value, -1.0f, 1.0f), std::memory_order_relaxed);
    } else if (param == "mute") {
        channelMutes_[channel].store(value > 0.5f, std::memory_order_relaxed);
    }
}

float MixerNode::getParameter(const std::string& name) const {
    if (name == "master_level") {
        return masterLevel_.load(std::memory_order_relaxed);
    }

    if (name.rfind("ch", 0) != 0) {
        return 0.0f;
    }
    const auto underscore = name.find('_');
    if (underscore == std::string::npos) {
        return 0.0f;
    }

    const int channel = parseIndex(name, 2, underscore) - 1;
    if (channel < 0 || channel >= NUM_CHANNELS) {
        return 0.0f;
    }

    const std::string param = name.substr(underscore + 1);
    if (param == "level") {
        return channelLevels_[channel].load(std::memory_order_relaxed);
    }
    if (param == "pan") {
        return channelPans_[channel].load(std::memory_order_relaxed);
    }
    if (param == "mute") {
        return channelMutes_[channel].load(std::memory_order_relaxed) ? 1.0f : 0.0f;
    }
    return 0.0f;
}
