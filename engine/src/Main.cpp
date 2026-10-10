#include <juce_gui_basics/juce_gui_basics.h>
#include <juce_events/juce_events.h>
#include "AudioEngine.h"
#include "NodeManager.h"
#include "CommandServer.h"
#include "SharedMemoryBridge.h"
#include "MIDIManager.h"
#include <iostream>
#include <csignal>

// Global flag for signal handling
std::atomic<bool> shouldQuit{false};

void signalHandler(int signal) {
    (void)signal;
    shouldQuit.store(true, std::memory_order_release);
}

class AudioNodesApplication : public juce::JUCEApplication {
public:
    AudioNodesApplication() = default;
    
    const juce::String getApplicationName() override {
        return "Audio Nodes Engine";
    }
    
    const juce::String getApplicationVersion() override {
        return "3.1.0";
    }
    
    bool moreThanOneInstanceAllowed() override {
        // Each engine is scoped to its own --zmq-port, so two Blender
        // instances on different ports are a supported setup. Returning
        // false made JUCE take a machine-wide named lock (a mutex on
        // Windows) and refuse to launch the second engine entirely, with
        // no message the add-on could surface. Port collisions are caught
        // where they belong: CommandServer::start() fails to bind.
        return true;
    }
    
    void initialise(const juce::String& commandLine) override {
        // Parse command line for port
        int port = 5555;
        
        juce::StringArray args = juce::StringArray::fromTokens(commandLine, " ", "\"");
        for (int i = 0; i < args.size() - 1; ++i) {
            if (args[i] == "--zmq-port" || args[i] == "-p") {
                port = args[i + 1].getIntValue();
                if (port <= 0 || port > 65535) {
                    port = 5555;
                }
            }
        }
        
        std::cout << "Audio Nodes Engine starting..." << std::endl;
        
        // Request microphone permission on macOS
        AudioEngine::requestMicrophonePermission();
        
        // Initialize components
        nodeManager_ = std::make_unique<NodeManager>();
        sharedMemory_ = std::make_unique<SharedMemoryBridge>();
        audioEngine_ = std::make_unique<AudioEngine>();
        midiManager_ = std::make_unique<MIDIManager>();
        commandServer_ = std::make_unique<CommandServer>(port);
        
        // Initialize shared memory
        if (!sharedMemory_->initialize()) {
            std::cerr << "Warning: Failed to initialize shared memory" << std::endl;
        }
        
        // Connect components
        audioEngine_->initialize(nodeManager_.get(), sharedMemory_.get());
        audioEngine_->setMIDIManager(midiManager_.get());
        nodeManager_->setMIDIManager(midiManager_.get());
        commandServer_->setAudioEngine(audioEngine_.get());
        commandServer_->setNodeManager(nodeManager_.get());
        commandServer_->setMIDIManager(midiManager_.get());
        
        // Start the command server before opening the audio device. Opening
        // can stall for a long time (e.g. Bluetooth headsets switching to
        // their headset profile), and Blender must still be able to reach
        // the engine meanwhile. Graph commands are queued until audio runs.
        if (!commandServer_->start()) {
            std::cerr << "Error: Failed to start command server" << std::endl;
            quit();
            return;
        }
        
        std::cout << "Audio Nodes Engine ready on port " << port << std::endl;
        
        // Start audio engine
        if (!audioEngine_->start()) {
            std::cerr << "Warning: Failed to start audio engine" << std::endl;
        }
        
        // Set up signal handler for graceful shutdown
        std::signal(SIGINT, signalHandler);
        std::signal(SIGTERM, signalHandler);
        
        // Start a timer to check for quit signal
        startTimer(100);
    }
    
    void shutdown() override {
        stopTimer();
        
        std::cout << "Audio Nodes Engine shutting down..." << std::endl;
        
        if (commandServer_) {
            commandServer_->stop();
        }
        
        if (audioEngine_) {
            audioEngine_->stop();
        }
        
        commandServer_.reset();
        audioEngine_.reset();
        midiManager_.reset();
        sharedMemory_.reset();
        nodeManager_.reset();
        
        std::cout << "Audio Nodes Engine stopped" << std::endl;
    }
    
    void systemRequestedQuit() override {
        quit();
    }
    
    void anotherInstanceStarted(const juce::String& /*commandLine*/) override {
        // Another instance was started, ignore
    }
    
private:
    std::unique_ptr<NodeManager> nodeManager_;
    std::unique_ptr<SharedMemoryBridge> sharedMemory_;
    std::unique_ptr<AudioEngine> audioEngine_;
    std::unique_ptr<MIDIManager> midiManager_;
    std::unique_ptr<CommandServer> commandServer_;
    
    void startTimer(int intervalMs) {
        juce::Timer::callAfterDelay(intervalMs, [this, intervalMs]() {
            if (shouldQuit.load(std::memory_order_acquire) || 
                (commandServer_ && !commandServer_->isRunning())) {
                quit();
            } else {
                startTimer(intervalMs);
            }
        });
    }
    
    void stopTimer() {
        // Timer stops automatically when we quit
    }
};

// Create the application instance
START_JUCE_APPLICATION(AudioNodesApplication)

