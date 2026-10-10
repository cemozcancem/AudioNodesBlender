#pragma once

#include "BaseNode.h"

// Audio Output Node - sends audio to the device output
class OutputNode : public BaseNode {
public:
    explicit OutputNode(const std::string& id);

    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;
    void prepare(double sampleRate, int maxBlockSize) override;
    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

private:
    std::atomic<float> gain_{1.0f};
    juce::SmoothedValue<float> smoothedGain_;

    // Mute state
    std::atomic<bool> muted_{false};
};
