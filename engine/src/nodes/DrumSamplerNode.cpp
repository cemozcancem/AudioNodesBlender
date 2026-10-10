#include "DrumSamplerNode.h"
#include <cmath>

DrumSamplerNode::DrumSamplerNode(const std::string& id)
    : BaseNode(id)
{
}

DrumSamplerNode::~DrumSamplerNode() {
    // Runs on the command thread after the audio thread has unlinked us.
    drainRetiredSamples();
    for (auto& slot : slots_) {
        delete slot.pending.exchange(nullptr);
        delete slot.current;
    }
}

void DrumSamplerNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    sampleRate_ = sampleRate;
}

void DrumSamplerNode::reset() {
    BaseNode::reset();
    for (auto& slot : slots_) {
        slot.playing.store(false);
        slot.position.store(0.0);
        slot.pendingTrigger.store(false);
        slot.lastTrigger.store(0.0f);
        slot.lastTriggerControl = 0.0f;
    }
}

int DrumSamplerNode::resolvePortIndex(const std::string& portName) const {
    if (portName == "input" || portName == "audio_in") return PORT_AUDIO;
    // "trig_1".."trig_8" -> ports 1..8
    if (portName.rfind("trig_", 0) == 0) {
        const int n = parseIndex(portName, 5);
        if (n >= 1 && n <= NUM_SLOTS) {
            return PORT_TRIG_BASE + (n - 1);
        }
    }
    return -1;
}

void DrumSamplerNode::drainRetiredSamples() {
    while (auto retired = retiredSamples_.pop()) {
        delete *retired;
    }
}

bool DrumSamplerNode::loadSample(int slot, const std::string& filePath) {
    if (slot < 0 || slot >= NUM_SLOTS) {
        return false;
    }

    drainRetiredSamples();

    juce::AudioFormatManager formatManager;
    formatManager.registerBasicFormats();

    juce::File file(filePath);
    if (!file.existsAsFile()) {
        DBG("Drum sample file not found: " << filePath);
        return false;
    }

    std::unique_ptr<juce::AudioFormatReader> reader(
        formatManager.createReaderFor(file)
    );

    if (!reader) {
        DBG("Could not create reader for: " << filePath);
        return false;
    }

    auto data = std::make_unique<SampleData>();
    data->buffer.setSize(
        static_cast<int>(reader->numChannels),
        static_cast<int>(reader->lengthInSamples)
    );
    reader->read(
        &data->buffer,
        0,
        static_cast<int>(reader->lengthInSamples),
        0,
        true,
        true
    );
    data->sampleRate = reader->sampleRate;
    data->length = static_cast<int>(reader->lengthInSamples);

    auto& s = slots_[slot];
    s.filePath = filePath;

    SampleData* unconsumed = s.pending.exchange(data.release(),
                                                std::memory_order_acq_rel);
    delete unconsumed;

    s.loaded.store(true);

    DBG("Loaded drum sample " << slot << ": " << filePath);
    return true;
}

void DrumSamplerNode::adoptPendingSamples() {
    for (auto& s : slots_) {
        SampleData* fresh = s.pending.exchange(nullptr, std::memory_order_acq_rel);
        if (fresh == nullptr) {
            continue;
        }
        SampleData* old = s.current;
        s.current = fresh;
        s.playing.store(false);
        s.position.store(0.0);
        if (old != nullptr) {
            if (!retiredSamples_.push(old)) {
                jassertfalse;  // deliberately leak rather than free here
            }
        }
    }
}

void DrumSamplerNode::triggerSlot(int slot) {
    if (slot >= 0 && slot < NUM_SLOTS) {
        slots_[slot].pendingTrigger.store(true, std::memory_order_release);
    }
}

void DrumSamplerNode::stopSlot(int slot) {
    if (slot >= 0 && slot < NUM_SLOTS) {
        slots_[slot].playing.store(false);
    }
}

double DrumSamplerNode::getPitchRatio(int slot, double sourceSampleRate) const {
    const float semitones = slots_[slot].pitch.load(std::memory_order_relaxed);
    const double pitchRatio = std::pow(2.0, semitones / 12.0);
    const double rateRatio = sourceSampleRate / sampleRate_;
    return pitchRatio * rateRatio;
}

void DrumSamplerNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Pull upstream audio (if connected)
    pullInput(PORT_AUDIO, buffer, numSamples);

    // Muted (bypassed): pass upstream audio through, play nothing.
    if (isBypassed()) {
        for (auto& s : slots_) {
            s.pendingTrigger.store(false, std::memory_order_relaxed);
            s.playing.store(false);
        }
        return;
    }

    adoptPendingSamples();

    // Per-slot trigger control inputs (port 1 -> slot 0, ...). Pure index
    // lookups - no strings, no map, no allocation.
    for (int slot = 0; slot < NUM_SLOTS; ++slot) {
        const int port = PORT_TRIG_BASE + slot;
        BaseNode* triggerInput = getInput(port);
        if (triggerInput == nullptr) {
            continue;
        }
        auto& s = slots_[slot];
        const float trigVal = triggerInput->getControlOutput(getInputLane(port));
        if (trigVal > 0.5f && s.lastTriggerControl <= 0.5f) {
            s.pendingTrigger.store(true, std::memory_order_relaxed);
        }
        s.lastTriggerControl = trigVal;
    }

    // Process each slot
    for (int slot = 0; slot < NUM_SLOTS; ++slot) {
        processSlot(slot, buffer, numSamples);
    }

    // Apply master volume
    const float master = masterVolume_.load(std::memory_order_relaxed);
    if (std::abs(master - 1.0f) > 0.001f) {
        buffer.applyGain(master);
    }

    applyFade(buffer, numSamples);
}

void DrumSamplerNode::processSlot(int slot, juce::AudioBuffer<float>& buffer, int numSamples) {
    auto& s = slots_[slot];

    if (s.current == nullptr) {
        s.pendingTrigger.store(false, std::memory_order_relaxed);
        return;
    }

    // Consume staged triggers on the audio thread.
    if (s.pendingTrigger.exchange(false, std::memory_order_acq_rel)) {
        s.position.store(0.0);
        s.playing.store(true);
    }

    if (!s.playing.load(std::memory_order_relaxed)) {
        return;
    }

    const int length = s.current->length;
    const float vol = s.volume.load(std::memory_order_relaxed);
    const float pan = s.pan.load(std::memory_order_relaxed);
    const double pitchRatio = getPitchRatio(slot, s.current->sampleRate);

    // Constant power panning
    const float panAngle = (pan + 1.0f) * 0.25f * juce::MathConstants<float>::pi;
    const float leftGain = std::cos(panAngle) * vol;
    const float rightGain = std::sin(panAngle) * vol;

    const int sampleChannels = s.current->buffer.getNumChannels();
    const int numChannels = std::min(buffer.getNumChannels(), sampleChannels);

    double pos = s.position.load(std::memory_order_relaxed);

    for (int sample = 0; sample < numSamples; ++sample) {
        if (pos >= length) {
            s.playing.store(false);
            break;
        }

        // Linear interpolation
        const int idx0 = static_cast<int>(pos);
        const int idx1 = std::min(idx0 + 1, length - 1);
        const float frac = static_cast<float>(pos - idx0);

        if (sampleChannels == 1) {
            const float s0 = s.current->buffer.getSample(0, idx0);
            const float s1 = s.current->buffer.getSample(0, idx1);
            const float sampleVal = s0 + frac * (s1 - s0);

            if (buffer.getNumChannels() >= 2) {
                buffer.addSample(0, sample, sampleVal * leftGain);
                buffer.addSample(1, sample, sampleVal * rightGain);
            } else if (buffer.getNumChannels() == 1) {
                buffer.addSample(0, sample, sampleVal * vol);
            }
        } else {
            for (int ch = 0; ch < std::min(2, buffer.getNumChannels()); ++ch) {
                const int srcCh = std::min(ch, numChannels - 1);
                const float s0 = s.current->buffer.getSample(srcCh, idx0);
                const float s1 = s.current->buffer.getSample(srcCh, idx1);
                const float interpolated = s0 + frac * (s1 - s0);

                const float gain = (ch == 0) ? leftGain : rightGain;
                buffer.addSample(ch, sample, interpolated * gain);
            }
        }

        pos += pitchRatio;
    }

    s.position.store(pos, std::memory_order_relaxed);
}

void DrumSamplerNode::setParameter(const std::string& name, float value) {
    if (name == "master_volume") {
        masterVolume_.store(std::clamp(value, 0.0f, 2.0f));
        return;
    }

    // Slot-specific parameters: slot_0_volume, slot_1_pitch, ...
    if (name.rfind("slot_", 0) == 0) {
        const size_t underscorePos = name.find('_', 5);
        if (underscorePos != std::string::npos) {
            const int slot = parseIndex(name, 5, underscorePos);
            const std::string param = name.substr(underscorePos + 1);

            if (slot >= 0 && slot < NUM_SLOTS) {
                auto& s = slots_[slot];

                if (param == "volume") {
                    s.volume.store(std::clamp(value, 0.0f, 2.0f));
                }
                else if (param == "pitch") {
                    s.pitch.store(std::clamp(value, -24.0f, 24.0f));
                }
                else if (param == "pan") {
                    s.pan.store(std::clamp(value, -1.0f, 1.0f));
                }
                else if (param == "trigger") {
                    const float prev = s.lastTrigger.load();
                    if (value > 0.5f && prev <= 0.5f) {
                        triggerSlot(slot);
                    }
                    s.lastTrigger.store(value);
                }
            }
        }
        return;
    }

    // Simple trigger commands: trigger_0, trigger_1, ...
    if (name.rfind("trigger_", 0) == 0) {
        const int slot = parseIndex(name, 8);
        if (slot >= 0 && slot < NUM_SLOTS && value > 0.5f) {
            triggerSlot(slot);
        }
    }
}

float DrumSamplerNode::getParameter(const std::string& name) const {
    if (name == "master_volume") return masterVolume_.load();

    if (name.rfind("slot_", 0) == 0) {
        const size_t underscorePos = name.find('_', 5);
        if (underscorePos != std::string::npos) {
            const int slot = parseIndex(name, 5, underscorePos);
            const std::string param = name.substr(underscorePos + 1);

            if (slot >= 0 && slot < NUM_SLOTS) {
                const auto& s = slots_[slot];

                if (param == "volume") return s.volume.load();
                if (param == "pitch") return s.pitch.load();
                if (param == "pan") return s.pan.load();
                if (param == "loaded") return s.loaded.load() ? 1.0f : 0.0f;
                if (param == "playing") return s.playing.load() ? 1.0f : 0.0f;
            }
        }
    }

    return 0.0f;
}
