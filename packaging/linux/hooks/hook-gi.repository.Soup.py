"""WebKit2 4.0 requires Soup 2.4; never collect Soup 3 into this build."""

from PyInstaller.utils.hooks.gi import GiModuleInfo


def hook(hook_api):
    info = GiModuleInfo("Soup", "2.4")
    if not info.available:
        raise RuntimeError("Soup 2.4 GI metadata is required for Ubuntu 22.04")
    binaries, datas, hiddenimports = info.collect_typelib_data()
    hook_api.add_binaries(binaries)
    hook_api.add_datas(datas)
    hook_api.add_imports(*hiddenimports)
