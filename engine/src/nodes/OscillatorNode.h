#pragma once

#include "BaseNode.h"

// Oscillator Node - generates waveforms.
//
// Uses a hand-rolled phase accumulator instead of juce::dsp::Oscillator:
// juce's initialise() rebuilds a lookup table on the heap, which raced the
// audio thread when the waveform was switched from the command thread.
// Here the waveform is just an atomic int read per block.
class OscillatorNode : public BaseNode {
public:
    explicit OscillatorNode(const std::string& id);

    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;
    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    enum class Waveform {
        Sine = 0,
        Saw,
        Square,
        Triangle
    };

private:
    std::atomic<float> frequency_{440.0f};
    std::atomic<float> gainValue_{0.5f};
    std::atomic<int> waveformType_{0}; // Sine by default

    juce::SmoothedValue<float> smoothedFrequency_;
    juce::SmoothedValue<float> smoothedGain_;

    // Audio-thread owned phase accumulator, range [0, 1).
    double phase_ = 0.0;
};
