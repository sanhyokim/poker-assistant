"""Bridge for communicating with the resident postflop solver CLI."""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
import time
from typing import Any


logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


class PostflopSolverBridge:
    """postflop_cli.exe との JSON行通信ブリッジ。

    Rust CLIプロセスを常駐させ、solve リクエストを送受信する。
    SPEC.md セクション5.10, 23.3 準拠。
    """

    def __init__(self, cli_path: str = "solver/bin/postflop_cli.exe") -> None:
        """Initialize the bridge with the solver CLI executable path.

        Args:
            cli_path: Path to the resident Rust CLI executable.
        """
        self.cli_path = cli_path
        self.process: subprocess.Popen[str] | None = None
        self._restart_failures: int = 0
        self._max_restart_failures: int = 3
        self._disabled: bool = False
        self._logger = logger

    @property
    def disabled(self) -> bool:
        """Return True when solver restarts have been permanently disabled."""
        return self._disabled

    def start(self) -> None:
        """Start the solver CLI and wait up to 5 seconds for the ready signal.

        Raises:
            RuntimeError: If the CLI process does not print ready in time.
            OSError: If the CLI executable cannot be started.
        """
        if self.is_alive():
            return

        self.process = subprocess.Popen(
            [self.cli_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        ready_event = threading.Event()
        stderr_thread = threading.Thread(
            target=self._read_stderr_until_ready,
            args=(ready_event,),
            daemon=True,
        )
        stderr_thread.start()

        if not ready_event.wait(timeout=5.0):
            self._kill_process()
            self.process = None
            raise RuntimeError("Solver CLI failed to start within 5 seconds")

        self._restart_failures = 0
        self._disabled = False
        pid = self.process.pid if self.process is not None else "unknown"
        self._logger.info("Solver CLI started (PID: %s)", pid)

    def solve(self, request: JsonDict, timeout: float = 12.0) -> JsonDict:
        """Send one solve request and return the solver response dictionary.

        Args:
            request: JSON-serializable request payload for the Rust CLI.
            timeout: Maximum seconds to wait for one stdout JSON response.

        Returns:
            Solver response dictionary, or a failure dictionary on errors.
        """
        if self._disabled:
            return self._error("Solver disabled after repeated failures")

        if not self.is_alive() and not self._try_restart():
            return self._error("Solver disabled after repeated failures")

        process = self.process
        if process is None or process.stdin is None or process.stdout is None:
            return self._error("Solver process pipes are unavailable")

        try:
            payload = json.dumps(request, ensure_ascii=False)
            process.stdin.write(f"{payload}\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            self._logger.error("Solver request write failed: %s", error)
            return self._error(f"Solver request write failed: {error}")

        self._logger.debug("Solver request sent")
        started_at = time.monotonic()
        result_queue: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)
        reader_thread = threading.Thread(
            target=self._read_json_response,
            args=(process, result_queue),
            daemon=True,
        )
        reader_thread.start()

        try:
            result = result_queue.get(timeout=timeout)
        except queue.Empty:
            return self._error(f"Solver timeout (no response within {timeout}s)")

        if isinstance(result, BaseException):
            self._logger.error("Solver response read failed: %s", result)
            return self._error(f"Solver response read failed: {result}")

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        self._logger.debug("Solver response received in %sms", elapsed_ms)

        try:
            response = json.loads(result)
        except json.JSONDecodeError:
            self._logger.error("Invalid JSON response from solver: %r", result)
            return self._error("Invalid JSON response from solver")

        if not isinstance(response, dict):
            self._logger.error("Non-object JSON response from solver: %r", response)
            return self._error("Invalid JSON response from solver")

        return response

    def is_alive(self) -> bool:
        """Return True when the solver process exists and is still running."""
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        """Terminate the solver CLI process if it is running."""
        if self.process is None:
            return

        process = self.process
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            self.process = None

        self._logger.info("Solver CLI stopped")

    def _try_restart(self) -> bool:
        """Attempt to restart the solver process after a health check failure."""
        if self._restart_failures >= self._max_restart_failures:
            self._disabled = True
            self._logger.error(
                "Solver disabled after %s consecutive restart failures",
                self._restart_failures,
            )
            return False

        self.stop()
        try:
            self.start()
        except Exception as error:
            self._restart_failures += 1
            self._logger.warning(
                "Solver restart failed (%s/%s): %s",
                self._restart_failures,
                self._max_restart_failures,
                error,
            )
            return False

        return True

    def _read_stderr_until_ready(self, ready_event: threading.Event) -> None:
        """Read stderr until the solver emits ready."""
        process = self.process
        if process is None or process.stderr is None:
            return

        for line in process.stderr:
            message = line.strip()
            if message == "ready":
                ready_event.set()
                return
            if message:
                self._logger.error("Solver CLI stderr: %s", message)

    def _read_json_response(
        self,
        process: subprocess.Popen[str],
        result_queue: queue.Queue[str | BaseException],
    ) -> None:
        """Read stdout until a complete JSON response is assembled."""
        if process.stdout is None:
            result_queue.put(RuntimeError("stdout pipe is unavailable"))
            return

        buffer = ""
        try:
            while True:
                line = process.stdout.readline()
                if not line:
                    if buffer:
                        result_queue.put(buffer)
                    else:
                        result_queue.put(RuntimeError("Solver process closed stdout"))
                    return

                buffer += line
                try:
                    json.loads(buffer)
                except json.JSONDecodeError:
                    continue

                result_queue.put(buffer)
                return
        except BaseException as error:
            result_queue.put(error)

    def _kill_process(self) -> None:
        """Kill the current solver process if it is still alive."""
        if self.process is None:
            return

        try:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
        except Exception as error:
            self._logger.error("Solver CLI kill failed: %s", error)

    def _error(self, message: str) -> JsonDict:
        """Build a standard solve failure response dictionary."""
        return {"success": False, "error": message}
