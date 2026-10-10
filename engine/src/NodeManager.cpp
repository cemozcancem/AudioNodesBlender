#include "NodeManager.h"
#include "nodes/InputNode.h"
#include "nodes/OutputNode.h"
#include "nodes/OscillatorNode.h"
#include "nodes/FilterNode.h"
#include "nodes/GainNode.h"
#include "nodes/DelayNode.h"
#include "nodes/FFTAnalyzerNode.h"
#include "nodes/SamplerNode.h"
#include "nodes/DrumSamplerNode.h"
#include "nodes/SequencerNode.h"
#include "nodes/ADSRNode.h"
#include "nodes/MIDIInputNode.h"
#include "nodes/MIDIOutputNode.h"
#include "nodes/MixerNode.h"
#include "nodes/MathNode.h"
#include "nodes/BangNode.h"
#include <algorithm>
#include <iostream>

NodeManager::NodeManager() {
    // Reserve up-front so the audio thread never reallocates.
    audioNodes_.reserve(MAX_NODES);
    fading_.reserve(MAX_NODES);
    controlScratch_.setSize(2, maxBlockSize_);
}

NodeManager::~NodeManager() = default;

std::string NodeManager::makeConnectionKey(const std::string& destNodeId, const std::string& destPort) {
    return destNodeId + ":" + destPort;
}

void NodeManager::prepare(double sampleRate, int maxBlockSize) {
    sampleRate_ = sampleRate;
    maxBlockSize_ = maxBlockSize;

    controlScratch_.setSize(2, maxBlockSize);

    // The audio callback is not running while prepare() is called, so the
    // command-side mutex is safe here (it never touches the callback).
    std::lock_guard<std::mutex> lock(nodesMutex_);
    for (auto& [id, node] : nodes_) {
        node->prepare(sampleRate, maxBlockSize);
    }
    for (auto& [ptr, node] : zombies_) {
        node->prepare(sampleRate, maxBlockSize);
    }
}

// =============================================================================
// Audio thread
// =============================================================================

void NodeManager::process(const juce::AudioBuffer<float>& input,
                          juce::AudioBuffer<float>& output,
                          int numSamples)
{
    BaseNode::advanceEpoch();

    // Apply pending graph changes (pointer swaps only - no allocation).
    applyPendingCommands();

    const int controlSamples = std::min(numSamples, controlScratch_.getNumSamples());

    // Pump control sources first so their outputs are fresh this block.
    // Bang/MIDI before Sequencer so Play←Bang rising edges are seen same block.
    for (auto& entry : audioNodes_) {
        if (entry.kind == NodeKind::Bang || entry.kind == NodeKind::MIDIInput) {
            entry.node->pullOutput(controlScratch_, controlSamples);
        }
    }
    for (auto& entry : audioNodes_) {
        if (entry.kind == NodeKind::Sequencer) {
            entry.node->pullOutput(controlScratch_, controlSamples);
        }
    }

    // Give every Input node access to the device input buffer.
    for (int i = 0; i < numInputNodes_; ++i) {
        inputNodes_[i]->setInputBuffer(input);
    }

    // Mix every live Output into the device buffer. A second, unconnected
    // Output used to overwrite the single outputNode_ pointer and silence
    // the graph; deleting it then left a null pointer. Summing all Outputs
    // keeps the first chain audible and restores correctly after deletes.
    output.clear();
    if (numOutputNodes_ == 1) {
        outputNodes_[0]->pullOutput(output, numSamples);
    } else if (numOutputNodes_ > 1) {
        const int mixChannels = juce::jmin(output.getNumChannels(),
                                           controlScratch_.getNumChannels());
        const int mixSamples = juce::jmin(numSamples, controlScratch_.getNumSamples());
        for (int i = 0; i < numOutputNodes_; ++i) {
            outputNodes_[i]->pullOutput(controlScratch_, mixSamples);
            for (int ch = 0; ch < mixChannels; ++ch) {
                output.addFrom(ch, 0, controlScratch_, ch, 0, mixSamples);
            }
        }
    }

    // Pump control sinks that were not reached through the audio graph
    // (MIDI output notes must flush, ADSR envelopes must keep advancing,
    // FFT analyzers on analysis-only side chains must keep analyzing -
    // e.g. input -> FFT with nothing routed onward to Output).
    for (auto& entry : audioNodes_) {
        if ((entry.kind == NodeKind::MIDIOutput || entry.kind == NodeKind::ADSR
             || entry.kind == NodeKind::FFTAnalyzer)
            && !entry.node->processedThisEpoch()) {
            entry.node->pullOutput(controlScratch_, controlSamples);
        }
    }

    // Retire nodes whose fade-out finished.
    reclaimFadedNodes(numSamples);
}

void NodeManager::applyPendingCommands() {
    while (auto cmd = commandQueue_.pop()) {
        const GraphCommand& c = *cmd;

        switch (c.type) {
            case GraphCommand::Type::AddNode:
                addAudioNode(c.node, c.kind);
                break;

            case GraphCommand::Type::RemoveNode: {
                c.node->startFadeOut();
                // Deleted nodes stop appearing in shared memory right away;
                // only their audio fades out.
                unpublishNode(c.node);
                if (fading_.size() < fading_.capacity()) {
                    // Force-reclaim deadline: nodes that are not part of the
                    // audible graph never run applyFade, so a sample-count
                    // deadline guarantees they are still reclaimed.
                    const int deadline =
                        static_cast<int>(sampleRate_ * 0.2) + 4 * maxBlockSize_;
                    fading_.push_back({c.node, deadline});
                }
                break;
            }

            case GraphCommand::Type::Connect:
                if (c.node != nullptr) {
                    c.node->setInput(c.portIndex, c.source, c.sourceLane);
                }
                break;

            case GraphCommand::Type::Disconnect:
                if (c.node != nullptr) {
                    c.node->clearInput(c.portIndex);
                }
                break;
        }
    }
}

void NodeManager::addAudioNode(BaseNode* node, NodeKind kind) {
    if (node == nullptr || audioNodes_.size() >= audioNodes_.capacity()) {
        return;
    }
    audioNodes_.push_back({node, kind});

    switch (kind) {
        case NodeKind::Input:
            if (numInputNodes_ < MAX_IO_NODES) {
                inputNodes_[numInputNodes_++] = static_cast<InputNode*>(node);
            }
            break;
        case NodeKind::Output:
            if (numOutputNodes_ < MAX_IO_NODES) {
                outputNodes_[numOutputNodes_++] = node;
            }
            break;
        case NodeKind::FFTAnalyzer:
            if (numFFTAnalyzers_ < MAX_FFT_NODES) {
                fftAnalyzers_[numFFTAnalyzers_++] = static_cast<FFTAnalyzerNode*>(node);
            }
            break;
        case NodeKind::Sequencer:
            if (numSequencers_ < MAX_SEQUENCERS) {
                sequencers_[numSequencers_++] = static_cast<SequencerNode*>(node);
            }
            break;
        case NodeKind::Bang:
        case NodeKind::ADSR:
            if (numControlNodes_ < MAX_CONTROL_NODES) {
                controlNodeKinds_[numControlNodes_] = kind;
                controlNodes_[numControlNodes_++] = node;
            }
            break;
        default:
            break;
    }
}

void NodeManager::removeAudioNode(BaseNode* node) {
    for (size_t i = 0; i < audioNodes_.size(); ++i) {
        if (audioNodes_[i].node == node) {
            audioNodes_[i] = audioNodes_.back();
            audioNodes_.pop_back();
            break;
        }
    }

    for (int i = 0; i < numInputNodes_; ++i) {
        if (inputNodes_[i] == node) {
            for (int j = i; j < numInputNodes_ - 1; ++j) {
                inputNodes_[j] = inputNodes_[j + 1];
            }
            inputNodes_[--numInputNodes_] = nullptr;
            break;
        }
    }

    for (int i = 0; i < numOutputNodes_; ++i) {
        if (outputNodes_[i] == node) {
            for (int j = i; j < numOutputNodes_ - 1; ++j) {
                outputNodes_[j] = outputNodes_[j + 1];
            }
            outputNodes_[--numOutputNodes_] = nullptr;
            break;
        }
    }

    unpublishNode(node);
}

// Remove a node from the lists SharedMemoryBridge publishes (FFT analyzers,
// sequencers, control sources). Safe to call more than once.
void NodeManager::unpublishNode(BaseNode* node) {
    for (int i = 0; i < numFFTAnalyzers_; ++i) {
        if (fftAnalyzers_[i] == node) {
            // Preserve creation order so slot assignments stay stable.
            for (int j = i; j < numFFTAnalyzers_ - 1; ++j) {
                fftAnalyzers_[j] = fftAnalyzers_[j + 1];
            }
            fftAnalyzers_[--numFFTAnalyzers_] = nullptr;
            break;
        }
    }

    for (int i = 0; i < numSequencers_; ++i) {
        if (sequencers_[i] == node) {
            for (int j = i; j < numSequencers_ - 1; ++j) {
                sequencers_[j] = sequencers_[j + 1];
            }
            sequencers_[--numSequencers_] = nullptr;
            break;
        }
    }

    for (int i = 0; i < numControlNodes_; ++i) {
        if (controlNodes_[i] == node) {
            // Preserve creation order so published slots stay stable.
            for (int j = i; j < numControlNodes_ - 1; ++j) {
                controlNodes_[j] = controlNodes_[j + 1];
                controlNodeKinds_[j] = controlNodeKinds_[j + 1];
            }
            --numControlNodes_;
            controlNodes_[numControlNodes_] = nullptr;
            controlNodeKinds_[numControlNodes_] = NodeKind::Generic;
            break;
        }
    }
}

void NodeManager::reclaimFadedNodes(int numSamples) {
    for (size_t i = 0; i < fading_.size();) {
        FadingEntry& entry = fading_[i];
        entry.samplesLeft -= numSamples;

        if (entry.node->isFadeComplete() || entry.samplesLeft <= 0) {
            // Unlink from every remaining node FIRST so no dangling input
            // pointers survive, then hand the node to the command thread
            // for destruction.
            for (auto& audioEntry : audioNodes_) {
                audioEntry.node->clearInputsReferencing(entry.node);
            }
            removeAudioNode(entry.node);

            if (garbageQueue_.push(entry.node)) {
                fading_[i] = fading_.back();
                fading_.pop_back();
                continue;  // do not advance i
            }
            // Garbage queue full: node is already unlinked and silent.
            // Leave it in the list and retry next block.
            entry.samplesLeft = 0;
        }
        ++i;
    }
}

// =============================================================================
// Command thread
// =============================================================================

bool NodeManager::createNode(const std::string& nodeId, const std::string& nodeType) {
    NodeKind kind = NodeKind::Generic;
    auto node = instantiateNode(nodeType, nodeId, kind);
    if (!node) {
        return false;
    }

    node->prepare(sampleRate_, maxBlockSize_);

    GraphCommand cmd;
    cmd.type = GraphCommand::Type::AddNode;
    cmd.kind = kind;
    cmd.node = node.get();

    {
        std::lock_guard<std::mutex> lock(nodesMutex_);
        if (nodes_.count(nodeId) > 0) {
            return false;  // duplicate id
        }
        if (!commandQueue_.push(cmd)) {
            return false;  // queue full - node destroyed here, never shared
        }
        nodes_[nodeId] = std::move(node);
    }
    return true;
}

bool NodeManager::deleteNode(const std::string& nodeId) {
    std::lock_guard<std::mutex> lock(nodesMutex_);

    auto it = nodes_.find(nodeId);
    if (it == nodes_.end()) {
        return false;
    }

    BaseNode* raw = it->second.get();

    GraphCommand cmd;
    cmd.type = GraphCommand::Type::RemoveNode;
    cmd.node = raw;
    if (!commandQueue_.push(cmd)) {
        return false;  // keep the node alive; caller can retry
    }

    // Ownership parks in zombies_ until the audio thread finishes the fade
    // and returns the pointer through garbageQueue_.
    zombies_[raw] = std::move(it->second);
    nodes_.erase(it);

    // Drop bookkeeping for connections touching this node.
    for (auto connIt = connections_.begin(); connIt != connections_.end();) {
        const auto& key = connIt->first;
        const auto& sourceNodeId = connIt->second.first;
        if (sourceNodeId == nodeId || key.rfind(nodeId + ":", 0) == 0) {
            connIt = connections_.erase(connIt);
        } else {
            ++connIt;
        }
    }
    return true;
}

bool NodeManager::connect(const std::string& sourceNodeId, const std::string& destNodeId,
                          const std::string& sourcePort, const std::string& destPort) {
    std::lock_guard<std::mutex> lock(nodesMutex_);

    auto sourceIt = nodes_.find(sourceNodeId);
    auto destIt = nodes_.find(destNodeId);
    if (sourceIt == nodes_.end() || destIt == nodes_.end()) {
        return false;
    }

    const std::string port = destPort.empty() ? "input" : destPort;
    const int portIndex = destIt->second->resolvePortIndex(port);
    if (portIndex < 0) {
        // Port not consumed by the engine (control ports handled in Python).
        return true;
    }

    const int sourceLane = sourceIt->second->resolveOutputLane(
        sourcePort.empty() ? "output" : sourcePort);
    if (sourceLane < 0) {
        // Source socket is not a trigger/audio lane (e.g. sequencer "step"
        // or "playing") - do not wire it as a control input.
        return true;
    }

    GraphCommand cmd;
    cmd.type = GraphCommand::Type::Connect;
    cmd.node = destIt->second.get();
    cmd.source = sourceIt->second.get();
    cmd.portIndex = static_cast<int16_t>(portIndex);
    cmd.sourceLane = static_cast<int16_t>(sourceLane);

    if (!commandQueue_.push(cmd)) {
        return false;
    }

    connections_[makeConnectionKey(destNodeId, port)] =
        std::make_pair(sourceNodeId, sourcePort);
    return true;
}

bool NodeManager::disconnect(const std::string& destNodeId, const std::string& destPort) {
    std::lock_guard<std::mutex> lock(nodesMutex_);

    auto destIt = nodes_.find(destNodeId);
    if (destIt == nodes_.end()) {
        return false;
    }

    const std::string port = destPort.empty() ? "input" : destPort;
    const int portIndex = destIt->second->resolvePortIndex(port);
    if (portIndex < 0) {
        return true;
    }

    GraphCommand cmd;
    cmd.type = GraphCommand::Type::Disconnect;
    cmd.node = destIt->second.get();
    cmd.portIndex = static_cast<int16_t>(portIndex);

    if (!commandQueue_.push(cmd)) {
        return false;
    }

    connections_.erase(makeConnectionKey(destNodeId, port));
    return true;
}

void NodeManager::setParameter(const std::string& nodeId, const std::string& paramName, float value) {
    std::lock_guard<std::mutex> lock(nodesMutex_);
    auto it = nodes_.find(nodeId);
    if (it != nodes_.end()) {
        // Universal bypass (Blender node mute) - handled at the base level
        // so every node type supports it without per-node parameter code.
        if (paramName == "bypass") {
            it->second->setBypass(value > 0.5f);
            return;
        }
        it->second->setParameter(paramName, value);
    }
}

bool NodeManager::hasNode(const std::string& nodeId) const {
    std::lock_guard<std::mutex> lock(nodesMutex_);
    return nodes_.find(nodeId) != nodes_.end();
}

BaseNode* NodeManager::getNode(const std::string& nodeId) const {
    std::lock_guard<std::mutex> lock(nodesMutex_);
    auto it = nodes_.find(nodeId);
    return (it != nodes_.end()) ? it->second.get() : nullptr;
}

void NodeManager::drainGarbage() {
    while (auto node = garbageQueue_.pop()) {
        std::lock_guard<std::mutex> lock(nodesMutex_);
        zombies_.erase(*node);  // destructor runs here, on the command thread
    }
}

std::unique_ptr<BaseNode> NodeManager::instantiateNode(const std::string& nodeType,
                                                       const std::string& nodeId,
                                                       NodeKind& kindOut) {
    kindOut = NodeKind::Generic;

    if (nodeType == "input") {
        kindOut = NodeKind::Input;
        return std::make_unique<InputNode>(nodeId);
    } else if (nodeType == "output") {
        kindOut = NodeKind::Output;
        return std::make_unique<OutputNode>(nodeId);
    } else if (nodeType == "oscillator") {
        return std::make_unique<OscillatorNode>(nodeId);
    } else if (nodeType == "filter") {
        return std::make_unique<FilterNode>(nodeId);
    } else if (nodeType == "gain") {
        return std::make_unique<GainNode>(nodeId);
    } else if (nodeType == "delay") {
        return std::make_unique<DelayNode>(nodeId);
    } else if (nodeType == "fft_analyzer") {
        kindOut = NodeKind::FFTAnalyzer;
        return std::make_unique<FFTAnalyzerNode>(nodeId);
    } else if (nodeType == "sampler") {
        return std::make_unique<SamplerNode>(nodeId);
    } else if (nodeType == "drum_sampler") {
        return std::make_unique<DrumSamplerNode>(nodeId);
    } else if (nodeType == "sequencer") {
        kindOut = NodeKind::Sequencer;
        return std::make_unique<SequencerNode>(nodeId);
    } else if (nodeType == "bang") {
        kindOut = NodeKind::Bang;
        return std::make_unique<BangNode>(nodeId);
    } else if (nodeType == "adsr") {
        kindOut = NodeKind::ADSR;
        return std::make_unique<ADSRNode>(nodeId);
    } else if (nodeType == "midi_input") {
        if (midiManager_ != nullptr) {
            kindOut = NodeKind::MIDIInput;
            return std::make_unique<MIDIInputNode>(nodeId, midiManager_);
        }
    } else if (nodeType == "midi_output") {
        if (midiManager_ != nullptr) {
            kindOut = NodeKind::MIDIOutput;
            return std::make_unique<MIDIOutputNode>(nodeId, midiManager_);
        }
    } else if (nodeType == "mixer") {
        return std::make_unique<MixerNode>(nodeId);
    } else if (nodeType == "math") {
        return std::make_unique<MathNode>(nodeId);
    }

    std::cerr << "NodeManager: unknown node type '" << nodeType << "'" << std::endl;
    return nullptr;
}
