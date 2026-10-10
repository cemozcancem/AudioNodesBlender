import sys
import os
import plistlib
import ctypes
import ctypes.util

# Module-level storage to keep ctypes objects alive until the async callback fires
_keep_alive = []


# ── Info.plist patching ───────────────────────────────────────────────────────

MIC_USAGE_KEY = "NSMicrophoneUsageDescription"
MIC_USAGE_VALUE = "Audio Nodes requires microphone access for real-time audio input processing."


def _find_info_plist():
    """
    Walk up from sys.executable to find Blender.app/Contents/Info.plist.
    Returns the path string, or None if not found.
    """
    path = os.path.realpath(sys.executable)
    while path and path != os.sep:
        path = os.path.dirname(path)
        candidate = os.path.join(path, "Info.plist")
        if os.path.isfile(candidate):
            return candidate
    return None


def check_plist_patched():
    """
    Returns True if Blender's Info.plist already contains NSMicrophoneUsageDescription.
    Returns None if the plist can't be found or read.
    """
    if sys.platform != "darwin":
        return True
    plist_path = _find_info_plist()
    if not plist_path:
        return None
    try:
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
        return bool(data.get(MIC_USAGE_KEY))
    except Exception:
        return None


def _read_plist():
    plist_path = _find_info_plist()
    if not plist_path:
        return None, None
    try:
        with open(plist_path, "rb") as f:
            return plist_path, plistlib.load(f)
    except Exception:
        return plist_path, None


def _bundle_id():
    """Return Blender's CFBundleIdentifier, or None."""
    _, data = _read_plist()
    if data:
        return data.get("CFBundleIdentifier")
    return None


def _bundle_root_for_plist(plist_path):
    """Given .../Blender.app/Contents/Info.plist, return .../Blender.app."""
    contents = os.path.dirname(plist_path)
    if os.path.basename(contents) == "Contents":
        return os.path.dirname(contents)
    return None


def _resign_bundle(bundle_path):
    """Ad-hoc re-sign the bundle after modifying its Info.plist.

    Editing Info.plist invalidates the existing code signature, after which
    macOS reports `spctl: invalid Info.plist (plist or signature have been
    modified)` and may SIGKILL the app on launch (especially when combined
    with a com.apple.quarantine xattr). Ad-hoc signing (`--sign -`) produces
    a valid (self-signed, untrusted) signature that Gatekeeper accepts for
    locally-installed apps. Also strips quarantine while we're at it.
    """
    import subprocess
    msgs = []

    try:
        subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", bundle_path],
            capture_output=True, text=True, timeout=120, check=True,
        )
        msgs.append("Re-signed bundle (ad-hoc).")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "").strip()
        msgs.append(f"Re-sign warning: {err or 'codesign failed'}")
    except Exception as e:
        msgs.append(f"Re-sign skipped: {e}")

    try:
        subprocess.run(
            ["xattr", "-rd", "com.apple.quarantine", bundle_path],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        pass

    return msgs


def patch_info_plist():
    """
    Add NSMicrophoneUsageDescription to Blender's Info.plist, then ad-hoc
    re-sign the bundle so the modification doesn't break code-signing.
    Returns (success: bool, message: str).
    """
    if sys.platform != "darwin":
        return True, "Not macOS — no patch needed."

    plist_path, data = _read_plist()
    if not plist_path:
        return False, "Could not locate Blender's Info.plist."
    if data is None:
        return False, f"Could not read {plist_path}."

    try:
        if data.get(MIC_USAGE_KEY):
            return True, "Info.plist already contains the microphone usage key."

        data[MIC_USAGE_KEY] = MIC_USAGE_VALUE

        with open(plist_path, "wb") as f:
            plistlib.dump(data, f)

    except PermissionError:
        return False, (
            f"Permission denied writing to {plist_path}\n"
            "macOS App Management protects /Applications. Either grant your "
            "terminal/Blender App Management permission in System Settings > "
            "Privacy & Security, or move Blender out of /Applications."
        )
    except Exception as e:
        return False, f"Failed to patch Info.plist: {e}"

    bundle = _bundle_root_for_plist(plist_path)
    sign_msgs = _resign_bundle(bundle) if bundle else []

    msg = f"Patched {plist_path}"
    if sign_msgs:
        msg += "\n" + "\n".join(sign_msgs)
    msg += "\nRestart Blender, then click Request Microphone Access again."
    return True, msg


def reset_tcc_permission():
    """
    Reset Blender's microphone TCC entry so macOS treats it as 'not determined' again.
    Returns (success: bool, message: str).
    """
    if sys.platform != "darwin":
        return True, "Not macOS."

    import subprocess

    bundle_id = _bundle_id()
    if not bundle_id:
        return False, "Could not determine Blender's bundle identifier."

    try:
        result = subprocess.run(
            ["tccutil", "reset", "Microphone", bundle_id],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True, f"Reset microphone permission for {bundle_id}.\nNow click Request Microphone Access."
        else:
            err = result.stderr.strip() or result.stdout.strip()
            return False, f"tccutil failed: {err}"
    except Exception as e:
        return False, f"Failed to run tccutil: {e}"


def check_mic_status():
    """
    Returns one of: 'authorized', 'denied', 'restricted', 'not_determined', 'unavailable'.
    On non-macOS platforms always returns 'authorized'.
    """
    if sys.platform != 'darwin':
        return 'authorized'

    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        return _int_to_status(status)
    except ImportError:
        pass

    return _check_mic_ctypes()


def request_mic_permission(on_done=None):
    """
    Show the macOS microphone permission dialog if not yet determined.
    on_done(granted: bool) is called on an arbitrary thread when the user responds.
    Returns the current status string immediately (before the dialog is answered).
    """
    if sys.platform != 'darwin':
        if on_done:
            on_done(True)
        return 'authorized'

    status = check_mic_status()
    if status == 'authorized':
        if on_done:
            on_done(True)
        return 'authorized'
    if status in ('denied', 'restricted'):
        if on_done:
            on_done(False)
        return status
    if status == 'unavailable':
        if on_done:
            on_done(False)
        return 'unavailable'

    # status == 'not_determined' — show the dialog
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio,
            lambda granted: on_done(bool(granted)) if on_done else None,
        )
        return 'requesting'
    except ImportError:
        pass

    return _request_mic_ctypes(on_done)


# ── ctypes implementation (no PyObjC) ────────────────────────────────────────

def _int_to_status(n):
    return {0: 'not_determined', 1: 'restricted', 2: 'denied', 3: 'authorized'}.get(int(n), 'unavailable')


def _load_av():
    """Load libobjc + AVFoundation. Returns libobjc or None on failure."""
    path = ctypes.util.find_library('objc')
    if not path:
        return None
    try:
        lib = ctypes.cdll.LoadLibrary(path)
        ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/AVFoundation.framework/AVFoundation'
        )
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.objc_getClass.argtypes = [ctypes.c_char_p]
        lib.sel_registerName.restype = ctypes.c_void_p
        lib.sel_registerName.argtypes = [ctypes.c_char_p]
        return lib
    except OSError:
        return None


def _av_media_audio_string(lib):
    """Return an NSString* for 'soun' (AVMediaTypeAudio)."""
    NSString = lib.objc_getClass(b'NSString')
    sel = lib.sel_registerName(b'stringWithUTF8String:')
    lib.objc_msgSend.restype = ctypes.c_void_p
    lib.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
    return lib.objc_msgSend(NSString, sel, b'soun')


def _check_mic_ctypes():
    try:
        lib = _load_av()
        if not lib:
            return 'unavailable'
        av_audio = _av_media_audio_string(lib)
        AVCapture = lib.objc_getClass(b'AVCaptureDevice')
        sel = lib.sel_registerName(b'authorizationStatusForMediaType:')
        lib.objc_msgSend.restype = ctypes.c_long
        lib.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        status = lib.objc_msgSend(AVCapture, sel, av_audio)
        return _int_to_status(status)
    except Exception:
        return 'unavailable'


def _request_mic_ctypes(on_done):
    """Trigger the macOS mic dialog using the Objective-C block ABI via ctypes."""
    try:
        lib = _load_av()
        if not lib:
            return 'unavailable'

        av_audio = _av_media_audio_string(lib)

        # Build a minimal Objective-C block struct (global block, no captures).
        INVOKE_TYPE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_bool)

        class BlockDescriptor(ctypes.Structure):
            _fields_ = [('reserved', ctypes.c_ulong), ('size', ctypes.c_ulong)]

        class Block(ctypes.Structure):
            _fields_ = [
                ('isa', ctypes.c_void_p),
                ('flags', ctypes.c_int),
                ('reserved', ctypes.c_int),
                ('invoke', ctypes.c_void_p),
                ('descriptor', ctypes.POINTER(BlockDescriptor)),
            ]

        def _invoke(block_ptr, granted):
            if on_done:
                on_done(bool(granted))

        invoke_cb = INVOKE_TYPE(_invoke)
        descriptor = BlockDescriptor(0, ctypes.sizeof(Block))

        # _NSConcreteGlobalBlock is the class descriptor for global blocks.
        # block.isa must be &_NSConcreteGlobalBlock (the address of the symbol),
        # not the value stored there — same as C's `block.isa = &_NSConcreteGlobalBlock`.
        try:
            global_block_sym = (ctypes.c_uint8 * 1).in_dll(lib, '_NSConcreteGlobalBlock')
        except ValueError:
            libsys = ctypes.cdll.LoadLibrary(ctypes.util.find_library('System'))
            global_block_sym = (ctypes.c_uint8 * 1).in_dll(libsys, '_NSConcreteGlobalBlock')

        block = Block()
        block.isa = ctypes.addressof(global_block_sym)  # address OF the symbol
        block.flags = 1 << 28  # BLOCK_IS_GLOBAL
        block.reserved = 0
        block.invoke = ctypes.cast(invoke_cb, ctypes.c_void_p).value
        block.descriptor = ctypes.pointer(descriptor)

        # Keep alive until callback fires (including global_block_sym so addressof stays valid)
        _keep_alive.append((block, invoke_cb, descriptor, global_block_sym))

        AVCapture = lib.objc_getClass(b'AVCaptureDevice')
        sel = lib.sel_registerName(b'requestAccessForMediaType:completionHandler:')
        lib.objc_msgSend.restype = None
        lib.objc_msgSend.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        lib.objc_msgSend(AVCapture, sel, av_audio, ctypes.byref(block))
        return 'requesting'

    except Exception as e:
        print(f"Audio Nodes: mic permission request failed: {e}")
        return 'unavailable'


# ── Legacy entry-point (called from register()) ───────────────────────────────

def request_audio_permission():
    """Called at add-on registration; silently checks/requests without blocking."""
    request_mic_permission()
