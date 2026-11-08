"""Windows reviewed-process containment, using a gated trusted bootstrap.

The child cannot launch the command until assignment succeeds. Job limits do
not permit breakaway. See Microsoft Learn: procthread/job-objects.
"""
import ctypes
from ctypes import wintypes as W
from pathlib import Path
import subprocess
import sys
import time


class BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                ('flags', W.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                ('active_limit', W.DWORD), ('affinity', ctypes.c_size_t),
                ('priority', W.DWORD), ('scheduling', W.DWORD)]


class IO(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ('ro', 'wo', 'oo', 'rb', 'wb', 'ob')]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('io', IO), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [(n, ctypes.c_longlong) for n in ('user', 'kernel', 'period_user', 'period_kernel')] + [
        (n, W.DWORD) for n in ('faults', 'total', 'active', 'terminated')]


class WindowsJob:
    def __init__(self):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('CreateJobObjectW', [ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
            ('SetInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
            ('AssignProcessToJobObject', [W.HANDLE, W.HANDLE], W.BOOL),
            ('TerminateJobObject', [W.HANDLE, W.UINT], W.BOOL),
            ('QueryInformationJobObject', [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.c_void_p], W.BOOL),
            ('CloseHandle', [W.HANDLE], W.BOOL),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())

    def start(self, command, **kwargs):
        gate = Path(__file__).with_name('process_gate.py')
        process = subprocess.Popen([sys.executable, '-I', '-B', str(gate), *command],
                                   stdin=subprocess.PIPE, **kwargs)
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, W.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
            process.stdin.write(b'go\n')
            process.stdin.flush()
            process.stdin.close()
            process.stdin = None
            return process
        except BaseException:
            process.kill()
            process.communicate(timeout=5)
            raise

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def active(self):
        info = Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.active

    def close(self):
        if not self.handle:
            return False
        try:
            self.terminate()
            deadline = time.monotonic() + 5
            while self.active() and time.monotonic() < deadline:
                time.sleep(.02)
            return self.active() == 0
        finally:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
