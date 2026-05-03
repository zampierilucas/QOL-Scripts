import ctypes
import logging

logger = logging.getLogger(__name__)

# NVAPI function IDs
_NVAPI_INITIALIZE = 0x0150E828
_NVAPI_ENUM_DISPLAY_HANDLE = 0x9ABDD40D
_NVAPI_GET_DVC_INFO = 0x4085DE45
_NVAPI_SET_DVC_LEVEL = 0x172409B4

_MAX_DISPLAYS = 16

_nvapi = None
_query_interface = None
_display_handles: list = []
_initialized = False


class _NvDVCInfo(ctypes.Structure):
    _fields_ = [
        ("version",       ctypes.c_uint32),
        ("currentLevel",  ctypes.c_int32),
        ("minLevel",      ctypes.c_int32),
        ("maxLevel",      ctypes.c_int32),
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


def _get_dvc_info(display_index: int):
    """Return (currentLevel, minLevel, maxLevel) or None."""
    if display_index >= len(_display_handles):
        return None
    get_fn = _query(
        _NVAPI_GET_DVC_INFO,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_NvDVCInfo),
    )
    if not get_fn:
        return None

    info = _NvDVCInfo()
    info.version = ctypes.sizeof(_NvDVCInfo) | (1 << 16)
    if get_fn(_display_handles[display_index], 0, ctypes.byref(info)) != 0:
        return None
    return info.currentLevel, info.minLevel, info.maxLevel


def set_vibrance(level_percent: int, display_indices: list | None = None) -> bool:
    """
    Set digital vibrance for the given display indices.

    level_percent: 0-100.  50 = driver default (maps to 0 in the ±1024 range).
    display_indices: list of NVAPI display indices (0-based).  None = all displays.
    """
    if not _initialized and not init_nvapi():
        return False

    if display_indices is None:
        display_indices = list(range(len(_display_handles)))

    set_fn = _query(
        _NVAPI_SET_DVC_LEVEL,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int32,
    )
    if not set_fn:
        return False

    ok = True
    for idx in display_indices:
        dvc = _get_dvc_info(idx)
        if dvc is None:
            ok = False
            continue
        _, min_lvl, max_lvl = dvc
        target = int(min_lvl + (max_lvl - min_lvl) * level_percent / 100)
        status = set_fn(_display_handles[idx], 0, target)
        if status != 0:
            logger.error(f"NvAPI_SetDVCLevel failed for display {idx}: {status}")
            ok = False
        else:
            logger.debug(f"Display {idx}: DVC → {target} ({level_percent}%)")
    return ok
