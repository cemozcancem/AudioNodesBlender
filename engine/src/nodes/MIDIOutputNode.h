#pragma once

#include "BaseNode.h"
#include "../MIDIManager.h"
#include <atomic>

/**
 * MIDIOutputNode - Sends MIDI to external devices.
 */
class MIDIOutputNode : public BaseNode {
public:
    explicit MIDIOutputNode(const std::string& id, MIDIManager* midiManager);
    ~MIDIOutputNode() override = default;
    
    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;
    
    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;
    
    // Direct MIDI sending (called from ZMQ commands)
    void sendNoteOn(int channel, int note, int velocity);
    void sendNoteOff(int channel, int note);
    void sendCC(int channel, int ccNumber, int value);
    
private:
    MIDIManager* midiManager_;
    
    std::atomic<int> channel_{1};
    std::atomic<int> pendingNote_{-1};
    std::atomic<int> pendingVelocity_{0};
    std::atomic<bool> pendingNoteOn_{false};
    std::atomic<bool> pendingNoteOff_{false};
    
    int lastSentNote_{-1};
    
    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(MIDIOutputNode)
};

