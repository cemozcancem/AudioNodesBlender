#include "MIDIManager.h"
#include <juce_core/juce_core.h>

MIDIManager::MIDIManager() {
}

MIDIManager::~MIDIManager() {
    closeInputDevice();
    closeOutputDevice();
}

std::vector<std::string> MIDIManager::getInputDeviceNames() const {
    std::vector<std::string> names;
    auto devices = juce::MidiInput::getAvailableDevices();
    for (const auto& device : devices) {
        names.push_back(device.name.toStdString());
    }
    return names;
}

std::vector<std::string> MIDIManager::getOutputDeviceNames() const {
    std::vector<std::string> names;
    auto devices = juce::MidiOutput::getAvailableDevices();
    for (const auto& device : devices) {
        names.push_back(device.name.toStdString());
    }
    return names;
}

bool MIDIManager::openInputDevice(const std::string& deviceName) {
    closeInputDevice();
    
    std::cout << "[MIDI] Attempting to open input device: " << deviceName << std::endl;
    
    auto devices = juce::MidiInput::getAvailableDevices();
    std::cout << "[MIDI] Available input devices: " << devices.size() << std::endl;
    for (const auto& device : devices) {
        std::cout << "[MIDI]   - " << device.name.toStdString() << std::endl;
        if (device.name.toStdString() == deviceName) {
            midiInput_ = juce::MidiInput::openDevice(device.identifier, this);
            if (midiInput_) {
                midiInput_->start();
                currentInputDevice_ = deviceName;
                std::cout << "[MIDI] SUCCESS: Input device opened and started: " << deviceName << std::endl;
                return true;
            } else {
                std::cout << "[MIDI] FAILED: Could not open device: " << deviceName << std::endl;
            }
        }
    }
    
    std::cout << "[MIDI] FAILED: Device not found: " << deviceName << std::endl;
    return false;
}

bool MIDIManager::openOutputDevice(const std::string& deviceName) {
    closeOutputDevice();
    
    auto devices = juce::MidiOutput::getAvailableDevices();
    for (const auto& device : devices) {
        if (device.name.toStdString() == deviceName) {
            midiOutput_ = juce::MidiOutput::openDevice(device.identifier);
            if (midiOutput_) {
                currentOutputDevice_ = deviceName;
                DBG("MIDI Output opened: " << deviceName);
                return true;
            }
        }
    }
    
    DBG("Failed to open MIDI output: " << deviceName);
    return false;
}

void MIDIManager::closeInputDevice() {
    if (midiInput_) {
        midiInput_->stop();
        midiInput_.reset();
        currentInputDevice_.clear();
    }
}

void MIDIManager::closeOutputDevice() {
    midiOutput_.reset();
    currentOutputDevice_.clear();
}

std::string MIDIManager::getCurrentInputDevice() const {
    return currentInputDevice_;
}

std::string MIDIManager::getCurrentOutputDevice() const {
    return currentOutputDevice_;
}

void MIDIManager::sendMidiMessage(const juce::MidiMessage& message) {
    if (midiOutput_) {
        midiOutput_->sendMessageNow(message);
    }
}

void MIDIManager::sendNoteOn(int channel, int note, int velocity) {
    sendMidiMessage(juce::MidiMessage::noteOn(channel, note, (juce::uint8)velocity));
}

void MIDIManager::sendNoteOff(int channel, int note) {
    sendMidiMessage(juce::MidiMessage::noteOff(channel, note));
}

void MIDIManager::sendCC(int channel, int ccNumber, int value) {
    sendMidiMessage(juce::MidiMessage::controllerEvent(channel, ccNumber, value));
}

void MIDIManager::sendProgramChange(int channel, int program) {
    sendMidiMessage(juce::MidiMessage::programChange(channel, program));
}

void MIDIManager::setMidiCallback(MidiCallback callback) {
    std::lock_guard<std::mutex> lock(callbackMutex_);
    midiCallback_ = std::move(callback);
}

void MIDIManager::handleIncomingMidiMessage(juce::MidiInput* source, 
                                            const juce::MidiMessage& message) {
    (void)source;

    // NOTE: this callback runs on the realtime MIDI thread - no logging,
    // no I/O, atomics only.
    if (message.isNoteOn()) {
        state_.lastNote.store(static_cast<uint8_t>(message.getNoteNumber()));
        state_.lastVelocity.store(static_cast<uint8_t>(message.getVelocity()));
        state_.noteGate.store(1);
    }
    else if (message.isNoteOff()) {
        state_.noteGate.store(0);
    }
    else if (message.isController()) {
        int ccNum = message.getControllerNumber();
        if (ccNum >= 0 && ccNum < 128) {
            float normalizedValue = message.getControllerValue() / 127.0f;
            state_.ccValues[ccNum].store(normalizedValue);
        }
    }
    else if (message.isMidiClock()) {
        processClockMessage();
    }
    else if (message.isMidiStart() || message.isMidiContinue()) {
        state_.transportPlaying.store(1);
        if (message.isMidiStart()) {
            state_.tickPosition.store(0);
            clockCount_ = 0;
        }
    }
    else if (message.isMidiStop()) {
        state_.transportPlaying.store(0);
    }
    else if (message.isSongPositionPointer()) {
        // Song position in MIDI beats (1/16th notes)
        int position = message.getSongPositionPointerMidiBeat();
        // Convert to ticks (6 clocks per 1/16th note)
        state_.tickPosition.store(static_cast<uint32_t>(position * 6));
    }
    
    // Call user callback
    {
        std::lock_guard<std::mutex> lock(callbackMutex_);
        if (midiCallback_) {
            midiCallback_(message);
        }
    }
}

void MIDIManager::processClockMessage() {
    double currentTime = juce::Time::getMillisecondCounterHiRes() / 1000.0;
    
    clockCount_++;
    state_.tickPosition.store(state_.tickPosition.load() + 1);
    
    // Calculate BPM from clock interval
    if (clockCount_ >= CLOCKS_PER_BEAT) {
        double interval = currentTime - lastClockTime_;
        if (interval > 0.0 && lastClockTime_ > 0.0) {
            // interval is time for 24 clocks (1 beat)
            double bpm = 60.0 / interval;
            // Sanity check
            if (bpm >= 20.0 && bpm <= 300.0) {
                state_.bpm.store(static_cast<float>(bpm));
            }
        }
        lastClockTime_ = currentTime;
        clockCount_ = 0;
    }
}

