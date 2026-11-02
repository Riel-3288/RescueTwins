"""Run the pinned local ACME version with a parent-owned shutdown pipe."""
import signal
import sys
import threading
import time


def main():
    from acmecse.__main__ import parseArgs
    from acmecse.runtime import CSE
    from acmecse.etc.Constants import RuntimeConstants as RC
    from acmecse.etc.Types import CSEStatus

    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    if not CSE.startup(parseArgs()):
        print("ACME startup failed; inspect the CSE directory's logs folder.", file=sys.stderr, flush=True)
        return 1

    def watch_parent():
        # EOF also stops an orphan after its parent exits.
        for line in sys.stdin:
            if line.strip() == "shutdown":
                break
        stopped.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        deadline = time.monotonic() + 15
        while RC.cseStatus != CSEStatus.RUNNING:
            if time.monotonic() >= deadline:
                raise RuntimeError("ACME did not finish startup")
            time.sleep(.05)
        stopped.wait()
    finally:
        # ACME 2026.5.3's public shutdown() only stops its console loop.
        # This pinned version's finalizer closes and flushes TinyDB writers.
        CSE._shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
