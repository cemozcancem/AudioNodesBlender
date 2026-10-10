#include "MIDIInputNode.h"

MIDIInputNode::MIDIInputNode(const std::string& id, MIDIManager* midiManager)
    : BaseNode(id)
    , midiManager_(midiManager)
{
}

void MIDIInputNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
}

void MIDIInputNode::reset() {
    BaseNode::reset();
    currentNote_.store(0);
    currentVelocity_.store(0.0f);
    gate_.store(0.0f);
}

void MIDIInputNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    (void)buffer;
    (void)numSamples;

    // Update values from MIDI manager state
    if (midiManager_) {
        const auto& state = midiManager_->getState();
        currentNote_.store(state.lastNote.load(std::memory_order_relaxed),
                           std::memory_order_relaxed);
        currentVelocity_.store(
            static_cast<float>(state.lastVelocity.load(std::memory_order_relaxed)) / 127.0f,
            std::memory_order_relaxed);
        gate_.store(state.noteGate.load(std::memory_order_relaxed) > 0 ? 1.0f : 0.0f,
                    std::memory_order_relaxed);
    }
}

void MIDIInputNode::setParameter(const std::string& name, float value) {
    (void)name;
    (void)value;
    // MIDI input node has no settable parameters
}

float MIDIInputNode::getParameter(const std::string& name) const {
    if (name == "note") return getNote();
    if (name == "velocity") return getVelocity();
    if (name == "gate") return getGate();
    return 0.0f;
}
