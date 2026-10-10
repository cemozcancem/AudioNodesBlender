#pragma once

#include "BaseNode.h"

// Delay Node - Time-based delay effect
class DelayNode : public BaseNode {
public:
    explicit DelayNode(const std::string& id);
    
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;
    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;
    
private:
    juce::dsp::DelayLine<float, juce::dsp::DelayLineInterpolationTypes::Linear> delayLine_{96000}; // 2 sec max at 48kHz
    
    std::atomic<float> delayTimeMs_{250.0f};
    std::atomic<float> feedback_{0.3f};
    std::atomic<float> mix_{0.5f};
    
    juce::SmoothedValue<float> smoothedDelayTime_;
    juce::SmoothedValue<float> smoothedFeedback_;
    juce::SmoothedValue<float> smoothedMix_;
    
    // Feedback buffers (per channel)
    std::array<float, 2> feedbackSample_{0.0f, 0.0f};
};

