#pragma once

#include "BaseNode.h"

// Gain Node - Volume control with dB or linear gain
class GainNode : public BaseNode {
public:
    explicit GainNode(const std::string& id);
    
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;
    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;
    
private:
    juce::dsp::Gain<float> gain_;
    
    std::atomic<float> gainDb_{0.0f};
    std::atomic<bool> muted_{false};
    
    juce::SmoothedValue<float> smoothedGain_;
};

