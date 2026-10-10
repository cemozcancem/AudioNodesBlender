#pragma once

#include "BaseNode.h"
#include "common/LockFreeQueue.h"
#include <juce_audio_formats/juce_audio_formats.h>
#include <array>
#include <atomic>
#include <string>

/**
 * DrumSamplerNode - Multi-sample drum machine with 8 slots.
 * Each slot can load a different sample and has independent settings.
 *
 * Sample handoff is lock-free (same scheme as SamplerNode): the command
 * thread loads into a fresh SampleData and publishes it via an atomic
 * pointer per slot; the audio thread swaps it in and retires the old
 * buffer through a queue for command-thread destruction.
 *
 * Input ports: 0 = audio input, 1..8 = "trig_1".."trig_8".
 */
class DrumSamplerNode : public BaseNode {
public:
    static constexpr int NUM_SLOTS = 8;
    static constexpr int PORT_AUDIO = 0;
    static constexpr int PORT_TRIG_BASE = 1;   // trig_N -> port N

    explicit DrumSamplerNode(const std::string& id);
    ~DrumSamplerNode() override;

    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;

    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    int resolvePortIndex(const std::string& portName) const override;

    // Load a sample into a slot. Command thread only.
    bool loadSample(int slot, const std::string& filePath);

    // Trigger a slot (thread-safe: atomics only)
    void triggerSlot(int slot);
    void stopSlot(int slot);

private:
    struct SampleData {
        juce::AudioBuffer<float> buffer;
        double sampleRate = 44100.0;
        int length = 0;
    };

    struct SampleSlot {
        // Lock-free handoff
        std::atomic<SampleData*> pending{nullptr};   // command -> audio
        SampleData* current = nullptr;               // audio-thread owned

        std::atomic<bool> loaded{false};
        std::atomic<bool> playing{false};
        std::atomic<double> position{0.0};
        std::atomic<bool> pendingTrigger{false};
        std::atomic<float> volume{1.0f};
        std::atomic<float> pitch{0.0f};
        std::atomic<float> pan{0.0f};                // -1 to 1
        std::atomic<float> lastTrigger{0.0f};        // command-thread edge detect
        float lastTriggerControl = 0.0f;             // audio-thread edge detect
        std::string filePath;                        // command thread only
    };

    std::array<SampleSlot, NUM_SLOTS> slots_;
    LockFreeQueue<SampleData*, 32> retiredSamples_;  // audio -> command

    std::atomic<float> masterVolume_{1.0f};
    double sampleRate_{44100.0};

    void processSlot(int slot, juce::AudioBuffer<float>& buffer, int numSamples);
    void adoptPendingSamples();     // audio thread
    void drainRetiredSamples();     // command thread
    double getPitchRatio(int slot, double sourceSampleRate) const;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(DrumSamplerNode)
};
