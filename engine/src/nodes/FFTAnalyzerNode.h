#pragma once

#include "BaseNode.h"
#include "common/SharedTypes.h"
#include <array>
#include <atomic>
#include <thread>

// FFT Analyzer Node - Computes spectrum data for visualization.
//
// The FFT itself runs on a dedicated worker thread: the audio thread only
// fills a FIFO and, when a window is complete, memcpys it into a staging
// buffer and raises a flag. Results are published through a double buffer
// with an atomic index, so the audio thread (which forwards them to shared
// memory) never sees a half-written spectrum and never pays for the FFT.
class FFTAnalyzerNode : public BaseNode {
public:
    // Time-domain waveform points kept per channel for visualization.
    static constexpr int WAVEFORM_POINTS = 1024;

    explicit FFTAnalyzerNode(const std::string& id);
    ~FFTAnalyzerNode() override;

    void process(juce::AudioBuffer<float>& buffer, int numSamples) override;
    void prepare(double sampleRate, int maxBlockSize) override;
    void reset() override;
    void setParameter(const std::string& name, float value) override;
    float getParameter(const std::string& name) const override;

    // Get the computed FFT data for shared memory (audio thread).
    const std::array<float, FFT_OUTPUT_SIZE>& getFFTLeft() const {
        return fftDataLeft_[activeBuffer_.load(std::memory_order_acquire)];
    }
    const std::array<float, FFT_OUTPUT_SIZE>& getFFTRight() const {
        return fftDataRight_[activeBuffer_.load(std::memory_order_acquire)];
    }
    float getPeakLeft() const { return peakLeft_.load(std::memory_order_acquire); }
    float getPeakRight() const { return peakRight_.load(std::memory_order_acquire); }
    float getRMSLeft() const { return rmsLeft_.load(std::memory_order_acquire); }
    float getRMSRight() const { return rmsRight_.load(std::memory_order_acquire); }

    // Time-domain waveform rings (written and read on the audio thread).
    const std::array<float, WAVEFORM_POINTS>& getWaveformLeft() const { return waveformLeft_; }
    const std::array<float, WAVEFORM_POINTS>& getWaveformRight() const { return waveformRight_; }
    int getWaveformWriteIndex() const { return waveformIndex_; }

private:
    static constexpr int FFT_ORDER = 11; // 2^11 = 2048

    // Clear FIFO/waveform staging (does not touch graph connections).
    void clearAnalysisState();

    juce::dsp::FFT fft_;                                  // worker thread only
    juce::dsp::WindowingFunction<float> window_;          // worker thread only

    // FIFO filled by the audio thread
    std::array<float, FFT_SIZE> fifoLeft_;
    std::array<float, FFT_SIZE> fifoRight_;
    int fifoIndex_ = 0;

    // Staging buffers: audio thread writes when fftPending_ is false,
    // worker thread reads when it is true.
    std::array<float, FFT_SIZE> stagingLeft_;
    std::array<float, FFT_SIZE> stagingRight_;
    std::atomic<bool> fftPending_{false};

    // Worker-local scratch
    std::array<float, FFT_SIZE * 2> fftOutputLeft_;
    std::array<float, FFT_SIZE * 2> fftOutputRight_;

    // Double-buffered magnitude output
    std::array<std::array<float, FFT_OUTPUT_SIZE>, 2> fftDataLeft_;
    std::array<std::array<float, FFT_OUTPUT_SIZE>, 2> fftDataRight_;
    std::atomic<int> activeBuffer_{0};

    // Peak and RMS values
    std::atomic<float> peakLeft_{0.0f};
    std::atomic<float> peakRight_{0.0f};
    std::atomic<float> rmsLeft_{0.0f};
    std::atomic<float> rmsRight_{0.0f};

    // Time-domain waveform rings (audio thread)
    std::array<float, WAVEFORM_POINTS> waveformLeft_;
    std::array<float, WAVEFORM_POINTS> waveformRight_;
    int waveformIndex_ = 0;

    // Worker thread
    std::thread worker_;
    std::atomic<bool> workerRunning_{false};

    void workerLoop();
    void computeFFT();   // worker thread
};
