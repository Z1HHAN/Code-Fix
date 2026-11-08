"""Capture the entire unrotated candidate stream with a shared byte ceiling."""
import subprocess
from threading import Event, Lock, Thread


class CandidateLogs:
    def __init__(self, executable, name, folder, limit=1_048_576):
        self.executable, self.name, self.folder = executable, name, folder
        self.limit, self.total = limit, 0
        self.exceeded, self.failed = Event(), Event()
        self.lock = Lock()
        self.process = subprocess.Popen([executable, 'logs', '--follow', name],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.threads = []
        for stream, filename in ((self.process.stdout, 'candidate-stdout.txt'),
                                 (self.process.stderr, 'candidate-stderr.txt')):
            thread = Thread(target=self._read, args=(stream, folder / filename), daemon=True)
            thread.start()
            self.threads.append(thread)

    def _read(self, stream, path):
        try:
            with path.open('wb') as output:
                while data := stream.read1(8192):
                    with self.lock:
                        available = max(0, self.limit - self.total)
                        self.total += len(data)
                        output.write(data[:available])
                        exceeded = self.total > self.limit
                    if exceeded:
                        self.exceeded.set()
                        subprocess.run([self.executable, 'kill', self.name], capture_output=True, timeout=5)
                        break
        except (OSError, subprocess.SubprocessError):
            self.failed.set()

    def finish(self):
        # Stop the producer before declaring an audit complete. No later candidate
        # activity may occur between audit acceptance and container teardown.
        try:
            stopped = subprocess.run([self.executable, 'stop', '--time=0', self.name],
                                     capture_output=True, timeout=10)
            if stopped.returncode:
                self.failed.set()
            self.process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            self.failed.set()
            self.process.kill()
            self.process.wait(timeout=5)
        for thread in self.threads:
            thread.join(timeout=5)
            if thread.is_alive():
                self.failed.set()
        for stream in (self.process.stdout, self.process.stderr):
            if not any(thread.is_alive() for thread in self.threads):
                stream.close()
        complete = (not self.failed.is_set() and not self.exceeded.is_set()
                    and self.process.returncode == 0)
        return complete, self.exceeded.is_set()
