"""A progress bar for the long-running tools, on stderr, safe to drive from several threads.

    from progress import Progress
    bar = Progress(len(items), "dumping")
    for item in items:
        work(item)
        bar.step(note=item)
    bar.done()

Prints one line that is rewritten in place on a terminal and appended as plain lines (every
few percent) when stderr is redirected to a file, so a background run's log still shows how far
it got.
"""
import sys
import threading
import time


def _hms(seconds):
    seconds = int(max(seconds, 0))
    return f"{seconds // 3600:d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60:02d}:{seconds % 60:02d}"


class Progress:
    def __init__(self, total, label="", width=30, stream=None):
        self.total = max(int(total), 0)
        self.label = label
        self.width = width
        self.stream = stream or sys.stderr
        self.count = 0
        self.started = time.time()
        self.lock = threading.Lock()
        self.tty = hasattr(self.stream, "isatty") and self.stream.isatty()
        self.last_percent = -1
        self.last_draw = 0.0
        self.render(force=True)

    def step(self, n=1, note=""):
        with self.lock:
            self.count += n
            self.render(note=note)

    def render(self, note="", force=False):
        now = time.time()
        percent = 100 * self.count // self.total if self.total else 100
        # On a terminal redraw at most ten times a second; in a file write a line every 2%.
        if not force:
            if self.tty and now - self.last_draw < 0.1 and self.count < self.total:
                return
            if not self.tty and percent - self.last_percent < 2 and self.count < self.total:
                return
        self.last_draw, self.last_percent = now, percent
        elapsed = now - self.started
        rate = self.count / elapsed if elapsed > 0 and self.count else 0.0
        eta = (self.total - self.count) / rate if rate else 0.0
        filled = self.width * self.count // self.total if self.total else self.width
        bar = "#" * filled + "-" * (self.width - filled)
        text = (f"{self.label} [{bar}] {self.count}/{self.total} {percent:3d}% {_hms(elapsed)}"
                + (f" eta {_hms(eta)}" if self.count < self.total and rate else "")
                + (f"  {note}" if note else ""))
        if self.tty:
            self.stream.write("\r" + text[:200].ljust(120))
        else:
            self.stream.write(text[:200] + "\n")
        self.stream.flush()

    def done(self, note=""):
        with self.lock:
            self.count = self.total
            self.render(note=note, force=True)
            if self.tty:
                self.stream.write("\n")
                self.stream.flush()
