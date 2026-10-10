#include "AudioEngine.h"
#include "NodeManager.h"
#include "SharedMemoryBridge.h"
#include "common/SharedTypes.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <functional>
#include <future>
#include <iostream>
#include <memory>
#include <thread>
#include <vector>

#if JUCE_MAC
#include "MacAudioPermissions.h"
#endif

AudioEngine::AudioEngine() {
    inputBuffer_.setSize(2, 4096);
    outputBuffer_.setSize(2, 4096);
}

AudioEngine::~AudioEngine() {
    stop();
}

void AudioEngine::initialize(NodeManager* nodeManager, SharedMemoryBridge* sharedMemory) {
    nodeManager_ = nodeManager;
    sharedMemory_ = sharedMemory;
}

bool AudioEngine::start() {
    if (running_.load(std::memory_order_acquire)) {
        return true;
    }
    
    openingDevice_.store(true, std::memory_order_release);
    const bool opened = setupAudioDevice();
    if (opened) {
        deviceManager_.addAudioCallback(this);
        running_.store(true, std::memory_order_release);
    }
    openingDevice_.store(false, std::memory_order_release);

    return opened;
}

void AudioEngine::stop() {
    if (!running_.load(std::memory_order_acquire)) {
        return;
    }
    
    running_.store(false, std::memory_order_release);
    deviceManager_.removeAudioCallback(this);
    deviceManager_.closeAudioDevice();
}

bool AudioEngine::setupAudioDevice() {
    // Test hook: simulate a device that is slow to open, like a Bluetooth
    // headset switching profiles (tests/test_engine.py).
    if (const char* delay = std::getenv("AUDIONODES_TEST_DEVICE_OPEN_DELAY_MS")) {
        std::this_thread::sleep_for(std::chrono::milliseconds(std::atoi(delay)));
    }

    auto& types = deviceManager_.getAvailableDeviceTypes();
    if (types.isEmpty()) {
        return false;
    }
    
    // Use CoreAudio on macOS, WASAPI on Windows
    juce::String preferredType;
#if JUCE_MAC
    preferredType = "CoreAudio";
#elif JUCE_WINDOWS
    preferredType = "Windows Audio";
#endif
    
    for (auto* type : types) {
        if (type->getTypeName() == preferredType) {
            deviceManager_.setCurrentAudioDeviceType(preferredType, true);
            break;
        }
    }
    
    // Setup with default devices.
    // Request up to MAX_INPUT_CHANNELS input channels so multi-channel
    // interfaces (e.g. an 18-in USB mixer) expose ALL their channels to
    // the graph, not just the first stereo pair. JUCE opens
    // min(requested, available), so plain stereo devices are unaffected.
    juce::AudioDeviceManager::AudioDeviceSetup setup;
    setup.sampleRate = sampleRate_.load(std::memory_order_acquire);
    setup.bufferSize = bufferSize_.load(std::memory_order_acquire);

    auto result = deviceManager_.initialise(MAX_INPUT_CHANNELS, 2, nullptr, true, {}, &setup);

    if (result.isNotEmpty()) {
        // Failed with custom settings, try defaults
        result = deviceManager_.initialise(MAX_INPUT_CHANNELS, 2, nullptr, true);
    }
    
    return result.isEmpty();
}

juce::StringArray AudioEngine::getInputDeviceNames() const {
    auto* type = deviceManager_.getCurrentDeviceTypeObject();
    if (type == nullptr) return {};
    return type->getDeviceNames(true);
}

juce::StringArray AudioEngine::getOutputDeviceNames() const {
    auto* type = deviceManager_.getCurrentDeviceTypeObject();
    if (type == nullptr) return {};
    return type->getDeviceNames(false);
}

juce::StringArray AudioEngine::getActiveInputChannelNames() const {
    auto* device = deviceManager_.getCurrentAudioDevice();
    if (device == nullptr) return {};

    // The callback's channel array contains only the ACTIVE channels, in
    // ascending order of their bit in the active mask. Filter the device's
    // full name list through the mask so index N here is exactly channel N
    // as seen by InputNode / the meters.
    auto names = device->getInputChannelNames();
    auto active = device->getActiveInputChannels();

    juce::StringArray result;
    for (int i = 0; i < names.size(); ++i) {
        if (active[i]) {
            result.add(names[i]);
        }
    }
    return result;
}

// Run a device-management action on the JUCE message thread and wait for
// the result. juce::AudioDeviceManager (and CoreAudio underneath it) is
// only reliable when reconfigured from the message thread - calling
// setAudioDeviceSetup from the ZMQ command thread reported success but the
// device switch did not actually take effect while audio was running.
static bool runOnMessageThread(std::function<bool()> action) {
    auto* mm = juce::MessageManager::getInstanceWithoutCreating();
    if (mm == nullptr || mm->isThisTheMessageThread()) {
        return action();
    }

    auto promise = std::make_shared<std::promise<bool>>();
    auto future = promise->get_future();
    juce::MessageManager::callAsync([promise, action]() {
        try {
            promise->set_value(action());
        } catch (...) {
            promise->set_value(false);
        }
    });

    // Generous: a device switch can try several ways of opening the
    // device, and each CoreAudio teardown/reopen takes real time. Timing
    // out early here used to abandon a switch that was about to succeed.
    if (future.wait_for(std::chrono::seconds(15)) != std::future_status::ready) {
        std::cerr << "[AudioEngine] device change timed out waiting for the "
                     "message thread" << std::endl;
        return false;
    }
    return future.get();
}

juce::String AudioEngine::tryOpenDevices(const juce::String& inputName,
                                        const juce::String& outputName,
                                        double sampleRate, int bufferSize) {
    auto setup = deviceManager_.getAudioDeviceSetup();
    setup.inputDeviceName = inputName;
    setup.outputDeviceName = outputName;
    setup.sampleRate = sampleRate;
    setup.bufferSize = bufferSize;
    setup.inputChannels.clear();
    setup.useDefaultInputChannels = true;
    setup.outputChannels.clear();
    setup.useDefaultOutputChannels = true;

    // Full teardown + reopen. Reconfiguring the running device in place
    // (setAudioDeviceSetup, even followed by restartLastAudioDevice)
    // reported success but left CoreAudio's input stream dead until the
    // whole engine was restarted. An engine restart always worked, so
    // replicate exactly what it does: unhook the callback, close the
    // device completely, open fresh with the new setup, re-add the
    // callback.
    deviceManager_.removeAudioCallback(this);
    deviceManager_.closeAudioDevice();

    // Re-enumerate devices before reopening. The engine-boot path (which
    // always produced a working switch) scans right before creating the
    // device; without a fresh scan, reopening the input leg of a combined
    // input/output device came up silently dead on CoreAudio.
    if (auto* type = deviceManager_.getCurrentDeviceTypeObject())
        type->scanForDevices();

    auto err = deviceManager_.setAudioDeviceSetup(setup, true);
    if (err.isEmpty()) {
        deviceManager_.addAudioCallback(this);
    }
    return err;
}

bool AudioEngine::applyDeviceChange(const juce::String& inputName,
                                    const juce::String& outputName,
                                    const char* label) {
    return runOnMessageThread([this, inputName, outputName, label]() {
        const auto previous = deviceManager_.getAudioDeviceSetup();

        // The device the caller asked for must survive every fallback; the
        // OTHER leg is what we are allowed to give up.
        const bool changingOutput = outputName.isNotEmpty();
        const juce::String wantInput =
            inputName.isNotEmpty() ? inputName : previous.inputDeviceName;
        const juce::String wantOutput =
            outputName.isNotEmpty() ? outputName : previous.outputDeviceName;
        const juce::String requested = changingOutput ? wantOutput : wantInput;

        const double preferredRate = preferredSampleRate_.load(std::memory_order_acquire);
        const int preferredBuffer = preferredBufferSize_.load(std::memory_order_acquire);

        // Attempt ladder. A half-open output-only device such as macOS
        // "External Headphones" has to be combined with a SEPARATE input
        // device, and CoreAudio refuses that combination whenever the two
        // cannot agree on a sample rate or buffer size. One rigid attempt
        // therefore failed perhaps half the time, depending on what the
        // previously open device had negotiated - which is exactly the
        // intermittent "engine won't start on external headphones" the
        // ladder below removes.
        struct Attempt {
            juce::String input;
            juce::String output;
            double sampleRate;
            int bufferSize;
            const char* what;
        };

        std::vector<Attempt> attempts;
        attempts.push_back({wantInput, wantOutput, preferredRate, preferredBuffer,
                            "preferred settings"});
        if (preferredRate > 0.0 || preferredBuffer > 0) {
            attempts.push_back({wantInput, wantOutput, 0.0, 0,
                                "device-negotiated rate and buffer"});
        }
        // Last resort for an OUTPUT switch: drop the input leg so the
        // requested output can open on its own instead of failing as half
        // of a pair. Only for outputs - giving up the output to satisfy an
        // input switch would leave the user with no sound at all, which is
        // worse than refusing the switch.
        if (changingOutput && wantInput.isNotEmpty()) {
            attempts.push_back({juce::String(), wantOutput, 0.0, 0,
                                "output alone, input released"});
        }

        juce::String lastError;
        for (const auto& attempt : attempts) {
            lastError = tryOpenDevices(attempt.input, attempt.output,
                                       attempt.sampleRate, attempt.bufferSize);
            if (lastError.isNotEmpty()) {
                std::cerr << "[AudioEngine] " << label << " ('"
                          << requested.toStdString() << "') with "
                          << attempt.what << " failed: "
                          << lastError.toStdString() << std::endl;
                continue;
            }

            // JUCE reports success even when it quietly substituted another
            // device, which looked like a working switch but sent audio
            // somewhere else. Confirm we got what was asked for.
            const auto applied = deviceManager_.getAudioDeviceSetup();
            const juce::String got = changingOutput ? applied.outputDeviceName
                                                    : applied.inputDeviceName;
            if (requested.isNotEmpty() && got != requested) {
                lastError = "device opened as '" + got + "' instead of '"
                          + requested + "'";
                std::cerr << "[AudioEngine] " << label << ": " << lastError.toStdString()
                          << std::endl;
                continue;
            }

            sampleRate_.store(applied.sampleRate, std::memory_order_release);
            bufferSize_.store(applied.bufferSize, std::memory_order_release);
            {
                std::lock_guard<std::mutex> lock(deviceErrorMutex_);
                lastDeviceError_.clear();
            }
            std::cout << "[AudioEngine] " << label << ": input='"
                      << applied.inputDeviceName.toStdString() << "' output='"
                      << applied.outputDeviceName.toStdString() << "' @ "
                      << applied.sampleRate << " Hz / " << applied.bufferSize
                      << " samples (" << attempt.what << ")" << std::endl;
            return true;
        }

        // Every attempt failed: bring the previous device back so audio
        // does not stay dead after a refused switch.
        std::cerr << "[AudioEngine] " << label << " ('" << requested.toStdString()
                  << "') failed on every attempt; restoring previous device"
                  << std::endl;
        tryOpenDevices(previous.inputDeviceName, previous.outputDeviceName, 0.0, 0);
        deviceManager_.restartLastAudioDevice();
        {
            std::lock_guard<std::mutex> lock(deviceErrorMutex_);
            lastDeviceError_ = lastError.isNotEmpty()
                ? lastError : juce::String("the device could not be opened");
        }
        return false;
    });
}

juce::String AudioEngine::getLastDeviceError() const {
    std::lock_guard<std::mutex> lock(deviceErrorMutex_);
    return lastDeviceError_;
}

bool AudioEngine::setInputDevice(const juce::String& deviceName) {
    return applyDeviceChange(deviceName, {}, "Input device switch");
}

bool AudioEngine::setOutputDevice(const juce::String& deviceName) {
    return applyDeviceChange({}, deviceName, "Output device switch");
}

juce::String AudioEngine::getCurrentInputDevice() const {
    return deviceManager_.getAudioDeviceSetup().inputDeviceName;
}

juce::String AudioEngine::getCurrentOutputDevice() const {
    return deviceManager_.getAudioDeviceSetup().outputDeviceName;
}

bool AudioEngine::setSampleRate(double sampleRate) {
    return setAudioSettings(sampleRate, 0);
}

bool AudioEngine::setBufferSize(int bufferSize) {
    return setAudioSettings(0.0, bufferSize);
}

bool AudioEngine::setAudioSettings(double sampleRate, int bufferSize,
                                   bool deferRestart) {
    if (sampleRate > 0.0) {
        preferredSampleRate_.store(sampleRate, std::memory_order_release);
        sampleRate_.store(sampleRate, std::memory_order_release);
    }
    if (bufferSize > 0) {
        preferredBufferSize_.store(bufferSize, std::memory_order_release);
        bufferSize_.store(bufferSize, std::memory_order_release);
    }
    if (deferRestart || !running_.load(std::memory_order_acquire)) {
        return true;  // applied by the next device open
    }
    return applyPreferredAudioSettings();
}

bool AudioEngine::applyPreferredAudioSettings() {
    return runOnMessageThread([this]() {
        auto* device = deviceManager_.getCurrentAudioDevice();
        if (device == nullptr) {
            return false;
        }

        double wantRate = preferredSampleRate_.load(std::memory_order_acquire);
        int wantBuffer = preferredBufferSize_.load(std::memory_order_acquire);

        // Snap to values the device actually supports - CoreAudio rejects
        // (or silently ignores) sizes outside the device's list.
        if (wantBuffer > 0) {
            auto sizes = device->getAvailableBufferSizes();
            if (!sizes.isEmpty() && !sizes.contains(wantBuffer)) {
                int best = sizes[0];
                for (int s : sizes) {
                    if (std::abs(s - wantBuffer) < std::abs(best - wantBuffer)) {
                        best = s;
                    }
                }
                std::cout << "[AudioEngine] buffer size " << wantBuffer
                          << " not supported, snapping to " << best << std::endl;
                wantBuffer = best;
            }
        }
        if (wantRate > 0.0) {
            auto rates = device->getAvailableSampleRates();
            if (!rates.isEmpty() && !rates.contains(wantRate)) {
                double best = rates[0];
                for (double r : rates) {
                    if (std::abs(r - wantRate) < std::abs(best - wantRate)) {
                        best = r;
                    }
                }
                std::cout << "[AudioEngine] sample rate " << wantRate
                          << " not supported, snapping to " << best << std::endl;
                wantRate = best;
            }
        }

        auto setup = deviceManager_.getAudioDeviceSetup();
        if (wantRate > 0.0)  setup.sampleRate = wantRate;
        if (wantBuffer > 0)  setup.bufferSize = wantBuffer;

        auto err = deviceManager_.setAudioDeviceSetup(setup, true);
        if (err.isNotEmpty()) {
            std::cerr << "[AudioEngine] audio settings change failed: "
                      << err.toStdString() << std::endl;
            return false;
        }

        // Store what the device actually negotiated (may differ from the
        // request even after snapping).
        auto applied = deviceManager_.getAudioDeviceSetup();
        sampleRate_.store(applied.sampleRate, std::memory_order_release);
        bufferSize_.store(applied.bufferSize, std::memory_order_release);
        std::cout << "[AudioEngine] audio settings: " << applied.sampleRate
                  << " Hz / " << applied.bufferSize << " samples" << std::endl;
        return true;
    });
}

int AudioEngine::getMinBufferSize() const {
    auto* device = deviceManager_.getCurrentAudioDevice();
    if (device == nullptr) return 0;
    auto sizes = device->getAvailableBufferSizes();
    if (sizes.isEmpty()) return 0;
    int minSize = sizes[0];
    for (int s : sizes) minSize = std::min(minSize, s);
    return minSize;
}

void AudioEngine::requestMicrophonePermission() {
#if JUCE_MAC
    requestMacOSMicrophonePermission();
#endif
}

void AudioEngine::audioDeviceIOCallbackWithContext(
    const float* const* inputChannelData,
    int numInputChannels,
    float* const* outputChannelData,
    int numOutputChannels,
    int numSamples,
    const juce::AudioIODeviceCallbackContext& /*context*/)
{
    // Diagnostic: log the first few callbacks after every device start so
    // silent-input problems are visible in the engine log.
    const int n = diagCallbackCount_.fetch_add(1, std::memory_order_acq_rel);
    const bool diag = n < 3;
    if (diag) {
        float inAbs = 0.0f;
        for (int ch = 0; ch < numInputChannels; ++ch)
            if (inputChannelData[ch] != nullptr)
                for (int s = 0; s < numSamples; ++s)
                    inAbs = std::max(inAbs, std::abs(inputChannelData[ch][s]));
        std::cout << "[AudioEngine] callback#" << n
                  << " inCh=" << numInputChannels
                  << " outCh=" << numOutputChannels
                  << " nSamples=" << numSamples
                  << " |in|max=" << inAbs << std::endl;
    }

    if (nodeManager_ == nullptr) {
        // Clear output
        for (int ch = 0; ch < numOutputChannels; ++ch) {
            if (outputChannelData[ch] != nullptr) {
                std::fill_n(outputChannelData[ch], numSamples, 0.0f);
            }
        }
        return;
    }
    
    // Copy input to buffer. avoidReallocating=true and the capacity set in
    // audioDeviceAboutToStart() guarantee no allocation here.
    inputBuffer_.setSize(numInputChannels, numSamples, false, false, true);
    for (int ch = 0; ch < numInputChannels; ++ch) {
        if (inputChannelData[ch] != nullptr) {
            inputBuffer_.copyFrom(ch, 0, inputChannelData[ch], numSamples);
        }
    }

    // Per-input-channel block peaks for the channel meters in Blender.
    const int meteredChannels = std::min(numInputChannels, MAX_INPUT_CHANNELS);
    for (int ch = 0; ch < meteredChannels; ++ch) {
        float peak = 0.0f;
        if (inputChannelData[ch] != nullptr) {
            for (int s = 0; s < numSamples; ++s) {
                peak = std::max(peak, std::abs(inputChannelData[ch][s]));
            }
        }
        inputPeaks_[ch] = peak;
    }
    
    // Prepare output buffer
    outputBuffer_.setSize(numOutputChannels, numSamples, false, false, true);
    outputBuffer_.clear();
    
    // Process through node manager
    nodeManager_->process(inputBuffer_, outputBuffer_, numSamples);

    if (diag) {
        float bufAbs = 0.0f, outAbs = 0.0f;
        for (int ch = 0; ch < inputBuffer_.getNumChannels(); ++ch) {
            const float* p = inputBuffer_.getReadPointer(ch);
            for (int s = 0; s < numSamples; ++s)
                bufAbs = std::max(bufAbs, std::abs(p[s]));
        }
        for (int ch = 0; ch < outputBuffer_.getNumChannels(); ++ch) {
            const float* p = outputBuffer_.getReadPointer(ch);
            for (int s = 0; s < numSamples; ++s)
                outAbs = std::max(outAbs, std::abs(p[s]));
        }
        std::cout << "[AudioEngine] callback#" << n
                  << " |inputBuffer|=" << bufAbs
                  << " |graphOut|=" << outAbs << std::endl;
    }
    
    // Copy output buffer to device output
    for (int ch = 0; ch < numOutputChannels; ++ch) {
        if (outputChannelData[ch] != nullptr) {
            std::copy_n(outputBuffer_.getReadPointer(ch), numSamples, outputChannelData[ch]);
        }
    }
    
    // Update shared memory with visualization data (including MIDI)
    if (sharedMemory_ != nullptr) {
        sharedMemory_->update(nodeManager_, midiManager_, numSamples,
                              sampleRate_.load(std::memory_order_relaxed),
                              inputPeaks_, meteredChannels);
    }
}

void AudioEngine::audioDeviceAboutToStart(juce::AudioIODevice* device) {
    double sr = device->getCurrentSampleRate();
    int bs = device->getCurrentBufferSizeSamples();

    sampleRate_.store(sr, std::memory_order_release);
    bufferSize_.store(bs, std::memory_order_release);

    // Reserve full channel capacity up front so the setSize() calls in the
    // audio callback (avoidReallocating=true) can never allocate, even on
    // devices with more than 2 channels.
    const int inChannels = std::max(2, device->getActiveInputChannels().countNumberOfSetBits());
    const int outChannels = std::max(2, device->getActiveOutputChannels().countNumberOfSetBits());
    inputBuffer_.setSize(inChannels, bs);
    outputBuffer_.setSize(outChannels, bs);

    std::cout << "[AudioEngine] aboutToStart: '" << device->getName().toStdString()
              << "' activeIn=" << device->getActiveInputChannels().countNumberOfSetBits()
              << " activeOut=" << device->getActiveOutputChannels().countNumberOfSetBits()
              << " @ " << sr << " Hz / " << bs << std::endl;
    diagCallbackCount_.store(0, std::memory_order_release);

    if (nodeManager_ != nullptr) {
        nodeManager_->prepare(sr, bs);
    }
}

void AudioEngine::audioDeviceStopped() {
    // Nothing to do
}

void AudioEngine::audioDeviceError(const juce::String& errorMessage) {
    juce::Logger::writeToLog("Audio device error: " + errorMessage);
}

