#pragma once

#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_dsp/juce_dsp.h>
#include <string>
#include <atomic>
#include <array>
#include <cstdint>

// Forward declaration
class NodeManager;

// Base class for all audio processing nodes.
//
// Threading model:
//   - Graph topology (inputs_) is mutated ONLY on the audio thread, via
//     POD commands popped from NodeManager's lock-free queue.
//   - Port NAMES are resolved to integer indices on the command thread
//     (resolvePortIndex); the audio thread never touches strings.
//   - setParameter()/getParameter() run on the command thread and must only
//     touch atomics / lock-free state. Anything that is not thread-safe to
//     mutate concurrently with process() must be deferred via a dirty flag
//     and applied inside process().
//   - Each node renders at most once per audio block (epoch-cached
//     pullOutput). Diamond fan-outs get a copy of the cached output;
//     feedback cycles get the previous block's output instead of
//     recursing infinitely.
class BaseNode {
public:
    // Fixed number of connectable input ports (audio + control triggers).
    static constexpr int MAX_PORTS = 16;

    explicit BaseNode(const std::string& id);
    virtual ~BaseNode() = default;

    // ---- Audio thread: rendering -------------------------------------------

    // Render this node's output for the current block into `dest`.
    // Epoch-cached: safe against diamonds and cycles. Extra channels in
    // `dest` beyond the internal stereo cache are cleared.
    void pullOutput(juce::AudioBuffer<float>& dest, int numSamples);

    // Has this node already rendered during the current epoch?
    bool processedThisEpoch() const { return lastEpoch_ == currentEpoch_; }

    // Called by NodeManager at the start of every audio callback.
    static void advanceEpoch() { ++currentEpoch_; }

    // ---- Lifecycle (command thread / device thread, callback not running) --

    // Prepare for playback - never called concurrently with process().
    virtual void prepare(double sampleRate, int maxBlockSize);

    // Reset state - never called concurrently with process().
    virtual void reset();

    // ---- Command thread: parameters ----------------------------------------

    // Must be lock-free / atomic only. Runs on the command thread.
    virtual void setParameter(const std::string& name, float value) = 0;
    virtual float getParameter(const std::string& name) const { return 0.0f; }

    // Control signal output (0.0 or 1.0 for triggers, continuous for
    // modulators). Read on the audio thread; must be atomic-backed.
    virtual float getControlOutput(int lane = 0) const { return 0.0f; }

    // ---- Command thread: port name resolution -------------------------------

    // Map a destination port name to an input port index (0..MAX_PORTS-1).
    // Returns -1 for ports the engine does not consume (e.g. control ports
    // handled Python-side). Command thread only.
    virtual int resolvePortIndex(const std::string& portName) const;

    // Map a source (output) port name to a control lane index. Default 0.
    virtual int resolveOutputLane(const std::string& portName) const;

    // Parse a non-negative integer from s starting at pos, ending at endPos
    // (npos = end of string). Returns -1 on failure. Never throws - safe
    // replacement for std::stoi in parameter-name parsing.
    static int parseIndex(const std::string& s, size_t pos,
                          size_t endPos = std::string::npos);

    // ---- Audio thread: connection management (index based, no strings) -----

    void setInput(int portIndex, BaseNode* node, int sourceLane = 0);
    BaseNode* getInput(int portIndex) const;
    int getInputLane(int portIndex) const;
    void clearInput(int portIndex);

    // Remove every reference to `node` from this node's inputs.
    // Called by NodeManager before a node is destroyed (UAF protection).
    void clearInputsReferencing(const BaseNode* node);

    // Node identification
    const std::string& getId() const { return id_; }

    // Bypass state
    void setBypass(bool bypass) { bypassed_.store(bypass, std::memory_order_release); }
    bool isBypassed() const { return bypassed_.load(std::memory_order_acquire); }

    // Fade state for click-free deletion
    bool isFadingOut() const { return fadingOut_.load(std::memory_order_acquire); }
    bool isFadeComplete() const { return fadeComplete_.load(std::memory_order_acquire); }
    void startFadeOut();

protected:
    // Main processing function - renders this node's output into `buffer`
    // (already cleared, stereo, >= numSamples capacity). Audio thread.
    // NEVER allocate memory, lock mutexes, do I/O, or touch strings here!
    virtual void process(juce::AudioBuffer<float>& buffer, int numSamples) = 0;

    // Render a connected input into `dest` (no-op leaves dest untouched if
    // the port is unconnected; returns false in that case).
    bool pullInput(int portIndex, juce::AudioBuffer<float>& dest, int numSamples);

    std::string id_;
    double sampleRate_ = 44100.0;
    int maxBlockSize_ = 512;

    std::atomic<bool> bypassed_{false};
    std::atomic<bool> fadingOut_{false};
    std::atomic<bool> fadeComplete_{false};

    // Fade out gain for click-free removal
    juce::SmoothedValue<float> fadeGain_;
    static constexpr float FADE_TIME_SECONDS = 0.05f; // 50ms fade

    // Input connections - audio thread owned, fixed size, no allocation.
    // Port 0 is the default "input" port.
    std::array<BaseNode*, MAX_PORTS> inputs_{};
    std::array<int, MAX_PORTS> inputLanes_{};

    // Apply fade if fading out
    void applyFade(juce::AudioBuffer<float>& buffer, int numSamples);

private:
    // Per-block output cache (epoch-guarded). Sized in prepare().
    juce::AudioBuffer<float> cache_;
    uint64_t lastEpoch_ = 0;

    // Current block epoch. Written by NodeManager and read by nodes on the
    // audio thread only, so a plain static is safe.
    static uint64_t currentEpoch_;
};
