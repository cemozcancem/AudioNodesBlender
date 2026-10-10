#pragma once

#include <juce_audio_basics/juce_audio_basics.h>
#include <memory>
#include <string>
#include <unordered_map>
#include <utility>
#include <mutex>
#include <vector>
#include <cstdint>
#include <type_traits>

#include "nodes/BaseNode.h"
#include "common/LockFreeQueue.h"

class MIDIManager;
class InputNode;
class FFTAnalyzerNode;
class SequencerNode;

// Classification used by the audio thread for special handling.
enum class NodeKind : uint8_t {
    Generic = 0,
    Input,
    Output,
    FFTAnalyzer,
    Sequencer,
    MIDIInput,
    MIDIOutput,
    ADSR,
    Bang,
};

// POD command passed from the command thread to the audio thread.
// Trivially copyable: no strings, no allocation on either side of the queue.
struct GraphCommand {
    enum class Type : uint8_t {
        AddNode,
        RemoveNode,
        Connect,
        Disconnect,
    };

    Type type = Type::AddNode;
    NodeKind kind = NodeKind::Generic;
    BaseNode* node = nullptr;    // AddNode/RemoveNode target, dest for (dis)connect
    BaseNode* source = nullptr;  // Connect source
    int16_t portIndex = 0;
    int16_t sourceLane = 0;
};

static_assert(std::is_trivially_copyable<GraphCommand>::value,
              "GraphCommand must be POD - it crosses the lock-free queue");

// Manages the DSP node graph.
//
// Threading model:
//   - Command thread (ZMQ server): owns nodes_ (id -> unique_ptr) and
//     connections_. Creates nodes, calls prepare(), resolves port names,
//     then hands POD pointers to the audio thread via commandQueue_.
//   - Audio thread: owns the live graph (audioNodes_, special pointers,
//     each node's input arrays). Pops commands at block start. Never
//     allocates, locks, throws, or touches strings.
//   - Deleted nodes fade out on the audio thread, get unlinked from every
//     remaining node (UAF protection), then travel back through
//     garbageQueue_ so their destructors run on the command thread.
//   - nodesMutex_ guards command-side maps only. The audio callback never
//     takes it (prepare() runs while the callback is stopped).
class NodeManager {
public:
    static constexpr size_t MAX_NODES = 1024;

    NodeManager();
    ~NodeManager();

    // Prepare for playback. Called from the device thread while the audio
    // callback is not running.
    void prepare(double sampleRate, int maxBlockSize);

    // Main processing function - audio thread.
    void process(const juce::AudioBuffer<float>& input,
                 juce::AudioBuffer<float>& output,
                 int numSamples);

    // ---- Command thread API -------------------------------------------------

    bool createNode(const std::string& nodeId, const std::string& nodeType);
    bool deleteNode(const std::string& nodeId);
    bool connect(const std::string& sourceNodeId, const std::string& destNodeId,
                 const std::string& sourcePort, const std::string& destPort);
    bool disconnect(const std::string& destNodeId, const std::string& destPort);

    // Direct parameter set/get. Nodes only touch atomics here.
    void setParameter(const std::string& nodeId, const std::string& paramName, float value);

    bool hasNode(const std::string& nodeId) const;
    BaseNode* getNode(const std::string& nodeId) const;

    // Destroy nodes the audio thread has finished fading out.
    // Call periodically from the command thread.
    void drainGarbage();

    // Set MIDIManager for MIDI nodes
    void setMIDIManager(MIDIManager* midiManager) { midiManager_ = midiManager; }

    // ---- Audio thread accessors (plain pointers, audio-thread owned) --------

    // All live FFT analyzers, in creation order. The single-analyzer
    // getter returns the OLDEST one (slot 0) for legacy consumers - it
    // used to return whichever analyzer was created last, which silently
    // hijacked the data of every previously created FFT node.
    FFTAnalyzerNode* getFFTAnalyzerAudio() const {
        return numFFTAnalyzers_ > 0 ? fftAnalyzers_[0] : nullptr;
    }
    int getNumFFTAnalyzersAudio() const { return numFFTAnalyzers_; }
    FFTAnalyzerNode* getFFTAnalyzerAudio(int index) const {
        return (index >= 0 && index < numFFTAnalyzers_) ? fftAnalyzers_[index] : nullptr;
    }
    // All live sequencers, in creation order. The "primary" getter returns
    // the OLDEST one (slot 0) and exists only for the legacy single-
    // sequencer field in shared memory - every sequencer now publishes its
    // own slot, so a second Step Sequencer no longer reports the first
    // one's transport.
    SequencerNode* getPrimarySequencerAudio() const {
        return numSequencers_ > 0 ? sequencers_[0] : nullptr;
    }
    int getNumSequencersAudio() const { return numSequencers_; }
    SequencerNode* getSequencerAudio(int index) const {
        return (index >= 0 && index < numSequencers_) ? sequencers_[index] : nullptr;
    }

    // Nodes whose output is a control signal Blender cannot see any other
    // way (Bang, ADSR). SharedMemoryBridge publishes these so Value Output
    // / Math / drivers downstream of them stop reading a constant zero.
    int getNumControlNodesAudio() const { return numControlNodes_; }
    BaseNode* getControlNodeAudio(int index) const {
        return (index >= 0 && index < numControlNodes_) ? controlNodes_[index] : nullptr;
    }
    NodeKind getControlNodeKindAudio(int index) const {
        return (index >= 0 && index < numControlNodes_)
            ? controlNodeKinds_[index] : NodeKind::Generic;
    }

private:
    // ---- Command-thread state ------------------------------------------------
    // Guarded by nodesMutex_ (contended only between the command thread and
    // prepare(); never the audio callback).
    mutable std::mutex nodesMutex_;
    std::unordered_map<std::string, std::unique_ptr<BaseNode>> nodes_;

    // Nodes removed from nodes_ but still owned until the audio thread
    // returns them through garbageQueue_.
    std::unordered_map<BaseNode*, std::unique_ptr<BaseNode>> zombies_;

    // Connections keyed by "destNodeId:destPort" -> (sourceNodeId, sourcePort).
    std::unordered_map<std::string, std::pair<std::string, std::string>> connections_;

    MIDIManager* midiManager_ = nullptr;

    double sampleRate_ = 44100.0;
    int maxBlockSize_ = 512;

    // ---- Cross-thread queues (lock-free SPSC) ---------------------------------
    LockFreeQueue<GraphCommand, 256> commandQueue_;   // command -> audio
    LockFreeQueue<BaseNode*, 256> garbageQueue_;      // audio -> command

    // ---- Audio-thread state (only touched inside process()) -------------------
    struct AudioNodeEntry {
        BaseNode* node = nullptr;
        NodeKind kind = NodeKind::Generic;
    };
    struct FadingEntry {
        BaseNode* node = nullptr;
        int samplesLeft = 0;  // force-reclaim deadline for unprocessed nodes
    };

    std::vector<AudioNodeEntry> audioNodes_;   // capacity reserved up-front
    std::vector<FadingEntry> fading_;          // capacity reserved up-front

    static constexpr int MAX_SEQUENCERS = 32;
    SequencerNode* sequencers_[MAX_SEQUENCERS] = {};
    int numSequencers_ = 0;

    // Input/Output are tracked as arrays (creation order) so adding a
    // second Output cannot hijack the primary, and deleting one reselects
    // the remaining. Device audio mixes every live Output.
    static constexpr int MAX_IO_NODES = 16;
    InputNode* inputNodes_[MAX_IO_NODES] = {};
    int numInputNodes_ = 0;
    BaseNode* outputNodes_[MAX_IO_NODES] = {};
    int numOutputNodes_ = 0;

    static constexpr int MAX_FFT_NODES = 16;
    FFTAnalyzerNode* fftAnalyzers_[MAX_FFT_NODES] = {};
    int numFFTAnalyzers_ = 0;

    static constexpr int MAX_CONTROL_NODES = 32;
    BaseNode* controlNodes_[MAX_CONTROL_NODES] = {};
    NodeKind controlNodeKinds_[MAX_CONTROL_NODES] = {};
    int numControlNodes_ = 0;

    // Scratch buffer for pumping control nodes (sequencers, MIDI, ADSR).
    juce::AudioBuffer<float> controlScratch_;

    // ---- Internals -------------------------------------------------------------
    // Audio thread:
    void applyPendingCommands();
    void reclaimFadedNodes(int numSamples);
    void addAudioNode(BaseNode* node, NodeKind kind);
    void removeAudioNode(BaseNode* node);
    void unpublishNode(BaseNode* node);

    // Command thread:
    std::unique_ptr<BaseNode> instantiateNode(const std::string& nodeType,
                                              const std::string& nodeId,
                                              NodeKind& kindOut);

    static std::string makeConnectionKey(const std::string& destNodeId,
                                         const std::string& destPort);
};
