"""Fuel benchmark bot: one benchmark per turn on the first champion, results printed as BENCH lines."""
import gc

import cambc
import _cambc
import benches


def main():
    queue = None
    while True:
        try:
            if not cambc.is_draft():
                if queue is None:
                    # The first champion to claim shared-memory byte 7 runs the benchmarks.
                    first = _cambc.shared_memory_get(7) == 0
                    _cambc.shared_memory_set(7, 1)
                    # Run every benchmark twice: garbage collector on, then off.
                    queue = [*benches.BENCHES, None, *benches.BENCHES] if first else []
                    if queue:
                        print("BENCH start fuel", _cambc.fuel_remaining(), flush=True)
                if queue:
                    entry = queue.pop(0)
                    if entry is None:
                        gc.disable()
                        print("BENCH gc disabled", flush=True)
                    else:
                        benches.run(*entry)
        except Exception as error:
            print(f"bench error: {error!r}", flush=True)
        cambc.end_turn()


if __name__ == "__main__":
    main()
