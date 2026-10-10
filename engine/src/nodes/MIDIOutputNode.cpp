#include "MIDIOutputNode.h"

MIDIOutputNode::MIDIOutputNode(const std::string& id, MIDIManager* midiManager)
    : BaseNode(id)
    , midiManager_(midiManager)
{
}

void MIDIOutputNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
}

void MIDIOutputNode::reset() {
    BaseNode::reset();
    
    // Send note off for any pending notes
    if (lastSentNote_ >= 0 && midiManager_) {
        midiManager_->sendNoteOff(channel_.load(), lastSentNote_);
        lastSentNote_ = -1;
    }
    
    pendingNote_.store(-1);
    pendingNoteOn_.store(false);
    pendingNoteOff_.store(false);
}

void MIDIOutputNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    (void)buffer;
    (void)numSamples;

    // Muted (bypassed): send nothing, drop staged note-ons. Still allow a
    // pending note-off through so no note hangs forever.
    if (isBypassed()) {
        pendingNoteOn_.store(false);
        pendingNote_.store(-1);
        if (pendingNoteOff_.load() && lastSentNote_ >= 0 && midiManager_) {
            midiManager_->sendNoteOff(channel_.load(), lastSentNote_);
            lastSentNote_ = -1;
            pendingNoteOff_.store(false);
        }
        return;
    }

    // Process any pending MIDI messages
    if (pendingNoteOff_.load() && lastSentNote_ >= 0) {
        if (midiManager_) {
            midiManager_->sendNoteOff(channel_.load(), lastSentNote_);
        }
        lastSentNote_ = -1;
        pendingNoteOff_.store(false);
    }
    
    if (pendingNoteOn_.load()) {
        int note = pendingNote_.load();
        int velocity = pendingVelocity_.load();
        
        if (note >= 0 && midiManager_) {
            // Turn off previous note if different
            if (lastSentNote_ >= 0 && lastSentNote_ != note) {
                midiManager_->sendNoteOff(channel_.load(), lastSentNote_);
            }
            
            midiManager_->sendNoteOn(channel_.load(), note, velocity);
            lastSentNote_ = note;
        }
        
        pendingNoteOn_.store(false);
    }
}

void MIDIOutputNode::setParameter(const std::string& name, float value) {
    if (name == "channel") {
        channel_.store(static_cast<int>(value));
    }
    else if (name == "note") {
        pendingNote_.store(static_cast<int>(value));
    }
    else if (name == "velocity") {
        pendingVelocity_.store(static_cast<int>(value));
    }
    else if (name == "gate") {
        if (value > 0.5f) {
            pendingNoteOn_.store(true);
        } else {
            pendingNoteOff_.store(true);
        }
    }
}

float MIDIOutputNode::getParameter(const std::string& name) const {
    if (name == "channel") return static_cast<float>(channel_.load());
    return 0.0f;
}

void MIDIOutputNode::sendNoteOn(int channel, int note, int velocity) {
    if (midiManager_) {
        // Turn off previous note if needed
        if (lastSentNote_ >= 0 && lastSentNote_ != note) {
            midiManager_->sendNoteOff(channel, lastSentNote_);
        }
        midiManager_->sendNoteOn(channel, note, velocity);
        lastSentNote_ = note;
    }
}

void MIDIOutputNode::sendNoteOff(int channel, int note) {
    if (midiManager_) {
        midiManager_->sendNoteOff(channel, note);
        if (lastSentNote_ == note) {
            lastSentNote_ = -1;
        }
    }
}

void MIDIOutputNode::sendCC(int channel, int ccNumber, int value) {
    if (midiManager_) {
        midiManager_->sendCC(channel, ccNumber, value);
    }
}

