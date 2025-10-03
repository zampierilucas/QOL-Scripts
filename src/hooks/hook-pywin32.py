from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

def hook(hook_api):
    """
    Custom hook for pywin32 to ensure all necessary modules and binaries are included.
    """
    # Collect all submodules from pywin32 and win32com
    hiddenimports = collect_submodules('win32com') + collect_submodules('pywin32')

    # Add common pywin32 modules that might be missed
    hiddenimports += [
        'win32com.client',
        'win32com.client.gencache',
        'win32com.gen_py',
        'win32api',
        'win32gui',
        'win32con',
        'winshell',
        'winreg'
    ]

    # Collect all dynamic libraries (DLLs) from pywin32
    binaries = collect_dynamic_libs('pywin32')

    # Assign to the hook API
    hook_api.hiddenimports = list(set(hiddenimports))
    hook_api.binaries = binaries