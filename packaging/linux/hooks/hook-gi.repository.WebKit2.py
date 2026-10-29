"""Collect Ubuntu 22.04 WebKit 4.0 metadata, including GI dependencies."""

from PyInstaller.utils.hooks.gi import GiModuleInfo


def hook(hook_api):
    pending = [("WebKit2", "4.0")]
    seen = set()
    while pending:
        namespace, version = pending.pop()
        if namespace in seen:
            continue
        seen.add(namespace)
        info = GiModuleInfo(namespace, version)
        if not info.available:
            raise RuntimeError(f"Required GI namespace is missing: {namespace} {version}")
        binaries, datas, hiddenimports = info.collect_typelib_data()
        hook_api.add_binaries(binaries)
        hook_api.add_datas(datas)
        hook_api.add_imports(*hiddenimports)
        for dependency in info.dependencies:
            dependency_name, dependency_version = dependency.rsplit("-", 1)
            pending.append((dependency_name, dependency_version))
