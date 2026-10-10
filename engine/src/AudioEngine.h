#pragma once

#include <juce_audio_devices/juce_audio_devices.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <memory>
#include <atomic>
#include <mutex>

class NodeManager;
class SharedMemoryBridge;
class MIDIManager;

// Core audio engine - manages audio device and routes to NodeManager
class AudioEngine : public juce::AudioIODeviceCallback {
public:
    AudioEngine();
    ~AudioEngine() override;
    
    // Initialize with NodeManager and SharedMemoryBridge
    void initialize(NodeManager* nodeManager, SharedMemoryBridge* sharedMemory);
    
    // Set MIDIManager for shared memory updates
    void setMIDIManager(MIDIManager* midiManager) { midiManager_ = midiManager; }
    
    // Device management
    bool start();
    void stop();
    bool isRunning() const { return running_.load(std::memory_order_acquire); }
    
    // Device selection
    juce::StringArray getInputDeviceNames() const;
    juce::StringArray getOutputDeviceNames() const;
    // Names of the ACTIVE input channels of the current device, in the
    // same order as the channels arriving in the audio callback.
    juce::StringArray getActiveInputChannelNames() const;
    bool setInputDevice(const juce::String& deviceName);
    bool setOutputDevice(const juce::String& deviceName);
    // Why the last device switch failed ("" after a success). Lets the
    // add-on tell the user what actually went wrong instead of a bare
    // "Failed to set output device".
    juce::String getLastDeviceError() const;
    juce::String getCurrentInputDevice() const;
    juce::String getCurrentOutputDevice() const;
    
    // Audio settings
    double getSampleRate() const { return sampleRate_; }
    int getBufferSize() const { return bufferSize_; }
    bool setSampleRate(double sampleRate);
    bool setBufferSize(int bufferSize);
    // Apply sample rate and/or buffer size in ONE device restart.
    // Pass 0 to keep the current value. Values become the preferred
    // settings, re-applied after every device switch. The device may snap
    // to the nearest supported value - read back via the getters.
    // deferRestart: remember the values as preferences but do NOT reopen
    // the device now. Used at startup, where the saved device is restored
    // immediately afterwards and would otherwise be the SECOND full
    // CoreAudio reopen in a row - the reliable path is one open with the
    // right settings already in place.
    bool setAudioSettings(double sampleRate, int bufferSize,
                          bool deferRestart = false);
    // Smallest buffer size the current device supports (0 if unknown).
    int getMinBufferSize() const;
    
    // Request microphone permission (macOS)
    static void requestMicrophonePermission();
    
    // AudioIODeviceCallback implementation
    void audioDeviceIOCallbackWithContext(
        const float* const* inputChannelData,
        int numInputChannels,
        float* const* outputChannelData,
        int numOutputChannels,
        int numSamples,
        const juce::AudioIODeviceCallbackContext& context) override;
    
    void audioDeviceAboutToStart(juce::AudioIODevice* device) override;
    void audioDeviceStopped() override;
    void audioDeviceError(const juce::String& errorMessage) override;
    
private:
    juce::AudioDeviceManager deviceManager_;
    NodeManager* nodeManager_ = nullptr;
    SharedMemoryBridge* sharedMemory_ = nullptr;
    MIDIManager* midiManager_ = nullptr;
    
    std::atomic<bool> running_{false};
    std::atomic<double> sampleRate_{44100.0};
    std::atomic<int> bufferSize_{512};
    std::atomic<int> diagCallbackCount_{0};

    // User-preferred settings (0 = no preference, let JUCE negotiate).
    // Honored on every device switch, unlike sampleRate_/bufferSize_
    // which always reflect what the device actually negotiated.
    std::atomic<double> preferredSampleRate_{0.0};
    std::atomic<int> preferredBufferSize_{0};
    
    // Temporary buffers for processing
    juce::AudioBuffer<float> inputBuffer_;
    juce::AudioBuffer<float> outputBuffer_;

    // Per-input-channel block peaks, recomputed every callback and handed
    // to SharedMemoryBridge for the channel meters. Audio thread only.
    float inputPeaks_[32] = {};

    // Setup device with current settings
    bool setupAudioDevice();

    // One open attempt inside applyDeviceChange. Message thread.
    // Returns JUCE's error string ("" on success).
    juce::String tryOpenDevices(const juce::String& inputName,
                                const juce::String& outputName,
                                double sampleRate, int bufferSize);

    mutable std::mutex deviceErrorMutex_;
    juce::String lastDeviceError_;

    // Shared full teardown + reopen device switch (runs on the JUCE
    // message thread). Empty string = keep the current device.
    bool applyDeviceChange(const juce::String& inputName,
                           const juce::String& outputName,
                           const char* label);

    // Apply preferredSampleRate_/preferredBufferSize_ to the running
    // device (message thread, snapped to supported values).
    bool applyPreferredAudioSettings();
};

