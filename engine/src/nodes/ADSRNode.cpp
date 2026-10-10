#include "ADSRNode.h"
#include <algorithm>

ADSRNode::ADSRNode(const std::string& id)
    : BaseNode(id)
{
}

void ADSRNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    sampleRate_ = sampleRate;
    adsr_.setSampleRate(sampleRate);
    paramsDirty_.store(true, std::memory_order_release);
}

void ADSRNode::reset() {
    BaseNode::reset();
    adsr_.reset();
    gateOn_.store(false);
    lastGateValue_.store(0.0f);
    pendingGate_.store(-1);
    currentEnvelope_.store(0.0f);
    lastGateControl_ = 0.0f;
}

int ADSRNode::resolvePortIndex(const std::string& portName) const {
    if (portName == "input" || portName == "audio_in") return PORT_AUDIO;
    if (portName == "gate") return PORT_GATE;
    return -1;
}

void ADSRNode::updateADSRParams() {
    adsrParams_.attack = attack_.load();
    adsrParams_.decay = decay_.load();
    adsrParams_.sustain = sustain_.load();
    adsrParams_.release = release_.load();
    adsr_.setParameters(adsrParams_);
}

void ADSRNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Pull audio input (if connected)
    pullInput(PORT_AUDIO, buffer, numSamples);

    // Muted (bypassed): audio passes through, envelope does not apply.
    if (isBypassed()) {
        return;
    }

    // Apply staged parameter changes on the audio thread (juce::ADSR is
    // not safe to reconfigure concurrently with getNextSample()).
    if (paramsDirty_.exchange(false, std::memory_order_acq_rel)) {
        updateADSRParams();
    }

    // Gate edges staged from the command thread (set_param "gate")
    const int staged = pendingGate_.exchange(-1, std::memory_order_acq_rel);
    if (staged == 1) {
        adsr_.noteOn();
        gateOn_.store(true);
    } else if (staged == 0) {
        adsr_.noteOff();
        gateOn_.store(false);
    }

    // Gate edges from a connected control input (e.g. sequencer lane)
    if (BaseNode* gateInput = getInput(PORT_GATE)) {
        const float gateVal = gateInput->getControlOutput(getInputLane(PORT_GATE));
        if (gateVal > 0.5f && lastGateControl_ <= 0.5f) {
            adsr_.noteOn();
            gateOn_.store(true);
        } else if (gateVal <= 0.5f && lastGateControl_ > 0.5f) {
            adsr_.noteOff();
            gateOn_.store(false);
        }
        lastGateControl_ = gateVal;
    }

    const bool applyToAudio = applyToAudio_.load();
    const int numChannels = buffer.getNumChannels();

    float envValue = currentEnvelope_.load(std::memory_order_relaxed);
    for (int sample = 0; sample < numSamples; ++sample) {
        envValue = adsr_.getNextSample();

        if (applyToAudio) {
            for (int ch = 0; ch < numChannels; ++ch) {
                float* channelData = buffer.getWritePointer(ch);
                channelData[sample] *= envValue;
            }
        }
    }
    // Publish once per block - readers only need the latest value.
    currentEnvelope_.store(envValue, std::memory_order_release);

    applyFade(buffer, numSamples);
}

void ADSRNode::setParameter(const std::string& name, float value) {
    if (name == "attack") {
        attack_.store(std::clamp(value / 1000.0f, 0.001f, 10.0f));
        paramsDirty_.store(true, std::memory_order_release);
    }
    else if (name == "decay") {
        decay_.store(std::clamp(value / 1000.0f, 0.001f, 10.0f));
        paramsDirty_.store(true, std::memory_order_release);
    }
    else if (name == "sustain") {
        sustain_.store(std::clamp(value, 0.0f, 1.0f));
        paramsDirty_.store(true, std::memory_order_release);
    }
    else if (name == "release") {
        release_.store(std::clamp(value / 1000.0f, 0.001f, 20.0f));
        paramsDirty_.store(true, std::memory_order_release);
    }
    else if (name == "gate") {
        const float prev = lastGateValue_.load();
        if (value > 0.5f && prev <= 0.5f) {
            pendingGate_.store(1, std::memory_order_release);
        } else if (value <= 0.5f && prev > 0.5f) {
            pendingGate_.store(0, std::memory_order_release);
        }
        lastGateValue_.store(value);
    }
    else if (name == "apply_to_audio") {
        applyToAudio_.store(value > 0.5f);
    }
}

float ADSRNode::getParameter(const std::string& name) const {
    if (name == "attack") return attack_.load() * 1000.0f;
    if (name == "decay") return decay_.load() * 1000.0f;
    if (name == "sustain") return sustain_.load();
    if (name == "release") return release_.load() * 1000.0f;
    if (name == "envelope" || name == "value") return currentEnvelope_.load();
    if (name == "gate") return gateOn_.load() ? 1.0f : 0.0f;
    if (name == "apply_to_audio") return applyToAudio_.load() ? 1.0f : 0.0f;
    return 0.0f;
}
