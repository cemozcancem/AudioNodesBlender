#pragma once

#include "BaseNode.h"
#include <array>
#include <atomic>

/**
 * MixerNode - 8-channel stereo mixer.
 * Each channel has independent level/pan/mute controls.
 * Channel inputs are ports 0..7 ("input_1".."input_8").
 */
class MixerNode : public BaseNode {
public:
    static constexpr int NUM_CHANNELS = 8;

    explicit MixerNode(const std::string& id);
    ~MixerNode() override = default;

    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;

    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    int resolvePortIndex(const std::string& portName) const override;

private:
    std::array<std::atomic<float>, NUM_CHANNELS> channelLevels_;
    std::array<std::atomic<float>, NUM_CHANNELS> channelPans_;
    std::array<std::atomic<bool>, NUM_CHANNELS> channelMutes_;
    std::atomic<float> masterLevel_{1.0f};

    juce::AudioBuffer<float> scratchBuffer_;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(MixerNode)
};
