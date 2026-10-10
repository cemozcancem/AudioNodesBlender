#pragma once

#include "BaseNode.h"

// Filter Node - State Variable TPT Filter
class FilterNode : public BaseNode {
public:
    explicit FilterNode(const std::string& id);

    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;
    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    enum class FilterType {
        LowPass = 0,
        HighPass,
        BandPass
    };

private:
    juce::dsp::StateVariableTPTFilter<float> filter_;

    std::atomic<float> cutoff_{1000.0f};
    std::atomic<float> resonance_{0.707f};
    std::atomic<int> filterType_{0}; // LowPass by default

    juce::SmoothedValue<float> smoothedCutoff_;
    juce::SmoothedValue<float> smoothedResonance_;

    // Audio-thread copy of the applied type so setType() (which resets the
    // filter state) only runs when the type actually changed.
    int appliedType_ = 0;

    // Coefficients are recomputed once per sub-block instead of per sample.
    static constexpr int PARAM_UPDATE_INTERVAL = 32;
};
