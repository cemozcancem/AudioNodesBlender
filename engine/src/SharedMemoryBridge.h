#pragma once

#include "common/SharedTypes.h"
#include <juce_core/juce_core.h>
#include <atomic>
#include <memory>

class NodeManager;
class MIDIManager;

// Shared memory bridge for sending visualization data to Blender.
//
// update() runs on the audio thread: it is lock-free (all node/MIDI state
// it reads is atomic or audio-thread owned), throttled to ~120 writes/sec,
// and publishes through a proper odd/even seqlock so the Python reader can
// reliably detect torn reads.
class SharedMemoryBridge {
public:
    SharedMemoryBridge();
    ~SharedMemoryBridge();

    // Initialize shared memory for the engine listening on `zmqPort`.
    // The mapping name depends on the port (sharedMemoryNameForPort in
    // SharedTypes.h). Message thread, once at startup.
    bool initialize(int zmqPort = DEFAULT_ZMQ_PORT);

    // Cleanup shared memory
    void cleanup();

    // Update visualization data. Audio thread - lock-free.
    // inputPeaks: per-input-channel block peaks (may be nullptr / 0 chans).
    void update(NodeManager* nodeManager, MIDIManager* midiManager,
                int numSamples, double sampleRate,
                const float* inputPeaks = nullptr, int numInputChannels = 0);

    // Check if shared memory is active
    bool isActive() const { return sharedMemory_ != nullptr; }

private:
    void* sharedMemory_ = nullptr;
    AudioVisualizationData* data_ = nullptr;

    // Seqlock counter (always even between writes). Audio thread only.
    uint32_t writeSequence_ = 0;

    // Throttle: accumulate samples, write at most ~120 times per second.
    double samplesSinceWrite_ = 0.0;

    // Max input peak per channel since the last publish, so short
    // transients between throttled writes are not lost. Audio thread only.
    float pendingInputPeaks_[32] = {};
    int pendingInputChannels_ = 0;

    // Platform-specific handle
#if JUCE_MAC || JUCE_LINUX  // POSIX: file-backed mmap in /tmp
    int shmFd_ = -1;
#elif JUCE_WINDOWS
    void* hMapFile_ = nullptr;
#endif

    // Staging buffer assembled before the seqlocked copy
    AudioVisualizationData writeBuffer_;
};
