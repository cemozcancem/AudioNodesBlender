#include "SamplerNode.h"
#include <cmath>

SamplerNode::SamplerNode(const std::string& id)
    : BaseNode(id)
{
}

SamplerNode::~SamplerNode() {
    // Runs on the command thread after the audio thread has unlinked us.
    drainRetiredSamples();
    delete pendingSample_.exchange(nullptr);
    delete currentSample_;
}

void SamplerNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    sampleRate_ = sampleRate;
}

void SamplerNode::reset() {
    BaseNode::reset();
    isPlaying_.store(false);
    playPosition_.store(0.0);
    pendingTrigger_.store(false);
    lastTriggerValue_.store(0.0f);
    lastTriggerControl_ = 0.0f;
}

int SamplerNode::resolvePortIndex(const std::string& portName) const {
    if (portName == "input" || portName == "audio_in") return PORT_AUDIO;
    if (portName == "trigger") return PORT_TRIGGER;
    // volume/pitch/start/end control ports are driven via set_param
    return -1;
}

void SamplerNode::drainRetiredSamples() {
    while (auto retired = retiredSamples_.pop()) {
        delete *retired;
    }
}

bool SamplerNode::loadSample(const std::string& filePath) {
    // Free buffers the audio thread has retired since the last load.
    drainRetiredSamples();

    juce::AudioFormatManager formatManager;
    formatManager.registerBasicFormats();

    juce::File file(filePath);
    if (!file.existsAsFile()) {
        DBG("Sample file not found: " << filePath);
        return false;
    }

    std::unique_ptr<juce::AudioFormatReader> reader(
        formatManager.createReaderFor(file)
    );

    if (!reader) {
        DBG("Could not create reader for: " << filePath);
        return false;
    }

    // Build the sample entirely off the audio thread.
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

    currentFilePath_ = filePath;
    currentLength_.store(data->length);

    // Publish. If a previous pending sample was never consumed, free it here.
    SampleData* unconsumed = pendingSample_.exchange(data.release(),
                                                     std::memory_order_acq_rel);
    delete unconsumed;

    sampleLoaded_.store(true);

    DBG("Loaded sample: " << filePath);
    return true;
}

void SamplerNode::adoptPendingSample() {
    SampleData* fresh = pendingSample_.exchange(nullptr, std::memory_order_acq_rel);
    if (fresh == nullptr) {
        return;
    }

    SampleData* old = currentSample_;
    currentSample_ = fresh;
    isPlaying_.store(false);
    playPosition_.store(0.0);

    if (old != nullptr) {
        if (!retiredSamples_.push(old)) {
            // Queue full (should never happen: the loader drains it on every
            // load). Deliberately leak rather than free on the audio thread.
            jassertfalse;
        }
    }
}

double SamplerNode::getPitchRatio(double sourceSampleRate) const {
    const float semitones = pitch_.load(std::memory_order_relaxed);
    const double pitchRatio = std::pow(2.0, semitones / 12.0);
    const double rateRatio = sourceSampleRate / sampleRate_;
    return pitchRatio * rateRatio;
}

void SamplerNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Pull upstream audio (if connected)
    pullInput(PORT_AUDIO, buffer, numSamples);

    // Muted (bypassed): pass upstream audio through, play nothing.
    if (isBypassed()) {
        pendingTrigger_.store(false, std::memory_order_relaxed);
        isPlaying_.store(false);
        return;
    }

    // Swap in a freshly loaded sample, if any.
    adoptPendingSample();

    // Trigger edges from a connected control node (e.g. sequencer lane)
    if (BaseNode* triggerInput = getInput(PORT_TRIGGER)) {
        const float trigVal = triggerInput->getControlOutput(getInputLane(PORT_TRIGGER));
        if (trigVal > 0.5f && lastTriggerControl_ <= 0.5f) {
            pendingTrigger_.store(true, std::memory_order_relaxed);
        } else if (trigVal <= 0.5f && lastTriggerControl_ > 0.5f
                   && !oneShot_.load(std::memory_order_relaxed)) {
            isPlaying_.store(false);
        }
        lastTriggerControl_ = trigVal;
    }

    if (currentSample_ == nullptr) {
        pendingTrigger_.store(false, std::memory_order_relaxed);
        return;
    }

    const int length = currentSample_->length;

    // Consume staged triggers on the audio thread, where the sample length
    // is known and stable.
    if (pendingTrigger_.exchange(false, std::memory_order_acq_rel)) {
        if (oneShot_.load(std::memory_order_relaxed) || !isPlaying_.load()) {
            playPosition_.store(startPoint_.load(std::memory_order_relaxed) * length);
            isPlaying_.store(true);
        }
    }

    if (!isPlaying_.load(std::memory_order_relaxed)) {
        return;
    }

    const float vol = volume_.load(std::memory_order_relaxed);
    const double pitchRatio = getPitchRatio(currentSample_->sampleRate);
    const double endPos = endPoint_.load(std::memory_order_relaxed) * length;
    const bool shouldLoop = loop_.load(std::memory_order_relaxed);

    const int numChannels = std::min(buffer.getNumChannels(),
                                     currentSample_->buffer.getNumChannels());

    double pos = playPosition_.load(std::memory_order_relaxed);

    for (int sample = 0; sample < numSamples; ++sample) {
        if (pos >= endPos) {
            if (shouldLoop) {
                pos = startPoint_.load(std::memory_order_relaxed) * length;
            } else {
                isPlaying_.store(false);
                break;
            }
        }

        // Linear interpolation for smooth pitch shifting
        const int idx0 = static_cast<int>(pos);
        int idx1 = idx0 + 1;
        const float frac = static_cast<float>(pos - idx0);

        if (idx1 >= length) {
            idx1 = idx0;
        }

        for (int ch = 0; ch < numChannels; ++ch) {
            const float s0 = currentSample_->buffer.getSample(ch, idx0);
            const float s1 = currentSample_->buffer.getSample(ch, idx1);
            const float interpolated = s0 + frac * (s1 - s0);

            // Mix with existing audio
            buffer.addSample(ch, sample, interpolated * vol);
        }

        pos += pitchRatio;
    }

    playPosition_.store(pos, std::memory_order_relaxed);
    applyFade(buffer, numSamples);
}

void SamplerNode::setParameter(const std::string& name, float value) {
    if (name == "volume" || name == "gain") {
        volume_.store(std::clamp(value, 0.0f, 2.0f));
    }
    else if (name == "pitch") {
        pitch_.store(std::clamp(value, -24.0f, 24.0f));
    }
    else if (name == "start" || name == "start_point") {
        startPoint_.store(std::clamp(value, 0.0f, 1.0f));
    }
    else if (name == "end" || name == "end_point") {
        endPoint_.store(std::clamp(value, 0.0f, 1.0f));
    }
    else if (name == "loop") {
        loop_.store(value > 0.5f);
    }
    else if (name == "one_shot") {
        oneShot_.store(value > 0.5f);
    }
    else if (name == "trigger") {
        const float prev = lastTriggerValue_.load();
        if (value > 0.5f && prev <= 0.5f) {
            trigger();
        } else if (value <= 0.5f && prev > 0.5f) {
            if (!oneShot_.load()) {
                stop();
            }
        }
        lastTriggerValue_.store(value);
    }
    else if (name == "stop") {
        if (value > 0.5f) stop();
    }
}

float SamplerNode::getParameter(const std::string& name) const {
    if (name == "volume" || name == "gain") return volume_.load();
    if (name == "pitch") return pitch_.load();
    if (name == "start" || name == "start_point") return startPoint_.load();
    if (name == "end" || name == "end_point") return endPoint_.load();
    if (name == "loop") return loop_.load() ? 1.0f : 0.0f;
    if (name == "one_shot") return oneShot_.load() ? 1.0f : 0.0f;
    if (name == "is_playing") return isPlaying_.load() ? 1.0f : 0.0f;
    if (name == "position") {
        const int length = currentLength_.load();
        if (length > 0) {
            return static_cast<float>(playPosition_.load() / length);
        }
        return 0.0f;
    }
    return 0.0f;
}
