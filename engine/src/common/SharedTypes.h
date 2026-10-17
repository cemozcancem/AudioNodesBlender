#pragma once

#include <cstdint>
#include <cstddef>
#include <string>

// =============================================================================
// MIDI Data Structure
// =============================================================================

struct MIDIData {
    uint8_t last_note;              // Last received note number (0-127)
    uint8_t last_velocity;          // Last received velocity (0-127)
    uint8_t note_gate;              // 1 if note is on, 0 if off
    uint8_t padding1;               // Alignment padding
    float cc_values[128];           // All CC values normalized 0-1
    float bpm;                      // Current tempo from MIDI clock
    uint32_t tick_position;         // Song position in MIDI ticks
    uint8_t transport_playing;      // 1 if transport is playing
    uint8_t padding2[3];            // Alignment padding
};

static_assert(sizeof(MIDIData) == 528, "MIDIData size mismatch!");

// =============================================================================
// Sequencer Data Structure
// =============================================================================

struct SequencerData {
    uint8_t current_step;           // Offset 0: Current step (0-63)
    uint8_t is_playing;             // Offset 1: 1 if playing, 0 if stopped
    uint8_t trigger_active;         // Offset 2: 1 if current step has a trigger
    uint8_t padding;
    float bpm;                      // Offset 4: Current BPM
    uint32_t step_count;            // Offset 8: Total number of steps
    // 4 bytes of implicit padding here: uint64_t is 8-byte aligned.
    uint64_t step_pattern;          // Offset 16: Bit pattern for active steps
};

static_assert(sizeof(SequencerData) == 24, "SequencerData size mismatch!");
static_assert(offsetof(SequencerData, bpm) == 4, "layout");
static_assert(offsetof(SequencerData, step_count) == 8, "layout");
static_assert(offsetof(SequencerData, step_pattern) == 16, "layout");

// =============================================================================
// Per-FFT-Analyzer Slot
// =============================================================================

// Up to MAX_FFT_ANALYZERS analyzer nodes publish their spectra
// simultaneously - one slot each, keyed by engine node id, so multiple FFT
// Analyzer nodes in one graph all stay live (a single global spectrum used
// to mean the newest analyzer silently hijacked every older node's data).
struct FFTAnalyzerSlot {
    char node_id[32];            // Engine node id, null-terminated ("" = unused)
    float fft_left[1024];        // Left channel FFT magnitude (0-1 normalized)
    float fft_right[1024];       // Right channel FFT magnitude
    float peak_left;
    float peak_right;
    float rms_left;
    float rms_right;
};

static_assert(sizeof(FFTAnalyzerSlot) == 8240, "FFTAnalyzerSlot size mismatch!");
static_assert(offsetof(FFTAnalyzerSlot, fft_left) == 32, "layout");
static_assert(offsetof(FFTAnalyzerSlot, fft_right) == 4128, "layout");
static_assert(offsetof(FFTAnalyzerSlot, peak_left) == 8224, "layout");

// =============================================================================
// Per-Sequencer Slot
// =============================================================================

// Every live Step Sequencer publishes its own transport, keyed by engine
// node id - the same shape as FFTAnalyzerSlot and for the same reason. The
// legacy top-level `sequencer` field below carries only the PRIMARY
// (oldest) sequencer, so a second Step Sequencer node's Trigger/Step/
// Playing outputs and its step highlight all silently showed the first
// one's state.
struct SequencerSlot {
    char node_id[32];            // Engine node id, null-terminated ("" = unused)
    uint8_t current_step;
    uint8_t is_playing;
    uint8_t trigger_active;
    uint8_t padding;
    float bpm;
    uint32_t step_count;
    // 4 bytes of implicit padding: uint64_t is 8-byte aligned.
    uint64_t step_pattern;
};

static_assert(sizeof(SequencerSlot) == 56, "SequencerSlot size mismatch!");
static_assert(offsetof(SequencerSlot, current_step) == 32, "layout");
static_assert(offsetof(SequencerSlot, bpm) == 36, "layout");
static_assert(offsetof(SequencerSlot, step_count) == 40, "layout");
static_assert(offsetof(SequencerSlot, step_pattern) == 48, "layout");

// =============================================================================
// Per-Control-Source Slot
// =============================================================================

// Engine nodes whose OUTPUT is a control signal (Bang triggers, ADSR
// envelopes) publish it here, keyed by engine node id.
//
// Before this section existed the only way a control value reached Blender
// was if the node also had a shared-memory slot of its own - which Bang and
// ADSR did not. Anything Blender-side downstream of them (Value Output,
// Math, NLA Scrub, a driver) therefore read a hardcoded 0.0 forever: a Bang
// fired in the engine and nothing on the Blender side ever noticed.
struct ControlSlot {
    char node_id[32];            // Engine node id, null-terminated ("" = unused)
    float values[4];             // Control lanes 0..3 (lane 0 = main output)
    uint32_t pulse_count;        // Monotonic count of discrete fires (Bang)
    uint32_t flags;              // bit 0: output currently high / gate open
};

static_assert(sizeof(ControlSlot) == 56, "ControlSlot size mismatch!");
static_assert(offsetof(ControlSlot, values) == 32, "layout");
static_assert(offsetof(ControlSlot, pulse_count) == 48, "layout");
static_assert(offsetof(ControlSlot, flags) == 52, "layout");

// =============================================================================
// Audio Visualization Data Structure
// =============================================================================

// Shared memory structure for audio visualization data.
// This struct MUST match the Python reader exactly - the offsetof
// static_asserts below pin every field the Python side reads.
//
// Torn-read protection is a seqlock: the writer stores an ODD value into
// `sequence`, memcpys the payload, then stores the next EVEN value. A
// reader accepts a snapshot only if it read the same EVEN sequence before
// and after copying the payload.

struct AudioVisualizationData {
    uint32_t sequence;           // Offset 0: seqlock counter (odd = write in progress)
    float fft_left[1024];        // Offset 4: Left channel FFT magnitude (0-1 normalized)
    float fft_right[1024];       // Offset 4100: Right channel FFT magnitude
    float peak_left;             // Offset 8196: Left channel peak level
    float peak_right;            // Offset 8200: Right channel peak level
    float rms_left;              // Offset 8204: Left channel RMS level
    float rms_right;             // Offset 8208: Right channel RMS level
    float waveform[2048];        // Offset 8212: Time-domain waveform (interleaved L/R)
    MIDIData midi;               // Offset 16404: MIDI data
    SequencerData sequencer;     // Offset 16936: Sequencer data
    float sample_rate;           // Offset 16960: Engine sample rate (Hz), 0 if unknown
    uint32_t num_input_channels; // Offset 16964: Active input channel count
    float input_peaks[32];       // Offset 16968: Per-input-channel peak levels
    uint8_t padding[24];         // Offset 17096: alignment padding
    uint32_t num_fft_analyzers;  // Offset 17120: used slots in fft_slots
    uint8_t padding2[28];        // Offset 17124: alignment padding
    FFTAnalyzerSlot fft_slots[4];// Offset 17152: per-analyzer spectra
    // NOTE: the legacy top-level fft_left/fft_right/peak/rms fields above
    // mirror slot 0 (the oldest live analyzer) for backward compatibility.

    // Per-sequencer slots, APPENDED so every offset above stays exactly
    // where it was - an older Python reader still finds everything it
    // knows about, it simply never looks this far.
    uint32_t num_sequencers;     // Offset 50112: used slots in sequencer_slots
    uint8_t padding3[28];        // Offset 50116: align the 8-byte slot array
    SequencerSlot sequencer_slots[4];  // Offset 50144
    // NOTE: the legacy top-level `sequencer` field mirrors slot 0 (the
    // oldest live sequencer) for backward compatibility.

    // Control-source slots, APPENDED for the same reason the sequencer
    // slots were: every offset above keeps its value, so a Python reader
    // built against the older layout still finds everything it knows and
    // simply stops short of this section.
    uint32_t num_control_nodes;  // Offset 50368: used slots in control_slots
    uint8_t padding4[28];        // Offset 50372: keep the slot array aligned
    ControlSlot control_slots[16];  // Offset 50400
};

static_assert(sizeof(AudioVisualizationData) == 51296, "SharedTypes size mismatch!");

// Pin the exact layout the Python reader (shared_memory_reader.py) expects.
static_assert(offsetof(AudioVisualizationData, sequence) == 0, "layout");
static_assert(offsetof(AudioVisualizationData, fft_left) == 4, "layout");
static_assert(offsetof(AudioVisualizationData, fft_right) == 4100, "layout");
static_assert(offsetof(AudioVisualizationData, peak_left) == 8196, "layout");
static_assert(offsetof(AudioVisualizationData, rms_left) == 8204, "layout");
static_assert(offsetof(AudioVisualizationData, waveform) == 8212, "layout");
static_assert(offsetof(AudioVisualizationData, midi) == 16404, "layout");
static_assert(offsetof(AudioVisualizationData, sequencer) == 16936, "layout");
static_assert(offsetof(AudioVisualizationData, sample_rate) == 16960, "layout");
static_assert(offsetof(AudioVisualizationData, num_input_channels) == 16964, "layout");
static_assert(offsetof(AudioVisualizationData, input_peaks) == 16968, "layout");
static_assert(offsetof(AudioVisualizationData, num_fft_analyzers) == 17120, "layout");
static_assert(offsetof(AudioVisualizationData, fft_slots) == 17152, "layout");
static_assert(offsetof(AudioVisualizationData, num_sequencers) == 50112, "layout");
static_assert(offsetof(AudioVisualizationData, sequencer_slots) == 50144, "layout");
static_assert(offsetof(AudioVisualizationData, num_control_nodes) == 50368, "layout");
static_assert(offsetof(AudioVisualizationData, control_slots) == 50400, "layout");

// Shared memory base name - must match Python side
constexpr const char* SHARED_MEMORY_NAME = "AudioNodesVisualization";
constexpr size_t SHARED_MEMORY_SIZE = sizeof(AudioVisualizationData);

// ZeroMQ default port
constexpr int DEFAULT_ZMQ_PORT = 5555;

// Name of the visualization mapping for the engine on `zmqPort` (issue #5).
// Each engine gets its own mapping, so two Blender instances with engines on
// different ports don't overwrite each other's meters/scopes/spectrums.
//   - default port (5555): "AudioNodesVisualization"   (unchanged, old name)
//   - any other port:      "AudioNodesVisualization_<port>"
// POSIX uses the file "/tmp/" + name; Windows uses it as the named mapping.
// THE SAME RULE lives in blender_addon/shared_memory_reader.py and
// tests/harness.py - change all three together.
// Called once at startup (message thread), never on the audio thread.
inline std::string sharedMemoryNameForPort(int zmqPort) {
    std::string name(SHARED_MEMORY_NAME);
    if (zmqPort != DEFAULT_ZMQ_PORT) {
        name += "_" + std::to_string(zmqPort);
    }
    return name;
}

// Audio constants
constexpr int MAX_BLOCK_SIZE = 4096;
constexpr int MAX_INPUT_CHANNELS = 32;   // Max device input channels opened/metered
constexpr int MAX_FFT_ANALYZERS = 4;     // Max simultaneously published FFT analyzers
constexpr int MAX_PUBLISHED_SEQUENCERS = 4;  // Max simultaneously published sequencers
constexpr int MAX_PUBLISHED_CONTROL_NODES = 16;  // Max published Bang/ADSR outputs
constexpr int FFT_SIZE = 2048;
constexpr int FFT_OUTPUT_SIZE = 1024;
constexpr int WAVEFORM_TOTAL = 2048;      // interleaved L/R
constexpr int WAVEFORM_CHANNEL = 1024;    // per channel
