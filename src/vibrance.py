import ctypes
import logging
from threading import Thread

logger = logging.getLogger(__name__)

# NVAPI function IDs
_NVAPI_INITIALIZE = 0x0150E828
_NVAPI_ENUM_DISPLAY_HANDLE = 0x9ABDD40D
_NVAPI_GET_DVC_INFO_EX = 0x0E45002D
_NVAPI_SET_DVC_LEVEL_EX = 0x4A82C2B1
_NVAPI_GET_ASSOC_DISPLAY_NAME = 0x22A78B05

_MAX_DISPLAYS = 16

_nvapi = None
_query_interface = None
_display_handles: list = []
_initialized = False


class _NvDVCInfoEx(ctypes.Structure):
    """Extended DVC info — exposes ``defaultLevel`` (the driver's "no
    enhancement" baseline) and uses the same scale as NVIDIA Control Panel:
    0 = grayscale, default (typically 50) = no enhancement, max (typically
    100) = max enhancement. The legacy ``NvAPI_GetDVCInfo`` reports a
    different, compressed range that doesn't line up with NCP's slider, so
    we use the Ex variant exclusively."""
    _fields_ = [
        ("version",       ctypes.c_uint32),
        ("currentLevel",  ctypes.c_int32),
        ("minLevel",      ctypes.c_int32),
        ("maxLevel",      ctypes.c_int32),
        ("defaultLevel",  ctypes.c_int32),
    ]


def _query(func_id, restype, *argtypes):
    """Return a ctypes callable for an NVAPI function ID, or None."""
    ptr = _query_interface(func_id)
    if not ptr:
        return None
    return ctypes.CFUNCTYPE(restype, *argtypes)(ptr)


def init_nvapi() -> bool:
    """Load NVAPI and enumerate display handles. Returns True on success."""
    global _nvapi, _query_interface, _display_handles, _initialized

    if _initialized:
        return True

    for dll_name in ("nvapi64.dll", "nvapi.dll"):
        try:
            _nvapi = ctypes.WinDLL(dll_name)
            break
        except OSError:
            continue
    else:
        logger.warning("NVAPI not found — NVIDIA drivers not installed or no NVIDIA GPU")
        return False

    try:
        _query_interface = _nvapi.nvapi_QueryInterface
        _query_interface.restype = ctypes.c_void_p
        _query_interface.argtypes = [ctypes.c_uint32]
    except AttributeError:
        logger.error("nvapi_QueryInterface not found")
        return False

    init_fn = _query(_NVAPI_INITIALIZE, ctypes.c_int32)
    if not init_fn or init_fn() != 0:
        logger.error("NvAPI_Initialize failed")
        return False

    enum_fn = _query(
        _NVAPI_ENUM_DISPLAY_HANDLE,
        ctypes.c_int32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    )
    if not enum_fn:
        logger.error("NvAPI_EnumNvidiaDisplayHandle not found")
        return False

    _display_handles = []
    for i in range(_MAX_DISPLAYS):
        handle = ctypes.c_void_p()
        status = enum_fn(i, ctypes.byref(handle))
        if status != 0:
            break
        if handle.value:
            _display_handles.append(handle.value)

    logger.info(f"NVAPI ready — {len(_display_handles)} display(s) found")
    _initialized = True
    return True


def get_display_count() -> int:
    if not _initialized:
        init_nvapi()
    return len(_display_handles)


def get_displays() -> list[tuple[int, str]]:
    """Return [(nvapi_index, gdi_name), ...] for each NVIDIA-attached display.

    gdi_name is the Windows GDI device path like '\\\\.\\DISPLAY1', or '' if
    the lookup failed."""
    if not _initialized and not init_nvapi():
        return []

    get_name_fn = _query(
        _NVAPI_GET_ASSOC_DISPLAY_NAME,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_char * 64,
    )

    out = []
    for i, handle in enumerate(_display_handles):
        gdi_name = ""
        if get_name_fn:
            buf = (ctypes.c_char * 64)()
            try:
                if get_name_fn(handle, buf) == 0:
                    gdi_name = buf.value.decode('ascii', errors='ignore')
            except Exception as e:
                logger.debug(f"GetAssociatedNvidiaDisplayName failed for {i}: {e}")
        out.append((i, gdi_name))
    return out


def _get_dvc_info(display_index: int):
    """Return (currentLevel, minLevel, maxLevel, defaultLevel) on the
    NCP-aligned scale (0 = grayscale, default = no enhancement, max = max
    enhancement), or ``None`` on failure."""
    if display_index >= len(_display_handles):
        return None
    get_fn = _query(
        _NVAPI_GET_DVC_INFO_EX,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_NvDVCInfoEx),
    )
    if not get_fn:
        return None

    info = _NvDVCInfoEx()
    info.version = ctypes.sizeof(_NvDVCInfoEx) | (1 << 16)
    if get_fn(_display_handles[display_index], 0, ctypes.byref(info)) != 0:
        return None
    return info.currentLevel, info.minLevel, info.maxLevel, info.defaultLevel


def set_vibrance(level_percent: int, display_indices: list | None = None) -> bool:
    """
    Set digital vibrance for the given display indices.

    Uses ``NvAPI_SetDVCLevelEx`` so the percentage is on the same scale NVIDIA
    Control Panel shows: 0% = grayscale, 50% = driver default (no
    enhancement), 100% = max enhancement.

    level_percent: 0-100.
    display_indices: list of NVAPI display indices (0-based). None = all displays.
    """
    if not _initialized and not init_nvapi():
        return False

    if display_indices is None:
        display_indices = list(range(len(_display_handles)))

    set_fn = _query(
        _NVAPI_SET_DVC_LEVEL_EX,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_NvDVCInfoEx),
    )
    if not set_fn:
        return False

    ok = True
    for idx in display_indices:
        dvc = _get_dvc_info(idx)
        if dvc is None:
            ok = False
            continue
        _, min_lvl, max_lvl, default_lvl = dvc
        target = int(min_lvl + (max_lvl - min_lvl) * level_percent / 100)
        write = _NvDVCInfoEx()
        write.version = ctypes.sizeof(_NvDVCInfoEx) | (1 << 16)
        write.currentLevel = target
        write.minLevel = min_lvl
        write.maxLevel = max_lvl
        write.defaultLevel = default_lvl
        status = set_fn(_display_handles[idx], 0, ctypes.byref(write))
        if status != 0:
            logger.error(f"NvAPI_SetDVCLevelEx failed for display {idx}: {status}")
            ok = False
        else:
            logger.debug(f"Display {idx}: DVC -> {target} ({level_percent}%)")
    return ok


class VibranceFocusConsumer:
    """Daemon thread that subscribes to a FocusMonitor and switches NVIDIA
    digital vibrance between 'game' and 'default' levels based on the
    foreground window."""

    def __init__(self, settings, focus_monitor):
        self.settings = settings
        self.focus_monitor = focus_monitor
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = Thread(target=self._run, daemon=True, name="vibrance-focus")
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        sub = self.focus_monitor.subscribe()
        while self._running:
            title = sub.wait()
            if title is None:
                return
            try:
                self._apply(title)
            except Exception as e:
                logger.error(f"Vibrance consumer error: {e}")

    def _apply(self, focused_title: str):
        if not self.settings.data.get("vibrance_enabled", False):
            return
        vibrance_games = self.settings.data.get("games_vibrance", [])
        is_vibrance_game = focused_title in vibrance_games
        vibrance_displays = self.settings.data.get("vibrance_displays", []) or None
        level = (self.settings.data.get("vibrance_game_level", 75)
                 if is_vibrance_game
                 else self.settings.data.get("vibrance_default_level", 50))
        logger.debug(f"Vibrance: {'game' if is_vibrance_game else 'default'} -> {level}%")
        set_vibrance(level, vibrance_displays)
