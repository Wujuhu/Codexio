"""Keep Codexio's bundled runtime out of external desktop/CLI processes."""
import os


def external_environment():
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith(("_PYI_", "PYINSTALLER_")) or key in (
                "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM", "PYTHONHOME",
                "CODEXIO_UPDATE_JOB", "CODEXIO_SKIP_UPDATE_ONCE"):
            environment.pop(key, None)
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH"):
        previous = environment.pop(key + "_ORIG", None)
        if previous is not None:
            environment[key] = previous
        else:
            environment.pop(key, None)
    return environment
