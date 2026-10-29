"""Support PyInstaller releases without a built-in Soup namespace hook."""


def pre_safe_import_module(api):
    api.add_runtime_module(api.module_name)
