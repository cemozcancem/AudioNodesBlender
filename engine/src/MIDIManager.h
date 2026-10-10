#pragma once

#include <juce_audio_devices/juce_audio_devices.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <atomic>
#include <functional>
#include <vector>
#include <string>
#include <mutex>

/**
 * MIDIManager handles all MIDI I/O for the audio engine.
 * Thread-safe access to MIDI data for the audio thread.
 */
class MIDIManager : public juce::MidiInputCallback {
public:
    // Callback type for MIDI events
    using MidiCallback = std::function<void(const juce::MidiMessage&)>;
    
    MIDIManager();
    ~MIDIManager() override;
    
    // Device management
    std::vector<std::string> getInputDeviceNames() const;
    std::vector<std::string> getOutputDeviceNames() const;
    
    bool openInputDevice(const std::string& deviceName);
    bool openOutputDevice(const std::string& deviceName);
    void closeInputDevice();
    void closeOutputDevice();
    
    std::string getCurrentInputDevice() const;
    std::string getCurrentOutputDevice() const;
    
    // MIDI Output
    void sendMidiMessage(const juce::MidiMessage& message);
    void sendNoteOn(int channel, int note, int velocity);
    void sendNoteOff(int channel, int note);
    void sendCC(int channel, int ccNumber, int value);
    void sendProgramChange(int channel, int program);
    
    // Callback registration
    void setMidiCallback(MidiCallback callback);
    
    // Thread-safe access to latest MIDI data (for shared memory)
    struct MidiState {
        std::atomic<uint8_t> lastNote{0};
        std::atomic<uint8_t> lastVelocity{0};
        std::atomic<uint8_t> noteGate{0};
        std::atomic<float> ccValues[128];
        std::atomic<float> bpm{120.0f};
        std::atomic<uint32_t> tickPosition{0};
        std::atomic<uint8_t> transportPlaying{0};
        
        MidiState() {
            for (int i = 0; i < 128; ++i) {
                ccValues[i].store(0.0f);
            }
        }
    };
    
    const MidiState& getState() const { return state_; }
    
private:
    // juce::MidiInputCallback
    void handleIncomingMidiMessage(juce::MidiInput* source, 
                                   const juce::MidiMessage& message) override;
    
    std::unique_ptr<juce::MidiInput> midiInput_;
    std::unique_ptr<juce::MidiOutput> midiOutput_;
    
    std::string currentInputDevice_;
    std::string currentOutputDevice_;
    
    MidiCallback midiCallback_;
    std::mutex callbackMutex_;
    
    MidiState state_;
    
    // MIDI Clock processing
    double lastClockTime_{0.0};
    int clockCount_{0};
    static constexpr int CLOCKS_PER_BEAT = 24;
    
    void processClockMessage();
    void processTimingMessage(const juce::MidiMessage& message);
    
    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(MIDIManager)
};

