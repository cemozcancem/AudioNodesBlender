#include "CommandServer.h"
#include "AudioEngine.h"
#include "NodeManager.h"
#include "MIDIManager.h"
#include "nodes/SamplerNode.h"
#include "nodes/DrumSamplerNode.h"
#include "nodes/SequencerNode.h"
#include <iostream>
#include <thread>
#include <chrono>

CommandServer::CommandServer(int port)
    : port_(port)
    , context_(1)
{
}

CommandServer::~CommandServer() {
    stop();
}

bool CommandServer::start() {
    if (running_.load(std::memory_order_acquire)) {
        return true;
    }

    shouldStop_.store(false, std::memory_order_release);

    try {
        serverThread_ = std::thread(&CommandServer::serverLoop, this);
        running_.store(true, std::memory_order_release);
        return true;
    } catch (const std::exception& e) {
        std::cerr << "Failed to start command server: " << e.what() << std::endl;
        return false;
    }
}

void CommandServer::stop() {
    shouldStop_.store(true, std::memory_order_release);

    // Nudge the recv loop so it notices shouldStop_ quickly. Only useful
    // while the loop is still running, harmless otherwise.
    if (running_.load(std::memory_order_acquire)) {
        try {
            zmq::context_t tempContext(1);
            zmq::socket_t tempSocket(tempContext, zmq::socket_type::req);
            tempSocket.connect("tcp://127.0.0.1:" + std::to_string(port_));
            tempSocket.set(zmq::sockopt::linger, 0);

            json shutdownCmd = {{"type", "shutdown"}};
            std::string msg = shutdownCmd.dump();
            tempSocket.send(zmq::buffer(msg), zmq::send_flags::dontwait);
        } catch (...) {
            // Ignore errors during shutdown
        }
    }

    // ALWAYS join a joinable thread. The loop exits on its own after a
    // "shutdown" command (setting running_ to false); an early-return on
    // !running_ here would leave the std::thread joinable and its
    // destructor would call std::terminate() (SIGABRT on quit).
    if (serverThread_.joinable()) {
        serverThread_.join();
    }

    running_.store(false, std::memory_order_release);
}

void CommandServer::serverLoop() {
    try {
        zmq::socket_t socket(context_, zmq::socket_type::rep);
        socket.set(zmq::sockopt::rcvtimeo, 100); // 100ms timeout for checking shouldStop
        socket.bind("tcp://*:" + std::to_string(port_));

        std::cout << "Command server listening on port " << port_ << std::endl;

        while (!shouldStop_.load(std::memory_order_acquire)) {
            // Free nodes the audio thread has finished fading out. This is
            // the command thread, so destructors (sample buffers, FFT
            // worker joins) are safe here.
            if (nodeManager_ != nullptr) {
                nodeManager_->drainGarbage();
            }

            zmq::message_t request;
            auto result = socket.recv(request, zmq::recv_flags::none);

            if (!result) {
                // Timeout, check shouldStop and continue
                continue;
            }

            try {
                std::string requestStr(static_cast<char*>(request.data()), request.size());
                json command = json::parse(requestStr);

                json response = processCommand(command);

                std::string responseStr = response.dump();
                socket.send(zmq::buffer(responseStr), zmq::send_flags::none);

            } catch (const std::exception& e) {
                // Never let a malformed command (bad JSON, wrong types,
                // out-of-range values) take down the whole server.
                json errorResponse = {
                    {"status", "error"},
                    {"message", std::string("Command error: ") + e.what()}
                };
                socket.send(zmq::buffer(errorResponse.dump()), zmq::send_flags::none);
            }
        }

        socket.close();

    } catch (const zmq::error_t& e) {
        std::cerr << "ZMQ error in server loop: " << e.what() << std::endl;
    }

    running_.store(false, std::memory_order_release);
}

json CommandServer::processCommand(const json& command) {
    if (!command.contains("type")) {
        return {{"status", "error"}, {"message", "Missing 'type' field"}};
    }

    std::string type = command["type"].get<std::string>();

    if (type == "create_node") {
        return handleCreateNode(command);
    } else if (type == "delete_node") {
        return handleDeleteNode(command);
    } else if (type == "connect") {
        return handleConnect(command);
    } else if (type == "disconnect") {
        return handleDisconnect(command);
    } else if (type == "set_param") {
        return handleSetParam(command);
    } else if (type == "set_pattern") {
        return handleSetPattern(command);
    } else if (type == "set_device") {
        return handleSetDevice(command);
    } else if (type == "get_devices") {
        return handleGetDevices(command);
    } else if (type == "set_audio_settings") {
        return handleSetAudioSettings(command);
    } else if (type == "get_midi_devices") {
        return handleGetMidiDevices(command);
    } else if (type == "set_midi_device") {
        return handleSetMidiDevice(command);
    } else if (type == "load_sample") {
        return handleLoadSample(command);
    } else if (type == "load_drum_sample") {
        return handleLoadDrumSample(command);
    } else if (type == "ping") {
        return handlePing(command);
    } else if (type == "shutdown") {
        return handleShutdown(command);
    }

    return {{"status", "error"}, {"message", "Unknown command type: " + type}};
}

json CommandServer::handleCreateNode(const json& cmd) {
    if (!cmd.contains("node_id") || !cmd.contains("node_type")) {
        return {{"status", "error"}, {"message", "Missing node_id or node_type"}};
    }

    if (nodeManager_ == nullptr) {
        return {{"status", "error"}, {"message", "NodeManager not initialized"}};
    }

    const std::string nodeId = cmd["node_id"].get<std::string>();
    const std::string nodeType = cmd["node_type"].get<std::string>();

    // Validate params BEFORE creating the node. A non-numeric value used
    // to throw after registration, leaving a half-created node behind
    // under an id that could never be reused.
    if (cmd.contains("params") && cmd["params"].is_object()) {
        for (auto& [key, value] : cmd["params"].items()) {
            if (!value.is_number()) {
                return {{"status", "error"},
                        {"message", "Param '" + key + "' must be a number"}};
            }
        }
    }

    // Node construction and prepare() happen right here on the command
    // thread; only a pointer crosses to the audio thread. The node is
    // therefore visible to subsequent commands (set_param, load_sample)
    // immediately - no polling required.
    if (!nodeManager_->createNode(nodeId, nodeType)) {
        return {{"status", "error"}, {"message", "Failed to create node: " + nodeId}};
    }

    if (cmd.contains("params") && cmd["params"].is_object()) {
        for (auto& [key, value] : cmd["params"].items()) {
            nodeManager_->setParameter(nodeId, key, value.get<float>());
        }
    }

    return {{"status", "ok"}};
}

json CommandServer::handleDeleteNode(const json& cmd) {
    if (!cmd.contains("node_id")) {
        return {{"status", "error"}, {"message", "Missing node_id"}};
    }

    if (nodeManager_ == nullptr) {
        return {{"status", "error"}, {"message", "NodeManager not initialized"}};
    }

    nodeManager_->deleteNode(cmd["node_id"].get<std::string>());
    return {{"status", "ok"}};
}

json CommandServer::handleConnect(const json& cmd) {
    if (!cmd.contains("source_node") || !cmd.contains("dest_node")) {
        return {{"status", "error"}, {"message", "Missing source_node or dest_node"}};
    }

    if (nodeManager_ == nullptr) {
        return {{"status", "error"}, {"message", "NodeManager not initialized"}};
    }

    const bool ok = nodeManager_->connect(
        cmd["source_node"].get<std::string>(),
        cmd["dest_node"].get<std::string>(),
        cmd.value("source_port", "output"),
        cmd.value("dest_port", "input"));

    return {{"status", ok ? "ok" : "error"}};
}

json CommandServer::handleDisconnect(const json& cmd) {
    if (!cmd.contains("dest_node")) {
        return {{"status", "error"}, {"message", "Missing dest_node"}};
    }

    if (nodeManager_ == nullptr) {
        return {{"status", "error"}, {"message", "NodeManager not initialized"}};
    }

    const bool ok = nodeManager_->disconnect(
        cmd["dest_node"].get<std::string>(),
        cmd.value("dest_port", "input"));

    return {{"status", ok ? "ok" : "error"}};
}

json CommandServer::handleSetParam(const json& cmd) {
    if (!cmd.contains("node_id") || !cmd.contains("param") || !cmd.contains("value")) {
        return {{"status", "error"}, {"message", "Missing node_id, param, or value"}};
    }

    if (nodeManager_ == nullptr) {
        return {{"status", "error"}, {"message", "NodeManager not initialized"}};
    }

    // Nodes are created synchronously, so the target always exists by the
    // time a set_param for it arrives. setParameter only touches atomics.
    nodeManager_->setParameter(
        cmd["node_id"].get<std::string>(),
        cmd["param"].get<std::string>(),
        cmd["value"].get<float>());

    return {{"status", "ok"}};
}

json CommandServer::handleSetPattern(const json& cmd) {
    // Sequencer step patterns are 64-bit masks. They MUST travel as JSON
    // integers: the generic set_param path parses values as float32 (24-bit
    // mantissa), which silently corrupts any pattern using steps >= 25.
    if (!cmd.contains("node_id") || !cmd.contains("pattern")) {
        return {{"status", "error"}, {"message", "Missing node_id or pattern"}};
    }

    if (nodeManager_ == nullptr) {
        return {{"status", "error"}, {"message", "NodeManager not initialized"}};
    }

    if (!cmd["pattern"].is_number_integer() && !cmd["pattern"].is_number_unsigned()) {
        return {{"status", "error"}, {"message", "pattern must be an integer"}};
    }

    const std::string nodeId = cmd["node_id"].get<std::string>();
    const int lane = cmd.value("lane", 0);
    const uint64_t pattern = cmd["pattern"].get<uint64_t>();

    BaseNode* node = nodeManager_->getNode(nodeId);
    if (node == nullptr) {
        return {{"status", "error"}, {"message", "Node not found: " + nodeId}};
    }

    auto* sequencer = dynamic_cast<SequencerNode*>(node);
    if (sequencer == nullptr) {
        return {{"status", "error"}, {"message", "Node is not a sequencer: " + nodeId}};
    }

    if (lane < 0 || lane >= SequencerNode::MAX_LANES) {
        return {{"status", "error"}, {"message", "Invalid lane"}};
    }

    sequencer->setPattern(lane, pattern);  // atomic store, thread-safe
    return {{"status", "ok"}};
}

json CommandServer::handleSetDevice(const json& cmd) {
    if (!cmd.contains("device_type") || !cmd.contains("device_name")) {
        return {{"status", "error"}, {"message", "Missing device_type or device_name"}};
    }

    if (audioEngine_ == nullptr) {
        return {{"status", "error"}, {"message", "AudioEngine not initialized"}};
    }

    std::string deviceType = cmd["device_type"].get<std::string>();
    std::string deviceName = cmd["device_name"].get<std::string>();

    bool success = false;
    if (deviceType == "input") {
        success = audioEngine_->setInputDevice(deviceName);
    } else if (deviceType == "output") {
        success = audioEngine_->setOutputDevice(deviceName);
    } else {
        return {{"status", "error"}, {"message", "Invalid device_type"}};
    }

    // Report the devices actually in use. The add-on verifies against
    // these instead of trusting a bare ok: JUCE can open a substitute
    // device, and a reply lost to a client-side timeout used to look
    // exactly like a failed switch.
    json data = {
        {"current_input", audioEngine_->getCurrentInputDevice().toStdString()},
        {"current_output", audioEngine_->getCurrentOutputDevice().toStdString()},
        {"sample_rate", audioEngine_->getSampleRate()},
        {"buffer_size", audioEngine_->getBufferSize()}
    };

    if (success) {
        return {{"status", "ok"}, {"data", data}};
    }
    return {{"status", "error"},
            {"message", audioEngine_->getLastDeviceError().toStdString()},
            {"data", data}};
}

json CommandServer::handleGetDevices(const json& /*cmd*/) {
    if (audioEngine_ == nullptr) {
        return {{"status", "error"}, {"message", "AudioEngine not initialized"}};
    }

    auto inputDevices = audioEngine_->getInputDeviceNames();
    auto outputDevices = audioEngine_->getOutputDeviceNames();

    json inputList = json::array();
    for (const auto& device : inputDevices) {
        inputList.push_back(device.toStdString());
    }

    json outputList = json::array();
    for (const auto& device : outputDevices) {
        outputList.push_back(device.toStdString());
    }

    // Active input channel names of the current device, in callback order.
    // Index N in this list is exactly channel N for InputNode's
    // left_channel/right_channel params and for input_peaks[N] in shared
    // memory - the Blender UI builds its channel dropdowns from this.
    json channelList = json::array();
    for (const auto& name : audioEngine_->getActiveInputChannelNames()) {
        channelList.push_back(name.toStdString());
    }

    return {
        {"status", "ok"},
        {"data", {
            {"input_devices", inputList},
            {"output_devices", outputList},
            {"current_input", audioEngine_->getCurrentInputDevice().toStdString()},
            {"current_output", audioEngine_->getCurrentOutputDevice().toStdString()},
            {"input_channels", channelList},
            {"sample_rate", audioEngine_->getSampleRate()},
            {"buffer_size", audioEngine_->getBufferSize()},
            {"min_buffer_size", audioEngine_->getMinBufferSize()}
        }}
    };
}

json CommandServer::handleSetAudioSettings(const json& cmd) {
    if (audioEngine_ == nullptr) {
        return {{"status", "error"}, {"message", "AudioEngine not initialized"}};
    }

    const double sampleRate = cmd.value("sample_rate", 0.0);
    const int bufferSize = cmd.value("buffer_size", 0);

    if (sampleRate <= 0.0 && bufferSize <= 0) {
        return {{"status", "error"},
                {"message", "Provide sample_rate and/or buffer_size"}};
    }

    // defer: store as preferences without reopening the device. The
    // add-on uses this at startup so the saved audio device is restored in
    // ONE device open that already has the right rate and buffer, instead
    // of two back-to-back reopens.
    const bool defer = cmd.value("defer", false);
    const bool ok = audioEngine_->setAudioSettings(sampleRate, bufferSize, defer);

    // Always report what the device actually negotiated - the engine
    // snaps requests to the nearest supported value.
    return {
        {"status", ok ? "ok" : "error"},
        {"data", {
            {"sample_rate", audioEngine_->getSampleRate()},
            {"buffer_size", audioEngine_->getBufferSize()},
            {"min_buffer_size", audioEngine_->getMinBufferSize()}
        }}
    };
}

json CommandServer::handlePing(const json& /*cmd*/) {
    return {
        {"status", "ok"},
        {"message", "pong"}
    };
}

json CommandServer::handleShutdown(const json& /*cmd*/) {
    shouldStop_.store(true, std::memory_order_release);
    return {{"status", "ok"}};
}

json CommandServer::handleGetMidiDevices(const json& /*cmd*/) {
    if (midiManager_ == nullptr) {
        return {{"status", "error"}, {"message", "MIDIManager not initialized"}};
    }

    auto inputDevices = midiManager_->getInputDeviceNames();
    auto outputDevices = midiManager_->getOutputDeviceNames();

    json inputList = json::array();
    for (const auto& device : inputDevices) {
        inputList.push_back(device);
    }

    json outputList = json::array();
    for (const auto& device : outputDevices) {
        outputList.push_back(device);
    }

    return {
        {"status", "ok"},
        {"data", {
            {"input_devices", inputList},
            {"output_devices", outputList},
            {"current_input", midiManager_->getCurrentInputDevice()},
            {"current_output", midiManager_->getCurrentOutputDevice()}
        }}
    };
}

json CommandServer::handleSetMidiDevice(const json& cmd) {
    if (!cmd.contains("device_type") || !cmd.contains("device_name")) {
        return {{"status", "error"}, {"message", "Missing device_type or device_name"}};
    }

    if (midiManager_ == nullptr) {
        return {{"status", "error"}, {"message", "MIDIManager not initialized"}};
    }

    std::string deviceType = cmd["device_type"].get<std::string>();
    std::string deviceName = cmd["device_name"].get<std::string>();

    bool success = false;
    if (deviceType == "input") {
        success = midiManager_->openInputDevice(deviceName);
    } else if (deviceType == "output") {
        success = midiManager_->openOutputDevice(deviceName);
    } else {
        return {{"status", "error"}, {"message", "Invalid device_type"}};
    }

    return {{"status", success ? "ok" : "error"}};
}

json CommandServer::handleLoadSample(const json& cmd) {
    if (!cmd.contains("node_id") || !cmd.contains("file_path")) {
        return {{"status", "error"}, {"message", "Missing node_id or file_path"}};
    }

    if (nodeManager_ == nullptr) {
        return {{"status", "error"}, {"message", "NodeManager not initialized"}};
    }

    const std::string nodeId = cmd["node_id"].get<std::string>();
    const std::string filePath = cmd["file_path"].get<std::string>();

    // Nodes are created synchronously on this thread, so no polling loop
    // is needed - the node either exists or it does not.
    BaseNode* node = nodeManager_->getNode(nodeId);
    if (node == nullptr) {
        return {{"status", "error"}, {"message", "Node not found: " + nodeId}};
    }

    auto* samplerNode = dynamic_cast<SamplerNode*>(node);
    if (samplerNode == nullptr) {
        return {{"status", "error"}, {"message", "Node is not a sampler: " + nodeId}};
    }

    // The file read happens on this thread; the audio thread picks the new
    // buffer up via a lock-free pointer swap and is never blocked by it.
    const bool success = samplerNode->loadSample(filePath);
    return {{"status", success ? "ok" : "error"}};
}

json CommandServer::handleLoadDrumSample(const json& cmd) {
    if (!cmd.contains("node_id") || !cmd.contains("slot") || !cmd.contains("file_path")) {
        return {{"status", "error"}, {"message", "Missing node_id, slot, or file_path"}};
    }

    if (nodeManager_ == nullptr) {
        return {{"status", "error"}, {"message", "NodeManager not initialized"}};
    }

    const std::string nodeId = cmd["node_id"].get<std::string>();
    const int slot = cmd["slot"].get<int>();
    const std::string filePath = cmd["file_path"].get<std::string>();

    BaseNode* node = nodeManager_->getNode(nodeId);
    if (node == nullptr) {
        return {{"status", "error"}, {"message", "Node not found: " + nodeId}};
    }

    auto* drumSamplerNode = dynamic_cast<DrumSamplerNode*>(node);
    if (drumSamplerNode == nullptr) {
        return {{"status", "error"}, {"message", "Node is not a drum sampler: " + nodeId}};
    }

    const bool success = drumSamplerNode->loadSample(slot, filePath);
    return {{"status", success ? "ok" : "error"}};
}
