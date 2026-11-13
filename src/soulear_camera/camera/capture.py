"""Capture behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import re
import shutil
import subprocess
import threading

from .pcap import CaptureError, PcapStreamReader


class _CaptureMixin:
    def detect_interface(self: SoulearCamera) -> str:
        """Return the configured interface or detect the camera route.

        Returns
        -------
        str
            Interface name used for packet capture.

        Raises
        ------
        CaptureError
            If ``ip`` is unavailable or no interface can be determined.
        """
        if self.interface:
            return self.interface
        ip_command = shutil.which("ip")
        if ip_command is None:
            raise CaptureError("ip")
        try:
            result = subprocess.run(  # noqa: S603
                [ip_command, "route", "get", self.camera_ip],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            match = re.search(r"\bdev\s+(\S+)", result.stdout)
            if match:
                self.interface = match.group(1)
                return self.interface
        except (OSError, subprocess.SubprocessError) as exc:
            self.logger.debug(f"Interface detection failed: {exc}")
        raise CaptureError("interface")

    def start_dumpcap(self: SoulearCamera) -> None:
        """Start dumpcap and a background thread to drain its stderr.

        Raises
        ------
        CaptureError
            If dumpcap is unavailable or the network interface is unknown.
        OSError
            If the dumpcap process cannot be started.
        """
        dumpcap = shutil.which("dumpcap")
        if dumpcap is None:
            raise CaptureError("dumpcap")
        interface = self.detect_interface()

        command = [
            dumpcap,
            "-q",
            "-F",
            "pcap",
            "-i",
            interface,
            "-s",
            "0",
            "-w",
            "-",
        ]
        self.logger.debug("%s %s", "Capture:", " ".join(command))
        self.dumpcap_process = subprocess.Popen(  # noqa: S603
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        threading.Thread(
            target=self.dumpcap_stderr_loop,
            daemon=True,
        ).start()

    def dumpcap_stderr_loop(self: SoulearCamera) -> None:
        """Read dumpcap diagnostics and print them when debug mode is enabled."""
        process = self.dumpcap_process
        if process is None or process.stderr is None:
            return
        for raw_line in process.stderr:
            line = raw_line.decode(errors="replace").strip()
            if line and self.debug:
                self.logger.debug(f"[dumpcap] {line}")

    def capture_loop(self: SoulearCamera) -> None:
        """Run optional dumpcap capture and dispatch Ethernet frames."""
        try:
            self.start_dumpcap()
            reader = self._dumpcap_reader()
            self.logger.info(f"Optional dumpcap capture active on {self.interface}")
            for ethernet_frame in reader.packets():
                if not self.running:
                    break
                self.process_ethernet_frame(ethernet_frame)
        except Exception as exc:
            if self.running:
                self.logger.exception("CAPTURE ERROR")
                self.signals.status.emit(f"Capture-Fehler: {exc}")

    def _dumpcap_reader(self: SoulearCamera) -> PcapStreamReader:
        """Create a PCAP reader for the running dumpcap process.

        Returns
        -------
        PcapStreamReader
            Reader connected to dumpcap's standard output.

        Raises
        ------
        CaptureError
            If the process or its output stream is unavailable.
        """
        process = self.dumpcap_process
        if process is None or process.stdout is None:
            raise CaptureError("stdout")
        return PcapStreamReader(process.stdout)
