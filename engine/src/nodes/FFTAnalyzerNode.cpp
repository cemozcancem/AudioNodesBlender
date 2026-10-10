#include "FFTAnalyzerNode.h"
#include <chrono>
#include <cmath>
#include <cstring>

FFTAnalyzerNode::FFTAnalyzerNode(const std::string& id)
    : BaseNode(id)
    , fft_(FFT_ORDER)
    , window_(FFT_SIZE, juce::dsp::WindowingFunction<float>::hann)
{
    fifoLeft_.fill(0.0f);
    fifoRight_.fill(0.0f);
    stagingLeft_.fill(0.0f);
    stagingRight_.fill(0.0f);
    fftOutputLeft_.fill(0.0f);
    fftOutputRight_.fill(0.0f);
    for (auto& buf : fftDataLeft_) buf.fill(0.0f);
    for (auto& buf : fftDataRight_) buf.fill(0.0f);
    waveformLeft_.fill(0.0f);
    waveformRight_.fill(0.0f);

    workerRunning_.store(true, std::memory_order_release);
    worker_ = std::thread(&FFTAnalyzerNode::workerLoop, this);
}

FFTAnalyzerNode::~FFTAnalyzerNode() {
    workerRunning_.store(false, std::memory_order_release);
    if (worker_.joinable()) {
        worker_.join();
    }
}

void FFTAnalyzerNode::prepare(double sampleRate, int maxBlockSize) {
    BaseNode::prepare(sampleRate, maxBlockSize);
    // Only clear the analysis state here - do NOT call reset().
    // BaseNode::reset() wipes the graph input pointers, and prepare()
    // re-runs on every live device switch (audioDeviceAboutToStart ->
    // NodeManager::prepare). Clearing the edges there permanently
    // silenced the analyzer (and everything downstream of it) until the
    // engine was restarted and Blender re-sent the connections. That was
    // the real cause of "device switching needs an engine restart".
    clearAnalysisState();
}

void FFTAnalyzerNode::reset() {
    BaseNode::reset();
    clearAnalysisState();
}

void FFTAnalyzerNode::clearAnalysisState() {
    // Wait for any in-flight FFT so the worker is not reading the staging
    // buffers while we clear them. Called with the audio callback stopped;
    // bounded by one FFT computation (well under a millisecond).
    while (fftPending_.load(std::memory_order_acquire)) {
        std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
    fifoIndex_ = 0;
    waveformIndex_ = 0;
    fifoLeft_.fill(0.0f);
    fifoRight_.fill(0.0f);
    waveformLeft_.fill(0.0f);
    waveformRight_.fill(0.0f);
}

void FFTAnalyzerNode::process(juce::AudioBuffer<float>& buffer, int numSamples) {
    // Pull input from connected node - the analyzer is pass-through.
    pullInput(0, buffer, numSamples);

    // Muted (bypassed): audio passes through, analysis freezes.
    if (isBypassed()) {
        return;
    }

    const int numChannels = buffer.getNumChannels();

    float maxLeft = 0.0f, maxRight = 0.0f;
    float sumSqLeft = 0.0f, sumSqRight = 0.0f;

    for (int sample = 0; sample < numSamples; ++sample) {
        const float left = numChannels > 0 ? buffer.getSample(0, sample) : 0.0f;
        const float right = numChannels > 1 ? buffer.getSample(1, sample) : left;

        // Peak detection
        maxLeft = std::max(maxLeft, std::abs(left));
        maxRight = std::max(maxRight, std::abs(right));

        // RMS accumulation
        sumSqLeft += left * left;
        sumSqRight += right * right;

        // Waveform ring for visualization
        waveformLeft_[static_cast<size_t>(waveformIndex_)] = left;
        waveformRight_[static_cast<size_t>(waveformIndex_)] = right;
        waveformIndex_ = (waveformIndex_ + 1) % WAVEFORM_POINTS;

        // Fill FIFO for FFT
        fifoLeft_[static_cast<size_t>(fifoIndex_)] = left;
        fifoRight_[static_cast<size_t>(fifoIndex_)] = right;
        fifoIndex_++;

        if (fifoIndex_ >= FFT_SIZE) {
            fifoIndex_ = 0;

            // Hand the window to the worker thread. If it is still busy
            // with the previous window, drop this one - visualization
            // never justifies blocking the audio thread.
            if (!fftPending_.load(std::memory_order_acquire)) {
                std::memcpy(stagingLeft_.data(), fifoLeft_.data(), sizeof(float) * FFT_SIZE);
                std::memcpy(stagingRight_.data(), fifoRight_.data(), sizeof(float) * FFT_SIZE);
                fftPending_.store(true, std::memory_order_release);
            }
        }
    }

    peakLeft_.store(maxLeft, std::memory_order_release);
    peakRight_.store(maxRight, std::memory_order_release);
    if (numSamples > 0) {
        rmsLeft_.store(std::sqrt(sumSqLeft / static_cast<float>(numSamples)), std::memory_order_release);
        rmsRight_.store(std::sqrt(sumSqRight / static_cast<float>(numSamples)), std::memory_order_release);
    }
}

void FFTAnalyzerNode::workerLoop() {
    while (workerRunning_.load(std::memory_order_acquire)) {
        if (fftPending_.load(std::memory_order_acquire)) {
            computeFFT();
            fftPending_.store(false, std::memory_order_release);
        } else {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    }
}

void FFTAnalyzerNode::computeFFT() {
    // Copy staged input into the (zero-padded) transform buffers.
    std::memcpy(fftOutputLeft_.data(), stagingLeft_.data(), sizeof(float) * FFT_SIZE);
    std::memcpy(fftOutputRight_.data(), stagingRight_.data(), sizeof(float) * FFT_SIZE);
    std::fill(fftOutputLeft_.begin() + FFT_SIZE, fftOutputLeft_.end(), 0.0f);
    std::fill(fftOutputRight_.begin() + FFT_SIZE, fftOutputRight_.end(), 0.0f);

    // Apply window
    window_.multiplyWithWindowingTable(fftOutputLeft_.data(), FFT_SIZE);
    window_.multiplyWithWindowingTable(fftOutputRight_.data(), FFT_SIZE);

    // Perform FFT
    fft_.performRealOnlyForwardTransform(fftOutputLeft_.data());
    fft_.performRealOnlyForwardTransform(fftOutputRight_.data());

    // Write magnitudes into the inactive buffer, then flip.
    const int inactive = 1 - activeBuffer_.load(std::memory_order_relaxed);
    auto& outLeft = fftDataLeft_[static_cast<size_t>(inactive)];
    auto& outRight = fftDataRight_[static_cast<size_t>(inactive)];

    const float scale = 1.0f / static_cast<float>(FFT_SIZE);

    for (int i = 0; i < FFT_OUTPUT_SIZE; ++i) {
        const float realL = fftOutputLeft_[static_cast<size_t>(i * 2)];
        const float imagL = fftOutputLeft_[static_cast<size_t>(i * 2 + 1)];
        const float realR = fftOutputRight_[static_cast<size_t>(i * 2)];
        const float imagR = fftOutputRight_[static_cast<size_t>(i * 2 + 1)];

        const float magL = std::sqrt(realL * realL + imagL * imagL) * scale;
        const float magR = std::sqrt(realR * realR + imagR * imagR) * scale;

        outLeft[static_cast<size_t>(i)] = std::clamp(magL * 4.0f, 0.0f, 1.0f);
        outRight[static_cast<size_t>(i)] = std::clamp(magR * 4.0f, 0.0f, 1.0f);
    }

    activeBuffer_.store(inactive, std::memory_order_release);
}

void FFTAnalyzerNode::setParameter(const std::string& name, float value) {
    (void)name;
    (void)value;
}

float FFTAnalyzerNode::getParameter(const std::string& name) const {
    (void)name;
    return 0.0f;
}
