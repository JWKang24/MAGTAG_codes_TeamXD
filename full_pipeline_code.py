import maintenance_mode
import os
import supervisor

supervisor.runtime.autoreload = False

_MARKER = "/.start_espnow"


def _marker_exists():
    try:
        os.stat(_MARKER)
        return True
    except Exception:
        return False


def _write_marker():
    with open(_MARKER, "w") as f:
        f.write("1\n")


def _clear_marker():
    try:
        os.remove(_MARKER)
    except Exception:
        pass


if _marker_exists():
    # Phase 2: fresh boot into ESP-NOW runtime.
    _clear_marker()
    import mode_change_full_func
else:
    # Phase 1: run survey. After completion, force a clean reload.
    import user_survey
    try:
        _write_marker()
        supervisor.reload()
    except Exception:
        # Fallback if marker write/reload fails.
        import mode_change_full_func