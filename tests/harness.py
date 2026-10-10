"""
Engine test harness — starts the C++ engine directly, no Blender required.

Usage:
    from harness import Engine
    with Engine() as e:
        assert e.ping()
        e.create_node("gain_1", "gain")
"""

import json
import mmap
import os
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).parent.parent
ENGINE_BINARY = (
    PROJECT_ROOT
    / "blender_addon/bin/darwin_arm64/AudioNodesEngine.app/Contents/MacOS/Audio Nodes Engine"
)

ZMQ_PORT = 5555

# Shared memory layout — must match engine/src/common/SharedTypes.h
SHM_NAME = "AudioNodesVisualization"
SHM_SIZE = 50112
OFFSET_SEQUENCE  = 0
OFFSET_FFT_LEFT  = 4
OFFSET_FFT_RIGHT = 4100
OFFSET_PEAK_LEFT = 8196
OFFSET_PEAK_RIGHT = 8200
OFFSET_RMS_LEFT  = 8204
OFFSET_RMS_RIGHT = 8208
OFFSET_WAVEFORM  = 8212
OFFSET_MIDI      = 16404
OFFSET_SEQUENCER = 16936
# SequencerData layout (24 bytes):
#   uint8 current_step, uint8 is_playing, uint8 trigger_active, uint8 padding,
#   float bpm, uint32 step_count, uint64 step_pattern
OFFSET_SEQ_STEP      = 16936
OFFSET_SEQ_PLAYING   = 16937
OFFSET_SEQ_TRIGGER   = 16938
OFFSET_SEQ_BPM       = 16940
OFFSET_SEQ_COUNT     = 16944
OFFSET_SEQ_PATTERN   = 16948


class EngineError(RuntimeError):
    pass


class Engine:
    """
    Manages the engine process lifecycle and provides a ZMQ command API.

    Use as a context manager:
        with Engine() as e:
            e.ping()

    Or manually:
        e = Engine()
        e.start()
        ...
        e.stop()
    """

    def __init__(self, port: int = ZMQ_PORT, startup_timeout: float = 5.0,
                 log_path: Optional[Path] = None, wait_for_audio: bool = True,
                 audio_timeout: float = 20.0):
        """
        Args:
            port: ZMQ port the engine binds to.
            startup_timeout: How long to wait for the engine to respond to ping.
            log_path: If set, engine stdout/stderr are written here so tests
                that need to inspect engine diagnostics can read this file
                after `stop()`. When None, engine output is discarded.
            wait_for_audio: After the engine answers, also wait until it has
                finished opening the audio device (slow for Bluetooth
                headsets), so tests start with audio running.
            audio_timeout: How long to wait for the audio device to open.
        """
        self._port = port
        self._startup_timeout = startup_timeout
        self._log_path = log_path
        self._wait_for_audio = wait_for_audio
        self._audio_timeout = audio_timeout
        self._log_file = None
        self._proc: Optional[subprocess.Popen] = None
        self._ctx = None
        self._sock = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        try:
            import zmq
        except ImportError:
            raise EngineError("pyzmq not installed — run: pip install pyzmq")

        if not ENGINE_BINARY.exists():
            raise EngineError(
                f"Engine binary not found: {ENGINE_BINARY}\n"
                "Run ./scripts/build_engine.sh first."
            )

        if self._log_path is not None:
            self._log_file = open(self._log_path, "w")
            stdout = self._log_file
            stderr = self._log_file
        else:
            stdout = subprocess.DEVNULL
            stderr = subprocess.DEVNULL

        # Same dylib resolution the add-on uses: prefer the bundled
        # Frameworks copies so tests exercise the customer configuration.
        env = os.environ.copy()
        frameworks = ENGINE_BINARY.parent.parent / "Frameworks"
        if sys.platform == "darwin" and frameworks.is_dir():
            fw = str(frameworks)
            for var in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
                existing = env.get(var, "")
                env[var] = f"{fw}:{existing}" if existing else fw

        self._proc = subprocess.Popen(
            [str(ENGINE_BINARY), "--zmq-port", str(self._port)],
            stdout=stdout, stderr=stderr, env=env,
        )

        import zmq
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.REQ)
        self._sock.setsockopt(zmq.RCVTIMEO, 2000)
        self._sock.setsockopt(zmq.SNDTIMEO, 2000)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.connect(f"tcp://localhost:{self._port}")

        # Wait for engine to be ready
        deadline = time.time() + self._startup_timeout
        while time.time() < deadline:
            if self.ping():
                if self._wait_for_audio:
                    self._wait_for_audio_device()
                return
            time.sleep(0.1)

        self.stop()
        raise EngineError(f"Engine did not respond within {self._startup_timeout}s")

    def _wait_for_audio_device(self):
        deadline = time.time() + self._audio_timeout
        while time.time() < deadline:
            r = self.send({"type": "get_devices"})
            if r is not None and "still opening" not in r.get("message", ""):
                return
            time.sleep(0.2)
        self.stop()
        raise EngineError(
            f"Audio device did not open within {self._audio_timeout}s")

    def stop(self):
        try:
            self.send({"type": "shutdown"})
        except Exception:
            pass

        if self._sock:
            try:
                self._sock.close(linger=0)
            except Exception:
                pass
            self._sock = None

        if self._ctx:
            try:
                self._ctx.term()
            except Exception:
                pass
            self._ctx = None

        if self._proc:
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

        if self._log_file is not None:
            try:
                self._log_file.flush()
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    # ------------------------------------------------------------------
    # ZMQ commands
    # ------------------------------------------------------------------

    def send(self, command: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self._sock is None:
            raise EngineError("Engine not started")
        try:
            self._sock.send_string(json.dumps(command))
            return json.loads(self._sock.recv_string())
        except Exception as exc:
            # Reset socket after any error to avoid stuck REQ state
            import zmq
            try:
                self._sock.close(linger=0)
            except Exception:
                pass
            self._sock = self._ctx.socket(zmq.REQ)
            self._sock.setsockopt(zmq.RCVTIMEO, 2000)
            self._sock.setsockopt(zmq.SNDTIMEO, 2000)
            self._sock.setsockopt(zmq.LINGER, 0)
            self._sock.connect(f"tcp://localhost:{self._port}")
            raise EngineError(f"Command failed: {exc}") from exc

    def ok(self, command: Dict[str, Any]) -> bool:
        """Send a command and return True if status == 'ok'."""
        try:
            r = self.send(command)
            return r is not None and r.get("status") == "ok"
        except EngineError:
            return False

    def ping(self) -> bool:
        try:
            r = self.send({"type": "ping"})
            return r is not None and r.get("status") == "ok"
        except EngineError:
            return False

    def create_node(self, node_id: str, node_type: str,
                    params: Optional[Dict[str, float]] = None) -> bool:
        cmd = {"type": "create_node", "node_id": node_id, "node_type": node_type}
        if params:
            cmd["params"] = params
        return self.ok(cmd)

    def delete_node(self, node_id: str) -> bool:
        return self.ok({"type": "delete_node", "node_id": node_id})

    def connect(self, src: str, dst: str,
                src_port: str = "output", dst_port: str = "input") -> bool:
        return self.ok({
            "type": "connect",
            "source_node": src, "dest_node": dst,
            "source_port": src_port, "dest_port": dst_port,
        })

    def disconnect(self, dst: str, dst_port: str = "input") -> bool:
        return self.ok({"type": "disconnect", "dest_node": dst, "dest_port": dst_port})

    def set_param(self, node_id: str, param: str, value: float) -> bool:
        return self.ok({"type": "set_param", "node_id": node_id,
                        "param": param, "value": value})

    def set_pattern(self, node_id: str, pattern: int, lane: int = 0) -> bool:
        """Set a sequencer step mask as an exact 64-bit integer."""
        return self.ok({
            "type": "set_pattern",
            "node_id": node_id,
            "lane": lane,
            "pattern": int(pattern),
        })

    def get_devices(self) -> Optional[Dict]:
        r = self.send({"type": "get_devices"})
        return r.get("data") if r and r.get("status") == "ok" else None

    def get_midi_devices(self) -> Optional[Dict]:
        r = self.send({"type": "get_midi_devices"})
        return r.get("data") if r and r.get("status") == "ok" else None

    # ------------------------------------------------------------------
    # Shared memory
    # ------------------------------------------------------------------

    def read_shared_memory(self) -> Optional[Dict[str, Any]]:
        """
        Read one frame of visualization data from shared memory.
        Returns None if the shared memory file doesn't exist yet or a torn read
        is detected.
        """
        shm_path = f"/tmp/{SHM_NAME}"
        if not os.path.exists(shm_path):
            return None

        try:
            with open(shm_path, "rb") as f:
                with mmap.mmap(f.fileno(), SHM_SIZE, access=mmap.ACCESS_READ) as mm:
                    seq1 = struct.unpack_from("I", mm, OFFSET_SEQUENCE)[0]

                    fft_left  = struct.unpack_from("1024f", mm, OFFSET_FFT_LEFT)
                    fft_right = struct.unpack_from("1024f", mm, OFFSET_FFT_RIGHT)
                    peak_left  = struct.unpack_from("f", mm, OFFSET_PEAK_LEFT)[0]
                    peak_right = struct.unpack_from("f", mm, OFFSET_PEAK_RIGHT)[0]
                    rms_left   = struct.unpack_from("f", mm, OFFSET_RMS_LEFT)[0]
                    rms_right  = struct.unpack_from("f", mm, OFFSET_RMS_RIGHT)[0]

                    seq2 = struct.unpack_from("I", mm, OFFSET_SEQUENCE)[0]

            if seq1 != seq2:
                return None  # torn read

            return {
                "sequence": seq1,
                "fft_left": fft_left,
                "fft_right": fft_right,
                "peak_left": peak_left,
                "peak_right": peak_right,
                "rms_left": rms_left,
                "rms_right": rms_right,
            }
        except Exception:
            return None

    def read_sequencer(self) -> Optional[Dict[str, Any]]:
        """Read sequencer state from shared memory. Returns None if unavailable."""
        shm_path = f"/tmp/{SHM_NAME}"
        if not os.path.exists(shm_path):
            return None
        try:
            with open(shm_path, "rb") as f:
                with mmap.mmap(f.fileno(), SHM_SIZE, access=mmap.ACCESS_READ) as mm:
                    current_step   = struct.unpack_from("B", mm, OFFSET_SEQ_STEP)[0]
                    is_playing     = struct.unpack_from("B", mm, OFFSET_SEQ_PLAYING)[0]
                    trigger_active = struct.unpack_from("B", mm, OFFSET_SEQ_TRIGGER)[0]
                    bpm            = struct.unpack_from("f", mm, OFFSET_SEQ_BPM)[0]
                    step_count     = struct.unpack_from("I", mm, OFFSET_SEQ_COUNT)[0]
                    step_pattern   = struct.unpack_from("Q", mm, OFFSET_SEQ_PATTERN)[0]
            return {
                "current_step":   current_step,
                "is_playing":     bool(is_playing),
                "trigger_active": bool(trigger_active),
                "bpm":            bpm,
                "step_count":     step_count,
                "step_pattern":   step_pattern,
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Audio analysis helpers (require FFTAnalyzerNode in chain)
    # ------------------------------------------------------------------

    # Bin resolution at 44100 Hz with FFT_SIZE=2048: 44100/2048 ≈ 21.53 Hz/bin
    FFT_BIN_HZ = 44100.0 / 2048.0

    def freq_to_bin(self, freq_hz: float) -> int:
        """Convert frequency in Hz to nearest FFT bin index."""
        return round(freq_hz / self.FFT_BIN_HZ)

    def read_rms(self) -> float:
        """Return max of left/right RMS from shared memory (0.0 if unavailable)."""
        data = self.read_shared_memory()
        if data is None:
            return 0.0
        return max(data["rms_left"], data["rms_right"])

    def read_fft_peak_bin(self, min_bin: int = 2) -> Optional[int]:
        """Return the FFT bin (left channel) with the highest magnitude, ignoring DC."""
        data = self.read_shared_memory()
        if data is None:
            return None
        fft = data["fft_left"]
        return max(range(min_bin, len(fft)), key=lambda i: fft[i])

    def wait_for_rms(self, threshold: float = 0.001, timeout: float = 2.0) -> bool:
        """Poll until RMS exceeds threshold. Returns True when threshold reached."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.read_rms() > threshold:
                return True
            time.sleep(0.02)
        return False

    def measure_peak_rms(self, duration: float = 0.25) -> float:
        """Return the maximum RMS observed over a measurement window."""
        peak = 0.0
        deadline = time.time() + duration
        while time.time() < deadline:
            rms = self.read_rms()
            if rms > peak:
                peak = rms
            time.sleep(0.02)
        return peak

    def wait_for_audio(self, timeout: float = 2.0) -> bool:
        """Wait until shared memory shows the audio callback is running.

        Checks that the sequence counter advances, which proves the JUCE audio
        callback is active. Peaks only go non-zero when an FFTAnalyzerNode is
        in the chain, so sequence advancement is the reliable headless signal.
        """
        initial = self.read_shared_memory()
        if initial is None:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.read_shared_memory()
            if data and data["sequence"] != initial["sequence"]:
                return True
            time.sleep(0.05)
        return False
