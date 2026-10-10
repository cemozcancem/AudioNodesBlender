#pragma once

#include "BaseNode.h"
#include <juce_audio_basics/juce_audio_basics.h>
#include <atomic>

/**
 * ADSRNode - ADSR envelope generator using juce::ADSR.
 * Can be used to shape amplitude or as a control signal.
 *
 * juce::ADSR is not thread-safe, so parameter changes and gate edges from
 * the command thread are staged in atomics and applied inside process()
 * on the audio thread.
 */
class ADSRNode : public BaseNode {
public:
    // Input ports
    static constexpr int PORT_AUDIO = 0;
    static constexpr int PORT_GATE = 1;

    explicit ADSRNode(const std::string& id);
    ~ADSRNode() override = default;

    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;

    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    int resolvePortIndex(const std::string& portName) const override;

    // Envelope value as control output (for downstream control consumers)
    float getControlOutput(int /*lane*/ = 0) const override { return currentEnvelope_.load(); }

    // Get current envelope value (for output socket / shared memory)
    float getEnvelopeValue() const { return currentEnvelope_.load(); }

private:
    juce::ADSR adsr_;
    juce::ADSR::Parameters adsrParams_;

    // Parameters (in seconds for A/D/R, 0-1 for S)
    std::atomic<float> attack_{0.01f};   // 10ms default
    std::atomic<float> decay_{0.1f};     // 100ms default
    std::atomic<float> sustain_{0.7f};   // 70% level
    std::atomic<float> release_{0.3f};   // 300ms default
    std::atomic<bool> paramsDirty_{true};

    // Gate state. pendingGate_: -1 = none, 0 = note off, 1 = note on.
    // Consumed by the audio thread.
    std::atomic<int> pendingGate_{-1};
    std::atomic<bool> gateOn_{false};
    std::atomic<float> lastGateValue_{0.0f};   // command-thread edge detection
    std::atomic<float> currentEnvelope_{0.0f};

    // Mode: true = apply to audio, false = control signal only
    std::atomic<bool> applyToAudio_{true};

    // Audio-thread edge detection for a connected gate control input.
    float lastGateControl_ = 0.0f;

    double sampleRate_{44100.0};

    void updateADSRParams();  // audio thread only

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(ADSRNode)
};
