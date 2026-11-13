"""Watchdog behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import time


class _WatchdogMixin:
    def watchdog_loop(self: SoulearCamera) -> None:
        """Retry stream opening after startup failure or a sustained packet stall.

        Raises
        ------
        OSError
            If a fallback or reopen command cannot use its UDP socket.
        """
        # Give normal proOpenVideo time to answer and produce data.
        if not self.first_video_event.wait(1.0) and self.running and self.fallback_force:
            self.logger.warning("No video after normal open; trying the force-open fallback once")
            try:
                self.open_video_force("no-video-fallback")
            except OSError:
                self.logger.exception("Force-open fallback failed")
                return

        while self.running:
            time.sleep(0.25)
            if not self.auto_reopen:
                continue
            if self.reconfiguring:
                continue
            last = self.last_video_packet_time
            if last is None:
                continue
            idle = time.monotonic() - last
            if idle < 1.5:
                continue
            now = time.monotonic()
            if now - self.last_reopen_time < 3.0:
                continue

            self.last_reopen_time = now
            self.logger.warning(f"Video stream stalled for {idle:.1f}s; reopening")
            try:
                self.open_video_normal("watchdog")
            except OSError:
                self.logger.exception("Video stream reopen failed")
                return
