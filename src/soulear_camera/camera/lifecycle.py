"""Lifecycle behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import logging
import threading
import time
from contextlib import suppress


class _LifecycleMixin:
    def start(self: SoulearCamera) -> None:
        """Initialize sockets and threads, then open the camera video stream."""
        if self.running:
            return
        self.running = True
        self.camera_connected = False

        try:
            self.create_native_sockets()

            # Start the receive backend before the first OPEN_VIDEO command.
            # Normal operation needs no packet-capture privileges or interface.
            if self.use_dumpcap:
                self.capture_thread = threading.Thread(
                    target=self.capture_loop,
                    daemon=True,
                )
                self.capture_thread.start()
                time.sleep(0.4)
            else:
                self.start_socket_receivers()
                time.sleep(0.05)

            if self.keepalives_enabled:
                self.send_ne7_probe()
                time.sleep(0.15)

            if self.keepalives_enabled:
                self.keepalive_thread = threading.Thread(
                    target=self.keepalive_loop,
                    daemon=True,
                )
                self.keepalive_thread.start()

            online = self.camera_check_online()

            if online and self.status_payload is not None and self.capabilities_byte & 0x10:
                self.set_camera_time()

            # Native proOpenVideo (0x00010004)
            self.open_video_normal("initial")

            if self.online_monitor_enabled:
                self.online_thread = threading.Thread(
                    target=self.online_monitor_loop,
                    daemon=True,
                )
                self.online_thread.start()

            self.watchdog_thread = threading.Thread(
                target=self.watchdog_loop,
                daemon=True,
            )
            self.watchdog_thread.start()

        except OSError as exc:
            self.logger.log(
                logging.ERROR,
                "START ERROR: network setup failed for local IP %s: %s",
                self.local_ip,
                exc,
            )
            self.signals.status.emit(f"Startfehler: {exc}")
            self.stop()
        except Exception as exc:
            self.logger.exception("START ERROR")
            self.signals.status.emit(f"Startfehler: {exc}")
            self.stop()

    def stop(self: SoulearCamera) -> None:
        """Stop recording, close sockets, and terminate optional capture."""
        if self.camera_recording or self.record_ffmpeg is not None:
            try:
                self.stop_video_recording()
            except Exception:
                self.logger.exception("RECORD_STOP during shutdown failed")

        if not self.running:
            self.camera_connected = False
            return
        self.running = False
        self.camera_connected = False
        self.logger.info("Stopping Soulear...")

        for sock in (
            self.video_socket,
            self.video2_socket,
            self.presence_video_socket,
            self.presence_control1_socket,
            self.presence_control2_socket,
        ):
            if sock is not None:
                with suppress(OSError):
                    sock.close()

        if self.dumpcap_process is not None:
            try:
                self.dumpcap_process.terminate()
                self.dumpcap_process.wait(timeout=1.5)
            except Exception:
                with suppress(Exception):
                    self.dumpcap_process.kill()
            self.dumpcap_process = None

        self.logger.info("Soulear stopped.")
