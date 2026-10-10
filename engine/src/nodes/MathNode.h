#pragma once

#include "BaseNode.h"
#include <atomic>

/**
 * MathNode - Adaptive math operations for audio and control values.
 * Audio inputs: port 0 = "audio_a", port 1 = "audio_b".
 */
class MathNode : public BaseNode {
public:
    static constexpr int PORT_A = 0;
    static constexpr int PORT_B = 1;

    enum class Operation {
        Add = 0,
        Subtract = 1,
        Multiply = 2,
        Divide = 3,
        Min = 4,
        Max = 5,
        Abs = 6,
        Clamp = 7
    };

    explicit MathNode(const std::string& id);
    ~MathNode() override = default;

    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;

    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    int resolvePortIndex(const std::string& portName) const override;

private:
    std::atomic<int> operation_{0};
    std::atomic<float> controlA_{0.0f};
    std::atomic<float> controlB_{0.0f};
    std::atomic<float> clampMin_{0.0f};
    std::atomic<float> clampMax_{1.0f};

    juce::AudioBuffer<float> inABuffer_;
    juce::AudioBuffer<float> inBBuffer_;

    static float applyOperation(Operation op, float a, float b, float mn, float mx);

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(MathNode)
};
