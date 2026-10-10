#pragma once

#include "BaseNode.h"
#include "common/LockFreeQueue.h"
#include <juce_audio_formats/juce_audio_formats.h>
#include <atomic>
#include <string>

/**
 * SamplerNode - Plays audio samples triggered by input.
 * Supports pitch shifting, looping, and start/end points.
 *
 * Sample loading is completely lock-free from the audio thread's point of
 * view: the command thread reads the file into a fresh SampleData and
 * publishes it through an atomic pointer. The audio thread swaps it in at
 * block start and returns the old buffer through a queue so it is freed on
 * the command thread. No mutex, no disk I/O, no allocation in process().
 */
class SamplerNode : public BaseNode {
public:
    // Input ports
    static constexpr int PORT_AUDIO = 0;
    static constexpr int PORT_TRIGGER = 1;

    explicit SamplerNode(const std::string& id);
    ~SamplerNode() override;

    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;

    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    int resolvePortIndex(const std::string& portName) const override;

    // Load a sample file. Command thread only - never call while another
    // load for this node is in flight (the ZMQ server is single-threaded,
    // which guarantees this).
    bool loadSample(const std::string& filePath);
    bool isSampleLoaded() const { return sampleLoaded_.load(); }

    // Playback control (thread-safe: atomics only)
    void trigger() { pendingTrigger_.store(true, std::memory_order_release); }
    void stop() { isPlaying_.store(false); }

private:
    struct SampleData {
        juce::AudioBuffer<float> buffer;
        double sampleRate = 44100.0;
        int length = 0;
    };

    // Lock-free sample handoff
    std::atomic<SampleData*> pendingSample_{nullptr};   // command -> audio
    LockFreeQueue<SampleData*, 8> retiredSamples_;      // audio -> command
    SampleData* currentSample_ = nullptr;               // audio-thread owned

    std::atomic<bool> sampleLoaded_{false};
    std::atomic<int> currentLength_{0};   // mirror for getParameter()
    std::string currentFilePath_;          // command thread only

    // Playback state
    std::atomic<bool> isPlaying_{false};
    std::atomic<double> playPosition_{0.0};
    std::atomic<bool> pendingTrigger_{false};
    std::atomic<float> lastTriggerValue_{0.0f};  // command-thread edge detect

    // Parameters
    std::atomic<float> volume_{1.0f};
    std::atomic<float> pitch_{0.0f};        // Semitones (-24 to +24)
    std::atomic<float> startPoint_{0.0f};   // 0-1
    std::atomic<float> endPoint_{1.0f};     // 0-1
    std::atomic<bool> loop_{false};
    std::atomic<bool> oneShot_{true};       // Restart on retrigger

    // Audio-thread edge detection for the trigger control input
    float lastTriggerControl_ = 0.0f;

    double sampleRate_{44100.0};

    double getPitchRatio(double sourceSampleRate) const;
    void adoptPendingSample();      // audio thread
    void drainRetiredSamples();     // command thread

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(SamplerNode)
};
