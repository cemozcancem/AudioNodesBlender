#pragma once

#include "BaseNode.h"
#include "../MIDIManager.h"
#include <atomic>

/**
 * MIDIInputNode - Receives MIDI input and exposes values to the node graph.
 * Note: This node doesn't process audio, it only provides MIDI data.
 * Values are atomics: written on the audio thread, read from the command
 * thread via getParameter().
 */
class MIDIInputNode : public BaseNode {
public:
    explicit MIDIInputNode(const std::string& id, MIDIManager* midiManager);
    ~MIDIInputNode() override = default;

    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;

    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    // Gate as control output so it can drive samplers/ADSR directly.
    float getControlOutput(int /*lane*/ = 0) const override { return gate_.load(); }

    // Get current MIDI values (for output sockets)
    float getNote() const { return static_cast<float>(currentNote_.load()); }
    float getVelocity() const { return currentVelocity_.load(); }
    float getGate() const { return gate_.load(); }

private:
    MIDIManager* midiManager_;

    // Current values (updated from MIDIManager state on the audio thread)
    std::atomic<int> currentNote_{0};
    std::atomic<float> currentVelocity_{0.0f};
    std::atomic<float> gate_{0.0f};

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(MIDIInputNode)
};
