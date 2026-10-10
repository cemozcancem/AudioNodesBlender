#pragma once

#include "BaseNode.h"
#include <array>
#include <atomic>
#include <cstdint>

/**
 * SequencerNode - Step sequencer with configurable time signature.
 * Supports up to 64 steps with variable time signatures.
 *
 * Timing state (samplesPerStep_, counters, gates) is audio-thread owned.
 * Parameter changes from the command thread set atomics plus a dirty flag
 * that process() consumes, so nothing races.
 */
class SequencerNode : public BaseNode {
public:
    static constexpr int MAX_STEPS = 64;
    static constexpr int MAX_LANES = 8;
    static constexpr int PORT_PLAY = 0;
    static constexpr int PORT_STOP = 1;

    explicit SequencerNode(const std::string& id);
    ~SequencerNode() override = default;

    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;

    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    int resolvePortIndex(const std::string& portName) const override;
    int resolveOutputLane(const std::string& portName) const override;

    // Sequencer control (thread-safe: atomics only)
    void play() { pendingTransport_.store(1, std::memory_order_release); }
    void stop() { pendingTransport_.store(0, std::memory_order_release); }
    void setStep(int lane, int step, bool active);
    bool getStep(int lane, int step) const;
    void setPattern(int lane, uint64_t pattern);
    uint64_t getPattern(int lane) const;

    // Control output - trigger value for the given lane (0-7)
    float getControlOutput(int lane = 0) const override { return getTriggerValue(lane); }

    // State access
    int getCurrentStep() const { return currentStep_.load(); }
    bool isPlaying() const { return isPlaying_.load(); }
    bool isTriggerActive(int lane) const;
    float getTriggerValue(int lane) const;

    // For shared memory updates
    uint8_t getCurrentStepForShm() const { return static_cast<uint8_t>(currentStep_.load()); }
    uint8_t getIsPlayingForShm() const { return isPlaying_.load() ? 1 : 0; }
    uint8_t getTriggerActiveForShm() const { return isTriggerActive(0) ? 1 : 0; }
    float getBpmForShm() const { return bpm_.load(); }
    uint32_t getStepCountForShm() const { return static_cast<uint32_t>(stepCount_.load()); }
    uint64_t getStepPatternForShm() const { return stepPatterns_[0].load(); }

private:
    // Timing parameters (written by command thread, read by audio thread)
    //
    // The time signature is REAL timing, not decoration. Both halves of it
    // do work:
    //   beatUnit_    - what note value counts as one beat (4, 8, 16), which
    //                  sets how long a beat lasts against the quarter-note
    //                  BPM.
    //   beatsPerBar_ - how many of those beats make a bar, which together
    //                  with stepsPerBeat_ and bars_ sets the LOOP LENGTH.
    // beatsPerBar_ used to be stored and never read, so 7/8 looped over 16
    // steps exactly like 4/4 and the meter only existed in the UI.
    std::atomic<float> bpm_{120.0f};
    std::atomic<int> stepCount_{16};        // derived: beats * steps * bars
    std::atomic<int> beatsPerBar_{4};       // Time signature numerator
    std::atomic<int> beatUnit_{4};          // Time signature denominator
    std::atomic<int> stepsPerBeat_{4};      // Grid resolution within a beat
    std::atomic<int> bars_{1};              // Bars in the loop

    // POLYRHYTHM. 0 = off (the loop lasts as long as its meter says).
    // Above 0, the WHOLE loop is squeezed into this many quarter notes, so
    // its steps stop sharing the pulse of everything else and cut across
    // it: 7 steps fitted into 4 beats is a 7:4 cross-rhythm.
    //
    // Without this the engine could only produce POLYMETER - loops of
    // different lengths over one shared pulse. Two sequencers at 7/4 and
    // 4/4 on the same BPM both step in quarter notes; only the bar length
    // differs, and with every step filled they play in unison forever. A
    // real polyrhythm needs different step RATES across the same span,
    // which no combination of meter, grid and BPM could express.
    std::atomic<float> fitBeats_{0.0f};
    std::atomic<float> swing_{0.0f};        // 0-1 swing amount
    std::atomic<float> gateLength_{0.5f};   // 0-1 gate length
    std::atomic<bool> timingDirty_{true};

    // Transport staging: -1 none, 0 stop, 1 play (consumed on audio thread)
    std::atomic<int> pendingTransport_{-1};

    // State
    std::atomic<bool> isPlaying_{false};
    std::atomic<int> currentStep_{0};

    // Step patterns (one per lane)
    std::array<std::atomic<uint64_t>, MAX_LANES> stepPatterns_;

    // Trigger state per lane
    std::array<std::atomic<float>, MAX_LANES> triggerValues_;

    // ---- Audio-thread owned timing state ----
    double sampleRate_{44100.0};
    double samplesPerStep_{0.0};
    double stepInterval_{0.0};       // current step length incl. swing
    double sampleCounter_{0.0};

    // Per-lane gate state (previously one shared gate for all lanes)
    std::array<double, MAX_LANES> laneGateCounter_{};
    std::array<bool, MAX_LANES> laneGateActive_{};

    // Audio-thread edge detect for Play / Stop control inputs
    float lastPlayControl_{0.0f};
    float lastStopControl_{0.0f};

    void calculateTiming();          // audio thread (and prepare)
    int  derivedStepCount() const;   // beatsPerBar * stepsPerBeat * bars
    void advanceStep();
    void triggerStep();
    void updateStepInterval();
    void clearAllTriggers();

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(SequencerNode)
};
