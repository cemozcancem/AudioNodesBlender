#pragma once

#include <zmq.hpp>
#include <nlohmann/json.hpp>
#include <thread>
#include <atomic>
#include <functional>
#include <string>

class AudioEngine;
class NodeManager;
class MIDIManager;

using json = nlohmann::json;

// ZeroMQ command server for receiving commands from Blender
class CommandServer {
public:
    CommandServer(int port = 5555);
    ~CommandServer();
    
    // Set engine, node manager, and MIDI manager references
    void setAudioEngine(AudioEngine* engine) { audioEngine_ = engine; }
    void setNodeManager(NodeManager* manager) { nodeManager_ = manager; }
    void setMIDIManager(MIDIManager* manager) { midiManager_ = manager; }
    
    // Start/stop server thread
    bool start();
    void stop();
    bool isRunning() const { return running_.load(std::memory_order_acquire); }
    
private:
    int port_;
    std::atomic<bool> running_{false};
    std::atomic<bool> shouldStop_{false};
    std::thread serverThread_;
    
    AudioEngine* audioEngine_ = nullptr;
    NodeManager* nodeManager_ = nullptr;
    MIDIManager* midiManager_ = nullptr;
    
    // ZMQ context and socket
    zmq::context_t context_;
    
    // Server thread function
    void serverLoop();
    
    // Process a command and return response
    json processCommand(const json& command);
    
    // Command handlers
    json handleCreateNode(const json& cmd);
    json handleDeleteNode(const json& cmd);
    json handleConnect(const json& cmd);
    json handleDisconnect(const json& cmd);
    json handleSetParam(const json& cmd);
    json handleSetPattern(const json& cmd);
    json handleSetDevice(const json& cmd);
    json handleGetDevices(const json& cmd);
    json handleSetAudioSettings(const json& cmd);
    json handleGetMidiDevices(const json& cmd);
    json handleSetMidiDevice(const json& cmd);
    json handleLoadSample(const json& cmd);
    json handleLoadDrumSample(const json& cmd);
    json handlePing(const json& cmd);
    json handleShutdown(const json& cmd);
};

