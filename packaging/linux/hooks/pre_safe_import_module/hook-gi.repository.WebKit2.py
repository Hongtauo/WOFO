"""WebKit2 is a dynamic GI namespace rather than a Python source module."""


def pre_safe_import_module(api):
    api.add_runtime_module(api.module_name)
