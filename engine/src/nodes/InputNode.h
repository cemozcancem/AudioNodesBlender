#pragma once

#include "BaseNode.h"

// Audio Input Node - receives audio from the device input
class InputNode : public BaseNode {
public:
    explicit InputNode(const std::string& id);
    
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;
    void prepare(double sampleRate, int maxBlockSize) override;
    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;
    
    // Set the input buffer from AudioDeviceManager callback
    void setInputBuffer(const juce::AudioBuffer<float>& inputBuffer);
    
private:
    std::atomic<float> gain_{1.0f};
    juce::SmoothedValue<float> smoothedGain_;
    
    // Input buffer pointer - set by AudioEngine before processing
    const juce::AudioBuffer<float>* inputBufferPtr_ = nullptr;
    
    // Channel selection
    std::atomic<int> leftChannel_{0};
    std::atomic<int> rightChannel_{1};

    // 0 = stereo (leftChannel_ -> L, rightChannel_ -> R)
    // 1 = mono   (leftChannel_ -> both L and R)
    std::atomic<int> channelMode_{0};
};

