import logging
import threading
import time

from eink.display import EInkDisplay
from eink.screens import boot_screen, ready_screen, rotating_screens


LOGGER = logging.getLogger(__name__)
BOOT_SCREEN_SECONDS = 2
READY_SCREEN_SECONDS = 2
ROTATE_SECONDS = 12
_THREAD = None
_LOCK = threading.Lock()


def _run(snapshot_provider):
    try:
        display = EInkDisplay()
        display.initialize()
    except Exception:
        LOGGER.exception("E-ink display initialization failed")
        return

    try:
        snapshot = snapshot_provider()
        display.render(boot_screen(display.size, snapshot))
        time.sleep(BOOT_SCREEN_SECONDS)

        snapshot = snapshot_provider()
        display.render(ready_screen(display.size, snapshot))
        time.sleep(READY_SCREEN_SECONDS)

        screen_index = 0
        while True:
            try:
                snapshot = snapshot_provider()
                screens = rotating_screens(display.size, snapshot)
                display.render(screens[screen_index % len(screens)])
                screen_index += 1
            except Exception:
                LOGGER.exception("E-ink screen update failed")
            time.sleep(ROTATE_SECONDS)
    except Exception:
        LOGGER.exception("E-ink daemon stopped unexpectedly")


def start_daemon(snapshot_provider):
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(
            target=_run,
            args=(snapshot_provider,),
            name="wraith-eink-daemon",
            daemon=True,
        )
        _THREAD.start()
