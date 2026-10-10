#pragma once

#include "BaseNode.h"
#include <atomic>
#include <cstdint>

/**
 * BangNode - Manual / chained trigger pulse source.
 *
 * Fire from the UI (set_param "fire") or a rising edge on the Trigger input.
 * Output stays high for gate_ms milliseconds so downstream Sampler / Drum /
 * Sequencer Play inputs all see a clean 0→1→0 edge. One Bang output can
 * fan out to many destinations via Blender links.
 *
 * Re-firing: "fire" is a COUNTER, not a level. Every set_param("fire") the
 * engine receives bumps it, and process() compares against the count it
 * last acted on - so the second, third and hundredth press each produce
 * their own pulse. A level flag could not do this: it was already true (or
 * already consumed) and the repeat press vanished.
 *
 * Pressing again while the previous pulse is still high forces ONE block
 * at zero before re-arming. Without that gap downstream edge detectors
 * (Sampler trigger, Sequencer Play) never see a falling edge and treat the
 * retrigger as a continuation of the pulse they are already inside.
 */
class BangNode : public BaseNode {
public:
    static constexpr int PORT_TRIGGER = 0;

    explicit BangNode(const std::string& id);
    ~BangNode() override = default;

    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;

    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    int resolvePortIndex(const std::string& portName) const override;
    int resolveOutputLane(const std::string& portName) const override;

    float getControlOutput(int lane = 0) const override;

    // Published to Blender through shared memory so nodes downstream of a
    // Bang update the moment it fires instead of waiting for a UI event.
    uint32_t getPulseCountForShm() const {
        return firedPulses_.load(std::memory_order_relaxed);
    }

private:
    std::atomic<float> gateMs_{20.0f};
    std::atomic<uint32_t> fireRequests_{0};   // bumped by set_param("fire")
    std::atomic<uint32_t> firedPulses_{0};    // pulses actually emitted
    std::atomic<float> output_{0.0f};

    double sampleRate_{44100.0};
    int samplesLeft_{0};
    uint32_t lastSeenFireRequests_{0};
    bool armNextBlock_{false};
    float lastTriggerControl_{0.0f};

    void armPulse();

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(BangNode)
};
