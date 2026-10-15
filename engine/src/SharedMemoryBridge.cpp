#include "SharedMemoryBridge.h"
#include "NodeManager.h"
#include "MIDIManager.h"
#include "nodes/FFTAnalyzerNode.h"
#include "nodes/SequencerNode.h"
#include "nodes/BangNode.h"
#include <algorithm>
#include <cstring>

#if JUCE_MAC || JUCE_LINUX  // POSIX: file-backed mmap in /tmp
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
#elif JUCE_WINDOWS
// <windows.h> defines min/max as MACROS, which turns every std::min(...) in
// this file into std::(...) and produces a wall of C2589/C2059/C2737 errors.
// NOMINMAX suppresses them; WIN32_LEAN_AND_MEAN trims the rest of the header.
// Both are also set in CMakeLists for the whole target - kept here too so the
// file is correct no matter how it gets compiled.
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

SharedMemoryBridge::SharedMemoryBridge() {
    std::memset(&writeBuffer_, 0, sizeof(writeBuffer_));
}

SharedMemoryBridge::~SharedMemoryBridge() {
    cleanup();
}

bool SharedMemoryBridge::initialize() {
#if JUCE_MAC || JUCE_LINUX  // POSIX: file-backed mmap in /tmp
    // Create or open shared memory
    // Use /tmp/ path for file-based shared memory (simpler cross-process compatibility)
    const char* shmPath = "/tmp/AudioNodesVisualization";

    shmFd_ = open(shmPath, O_RDWR | O_CREAT, 0666);
    if (shmFd_ < 0) {
        return false;
    }

    // Set size
    if (ftruncate(shmFd_, SHARED_MEMORY_SIZE) < 0) {
        close(shmFd_);
        shmFd_ = -1;
        return false;
    }

    // Map memory
    sharedMemory_ = mmap(nullptr, SHARED_MEMORY_SIZE, PROT_READ | PROT_WRITE,
                          MAP_SHARED, shmFd_, 0);

    if (sharedMemory_ == MAP_FAILED) {
        close(shmFd_);
        shmFd_ = -1;
        sharedMemory_ = nullptr;
        return false;
    }

    data_ = static_cast<AudioVisualizationData*>(sharedMemory_);
    std::memset(data_, 0, sizeof(AudioVisualizationData));

    return true;

#elif JUCE_WINDOWS
    // Create named shared memory on Windows
    hMapFile_ = CreateFileMappingA(
        INVALID_HANDLE_VALUE,
        nullptr,
        PAGE_READWRITE,
        0,
        SHARED_MEMORY_SIZE,
        SHARED_MEMORY_NAME
    );

    if (hMapFile_ == nullptr) {
        return false;
    }

    sharedMemory_ = MapViewOfFile(
        hMapFile_,
        FILE_MAP_ALL_ACCESS,
        0,
        0,
        SHARED_MEMORY_SIZE
    );

    if (sharedMemory_ == nullptr) {
        CloseHandle(hMapFile_);
        hMapFile_ = nullptr;
        return false;
    }

    data_ = static_cast<AudioVisualizationData*>(sharedMemory_);
    std::memset(data_, 0, sizeof(AudioVisualizationData));

    return true;
#else
    return false;
#endif
}

void SharedMemoryBridge::cleanup() {
#if JUCE_MAC || JUCE_LINUX  // POSIX: file-backed mmap in /tmp
    if (sharedMemory_ != nullptr) {
        munmap(sharedMemory_, SHARED_MEMORY_SIZE);
        sharedMemory_ = nullptr;
    }
    if (shmFd_ >= 0) {
        close(shmFd_);
        shmFd_ = -1;
    }
#elif JUCE_WINDOWS
    if (sharedMemory_ != nullptr) {
        UnmapViewOfFile(sharedMemory_);
        sharedMemory_ = nullptr;
    }
    if (hMapFile_ != nullptr) {
        CloseHandle(hMapFile_);
        hMapFile_ = nullptr;
    }
#endif
    data_ = nullptr;
}

void SharedMemoryBridge::update(NodeManager* nodeManager, MIDIManager* midiManager,
                                int numSamples, double sampleRate,
                                const float* inputPeaks, int numInputChannels) {
    if (data_ == nullptr) {
        return;
    }

    // Accumulate per-channel input peaks every block (max-hold), even on
    // throttled-out calls, so transients between publishes still register
    // on the channel meters.
    if (inputPeaks != nullptr && numInputChannels > 0) {
        const int n = std::min(numInputChannels, MAX_INPUT_CHANNELS);
        for (int ch = 0; ch < n; ++ch) {
            pendingInputPeaks_[ch] = std::max(pendingInputPeaks_[ch], inputPeaks[ch]);
        }
        pendingInputChannels_ = n;
    }

    // Throttle: the Python reader runs at 60fps; writing every audio block
    // (up to ~350/sec) is wasted work in the callback. ~120 writes/sec
    // keeps the reader fresh at half the cost.
    samplesSinceWrite_ += numSamples;
    const double minInterval = sampleRate > 0.0 ? sampleRate / 120.0 : 367.0;
    if (samplesSinceWrite_ < minInterval) {
        return;
    }
    samplesSinceWrite_ = 0.0;

    // ---- Assemble the payload in the staging buffer (audio thread) --------

    if (nodeManager != nullptr) {
        // Per-analyzer slots: every live FFT Analyzer node publishes its
        // own spectrum, keyed by node id (first MAX_FFT_ANALYZERS in
        // creation order). Copying fixed-size arrays + the id chars only -
        // no allocation, no locks.
        const int numAnalyzers = std::min(nodeManager->getNumFFTAnalyzersAudio(),
                                          MAX_FFT_ANALYZERS);
        writeBuffer_.num_fft_analyzers = static_cast<uint32_t>(numAnalyzers);
        for (int i = 0; i < MAX_FFT_ANALYZERS; ++i) {
            FFTAnalyzerSlot& slot = writeBuffer_.fft_slots[i];
            FFTAnalyzerNode* analyzer =
                (i < numAnalyzers) ? nodeManager->getFFTAnalyzerAudio(i) : nullptr;
            if (analyzer == nullptr) {
                slot.node_id[0] = '\0';
                continue;
            }
            const std::string& id = analyzer->getId();  // fixed after creation
            const size_t idLen = std::min(id.size(), sizeof(slot.node_id) - 1);
            std::memcpy(slot.node_id, id.data(), idLen);
            slot.node_id[idLen] = '\0';
            std::memcpy(slot.fft_left, analyzer->getFFTLeft().data(),
                        sizeof(float) * FFT_OUTPUT_SIZE);
            std::memcpy(slot.fft_right, analyzer->getFFTRight().data(),
                        sizeof(float) * FFT_OUTPUT_SIZE);
            slot.peak_left = analyzer->getPeakLeft();
            slot.peak_right = analyzer->getPeakRight();
            slot.rms_left = analyzer->getRMSLeft();
            slot.rms_right = analyzer->getRMSRight();
        }

        // Legacy top-level fields mirror the OLDEST analyzer (slot 0).
        // Audio-thread owned pointer - no locks.
        FFTAnalyzerNode* fftAnalyzer = nodeManager->getFFTAnalyzerAudio();

        if (fftAnalyzer != nullptr) {
            const auto& fftLeft = fftAnalyzer->getFFTLeft();
            const auto& fftRight = fftAnalyzer->getFFTRight();

            std::memcpy(writeBuffer_.fft_left, fftLeft.data(),
                        sizeof(float) * FFT_OUTPUT_SIZE);
            std::memcpy(writeBuffer_.fft_right, fftRight.data(),
                        sizeof(float) * FFT_OUTPUT_SIZE);

            writeBuffer_.peak_left = fftAnalyzer->getPeakLeft();
            writeBuffer_.peak_right = fftAnalyzer->getPeakRight();
            writeBuffer_.rms_left = fftAnalyzer->getRMSLeft();
            writeBuffer_.rms_right = fftAnalyzer->getRMSRight();

            // Time-domain waveform, interleaved L/R, oldest sample first.
            const auto& waveL = fftAnalyzer->getWaveformLeft();
            const auto& waveR = fftAnalyzer->getWaveformRight();
            const int start = fftAnalyzer->getWaveformWriteIndex();
            for (int i = 0; i < WAVEFORM_CHANNEL; ++i) {
                const int src = (start + i) % WAVEFORM_CHANNEL;
                writeBuffer_.waveform[i * 2] = waveL[static_cast<size_t>(src)];
                writeBuffer_.waveform[i * 2 + 1] = waveR[static_cast<size_t>(src)];
            }
        }
    }

    // Copy MIDI data from MIDIManager (atomics)
    if (midiManager != nullptr) {
        const auto& state = midiManager->getState();
        writeBuffer_.midi.last_note = state.lastNote.load(std::memory_order_relaxed);
        writeBuffer_.midi.last_velocity = state.lastVelocity.load(std::memory_order_relaxed);
        writeBuffer_.midi.note_gate = state.noteGate.load(std::memory_order_relaxed);
        writeBuffer_.midi.bpm = state.bpm.load(std::memory_order_relaxed);
        writeBuffer_.midi.tick_position = state.tickPosition.load(std::memory_order_relaxed);
        writeBuffer_.midi.transport_playing = state.transportPlaying.load(std::memory_order_relaxed);

        for (int i = 0; i < 128; ++i) {
            writeBuffer_.midi.cc_values[i] = state.ccValues[i].load(std::memory_order_relaxed);
        }
    }

    // Per-sequencer slots: every live Step Sequencer publishes its own
    // transport, keyed by node id (first MAX_PUBLISHED_SEQUENCERS in
    // creation order). Same shape as the FFT analyzer slots above - fixed
    // arrays and id chars only, no allocation, no locks.
    if (nodeManager != nullptr) {
        const int numSeq = std::min(nodeManager->getNumSequencersAudio(),
                                    MAX_PUBLISHED_SEQUENCERS);
        writeBuffer_.num_sequencers = static_cast<uint32_t>(numSeq);
        for (int i = 0; i < MAX_PUBLISHED_SEQUENCERS; ++i) {
            SequencerSlot& slot = writeBuffer_.sequencer_slots[i];
            SequencerNode* seq =
                (i < numSeq) ? nodeManager->getSequencerAudio(i) : nullptr;
            if (seq == nullptr) {
                slot.node_id[0] = '\0';
                continue;
            }
            const std::string& id = seq->getId();  // fixed after creation
            const size_t idLen = std::min(id.size(), sizeof(slot.node_id) - 1);
            std::memcpy(slot.node_id, id.data(), idLen);
            slot.node_id[idLen] = '\0';
            slot.current_step = seq->getCurrentStepForShm();
            slot.is_playing = seq->getIsPlayingForShm();
            slot.trigger_active = seq->getTriggerActiveForShm();
            slot.bpm = seq->getBpmForShm();
            slot.step_count = seq->getStepCountForShm();
            slot.step_pattern = seq->getStepPatternForShm();
        }
    } else {
        writeBuffer_.num_sequencers = 0;
        for (int i = 0; i < MAX_PUBLISHED_SEQUENCERS; ++i) {
            writeBuffer_.sequencer_slots[i].node_id[0] = '\0';
        }
    }

    // Per-control-source slots: Bang and ADSR outputs, keyed by node id.
    // These are control signals with no audio path, so nothing else in
    // shared memory carries them - and without them every Blender-side
    // consumer downstream of a Bang or an ADSR reads a constant zero.
    if (nodeManager != nullptr) {
        const int numControl = std::min(nodeManager->getNumControlNodesAudio(),
                                        MAX_PUBLISHED_CONTROL_NODES);
        writeBuffer_.num_control_nodes = static_cast<uint32_t>(numControl);
        for (int i = 0; i < MAX_PUBLISHED_CONTROL_NODES; ++i) {
            ControlSlot& slot = writeBuffer_.control_slots[i];
            BaseNode* node = (i < numControl) ? nodeManager->getControlNodeAudio(i)
                                              : nullptr;
            if (node == nullptr) {
                slot.node_id[0] = '\0';
                slot.pulse_count = 0;
                slot.flags = 0;
                for (int lane = 0; lane < 4; ++lane) slot.values[lane] = 0.0f;
                continue;
            }
            const std::string& id = node->getId();  // fixed after creation
            const size_t idLen = std::min(id.size(), sizeof(slot.node_id) - 1);
            std::memcpy(slot.node_id, id.data(), idLen);
            slot.node_id[idLen] = '\0';
            for (int lane = 0; lane < 4; ++lane) {
                slot.values[lane] = node->getControlOutput(lane);
            }
            slot.flags = (slot.values[0] > 0.5f) ? 1u : 0u;
            // A Bang's pulse COUNT is what makes a repeat press visible to
            // Blender: two pulses 20ms apart can both fall between two
            // 60fps reads, and a level alone would show nothing happened.
            slot.pulse_count = 0;
            if (nodeManager->getControlNodeKindAudio(i) == NodeKind::Bang) {
                slot.pulse_count =
                    static_cast<BangNode*>(node)->getPulseCountForShm();
            }
        }
    } else {
        writeBuffer_.num_control_nodes = 0;
        for (int i = 0; i < MAX_PUBLISHED_CONTROL_NODES; ++i) {
            writeBuffer_.control_slots[i].node_id[0] = '\0';
        }
    }

    // Legacy single-sequencer field mirrors the PRIMARY (oldest) sequencer.
    if (nodeManager != nullptr) {
        SequencerNode* sequencer = nodeManager->getPrimarySequencerAudio();
        if (sequencer != nullptr) {
            writeBuffer_.sequencer.current_step = sequencer->getCurrentStepForShm();
            writeBuffer_.sequencer.is_playing = sequencer->getIsPlayingForShm();
            writeBuffer_.sequencer.trigger_active = sequencer->getTriggerActiveForShm();
            writeBuffer_.sequencer.bpm = sequencer->getBpmForShm();
            writeBuffer_.sequencer.step_count = sequencer->getStepCountForShm();
            writeBuffer_.sequencer.step_pattern = sequencer->getStepPatternForShm();
        } else {
            writeBuffer_.sequencer = {};
        }
    } else {
        writeBuffer_.sequencer = {};
    }

    writeBuffer_.sample_rate = static_cast<float>(sampleRate);

    // Publish + reset the per-channel input peak hold.
    writeBuffer_.num_input_channels = static_cast<uint32_t>(pendingInputChannels_);
    std::memcpy(writeBuffer_.input_peaks, pendingInputPeaks_,
                sizeof(float) * MAX_INPUT_CHANNELS);
    std::memset(pendingInputPeaks_, 0, sizeof(pendingInputPeaks_));

    // ---- Publish via seqlock ------------------------------------------------
    // Odd sequence = write in progress. Readers must see the same EVEN
    // value before and after copying the payload to accept it.
    auto* seqPtr = reinterpret_cast<std::atomic<uint32_t>*>(&data_->sequence);

    seqPtr->store(writeSequence_ + 1, std::memory_order_release);   // odd

    // Copy payload only (skip the sequence field at offset 0).
    std::memcpy(reinterpret_cast<char*>(data_) + sizeof(uint32_t),
                reinterpret_cast<const char*>(&writeBuffer_) + sizeof(uint32_t),
                sizeof(AudioVisualizationData) - sizeof(uint32_t));

    writeSequence_ += 2;
    seqPtr->store(writeSequence_, std::memory_order_release);        // even
}
