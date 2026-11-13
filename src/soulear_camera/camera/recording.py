"""Recording behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import os
import shutil
import struct
import subprocess
import time
from contextlib import suppress

from .constants import CAMERA_CONTROL_PORT, CMD_RECORD_STATUS_SYNC, MAGIC


class _RecordingMixin:
    def camera_record_status_sync(self: SoulearCamera, recording: bool) -> bool:
        """Try the optional camera-side recording-status sync command.

        The tested YPC camera rejects this optional status-sync feature with
        result code 2. Local recording does not depend on it.

        Parameters
        ----------
        recording : bool
            True when announcing recording start, false for stop.

        Returns
        -------
        bool
            Whether the camera acknowledged the status command.

        """
        if not self.ensure_camera_connected():
            self.record_remote_error = "offline"
            return False

        status = 1 if recording else 0
        seq = self.next_sequence()
        packet = struct.pack(
            "<IHIHB",
            MAGIC,
            seq,
            CMD_RECORD_STATUS_SYNC,
            1,
            status,
        )

        label = "RECORD_SYNC_START" if recording else "RECORD_SYNC_STOP"
        self.logger.info(f"{label}: seq={seq} status={status} -> {self.camera_ip}:{CAMERA_CONTROL_PORT}")
        self.logger.debug(f"  TX: {packet.hex(' ')}")

        response = self.sync_transaction(
            packet,
            CAMERA_CONTROL_PORT,
            seq,
            timeout=0.25,
            retries=5,
            label=label,
        )
        if response is None or len(response) < 10:
            self.logger.warning(f"{label}: no valid response")
            self.record_remote_error = "timeout"
            return False

        magic, rseq = struct.unpack_from("<IH", response, 0)
        cmd_low = struct.unpack_from("<H", response, 6)[0]
        expected_low = CMD_RECORD_STATUS_SYNC & 0xFFFF
        firmware_result = response[9]
        echoed_status = response[12] if len(response) >= 13 else None

        if magic != MAGIC or rseq != seq or cmd_low != expected_low:
            self.logger.warning(
                f"{label}: unexpected response header magic=0x{magic:08x} seq={rseq} cmd_low=0x{cmd_low:04x}"
            )
            self.record_remote_error = "bad-header"
            return False

        if firmware_result != 0:
            self.logger.warning(
                f"{label}: Firmware lehnt optionalen RecordStatusSync ab (code={firmware_result}, echoed_status={echoed_status}). Local recording can continue."
            )
            self.record_remote_error = firmware_result
            return False

        self.logger.info(f"{label}: ACK bestätigt (echoed_status={echoed_status})")
        self.record_remote_error = None
        return True

    def _record_filename(self: SoulearCamera) -> str:
        """Create an absolute timestamped Matroska output path.

        Returns
        -------
        str
            Path for the next local recording.
        """
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return os.path.abspath(f"soulear-recording-{stamp}.mkv")

    def start_video_recording(self: SoulearCamera) -> bool:
        """Start local MJPEG recording and notify the camera when possible.

        Returns
        -------
        bool
            Whether recording state was started; true can indicate camera-only
            recording state when ffmpeg is unavailable.

        Raises
        ------
        OSError
            If camera status synchronization cannot use its UDP socket.
        """
        with self.record_lock:
            if self.camera_recording:
                self.logger.info("RECORD_START: Aufnahme läuft bereits")
                return False

            # Freeze export orientation mode for the whole recording.
            # The GUI also locks the toggle while recording.
            self.record_auto_rotate = bool(self.auto_rotate)

            # Optional firmware status sync.  Some related models support it;
            # this Soulear firmware currently answers with result code 2.
            self.record_remote_active = self.camera_record_status_sync(True)

            output = self._record_filename()
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg is None:
                self.camera_recording = True
                self.record_output_path = None
                self.record_start_resolution = self.last_jpeg_resolution
                self.record_seen_resolutions = set()
                self.record_frame_count = 0
                self.logger.warning("RECORD_START: ffmpeg not found; local video file will not be created")
                return True

            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "image2pipe",
                "-framerate",
                str(self.record_fps),
                "-vcodec",
                "mjpeg",
                "-i",
                "pipe:0",
                "-an",
            ]
            # Input is MJPEG in both modes. Auto-rotation re-encodes each
            # source frame once in _transform_jpeg_for_export(); ffmpeg only
            # muxes those JPEG frames into Matroska without another encode.
            cmd += ["-c:v", "copy", output]

            try:
                proc = subprocess.Popen(  # noqa: S603
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
            except Exception:
                self.logger.exception("RECORD_START: could not start ffmpeg")
                self.camera_record_status_sync(False)
                return False

            self.record_ffmpeg = proc
            self.record_output_path = output
            self.camera_recording = True
            self.record_start_resolution = self.last_jpeg_resolution
            self.record_seen_resolutions = set()
            self.record_frame_count = 0

            self.logger.info(
                f"RECORD_START: lokale Datei {output}; "
                f"Start-Preview={self.record_start_resolution}; "
                f"auto_rotate={'ja' if self.record_auto_rotate else 'nein'}; "
                f"remote_sync={'aktiv' if self.record_remote_active else 'nicht unterstützt'}"
            )
            return True

    def stop_video_recording(self: SoulearCamera) -> bool:
        """Stop local recording and synchronize camera recording state.

        Returns
        -------
        bool
            Whether the camera acknowledged the stop request.

        Raises
        ------
        OSError
            If camera status synchronization cannot use its UDP socket.
        """
        with self.record_lock:
            if not self.camera_recording and self.record_ffmpeg is None:
                self.logger.debug("RECORD_STOP: no recording is active")
                return False

            if self.record_remote_active:
                remote_ok = self.camera_record_status_sync(False)
            else:
                remote_ok = False
                self.logger.debug(
                    "RECORD_SYNC_STOP: übersprungen, weil Firmware den RecordStatusSync beim Start nicht unterstützt hat."
                )

            proc = self.record_ffmpeg
            output = self.record_output_path
            self.record_ffmpeg = None
            self.camera_recording = False
            remote_was_active = self.record_remote_active
            self.record_remote_active = False
            record_auto_rotate = self.record_auto_rotate
            self.record_auto_rotate = False

            if proc is not None:
                try:
                    if proc.stdin:
                        proc.stdin.close()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.terminate()
                        proc.wait(timeout=2)
                except Exception:
                    self.logger.exception("RECORD_STOP: ffmpeg shutdown failed")

                if proc.returncode not in (0, None):
                    try:
                        err = "" if proc.stderr is None else proc.stderr.read().decode("utf-8", "replace").strip()
                    except Exception:
                        err = ""
                    if err:
                        self.logger.error(f"RECORD_STOP: ffmpeg: {err}")

            self.logger.info(
                f"RECORD_STOP: frames={self.record_frame_count}, "
                f"Start={self.record_start_resolution}, "
                f"gesehen={sorted(self.record_seen_resolutions)}, "
                f"Datei={output or '-'}, "
                f"auto_rotate={'ja' if record_auto_rotate else 'nein'}, "
                f"remote_sync={'ja' if remote_was_active else 'unsupported'}, "
                f"remote_stop_ack={'ja' if remote_ok else 'nein'}"
            )
            return remote_ok

    def _record_jpeg(
        self: SoulearCamera, jpeg: bytes, width: int, height: int, camera_angle: float | None = None
    ) -> None:
        """Write a complete frame to the active ffmpeg input pipe.

        Parameters
        ----------
        jpeg : bytes
            Encoded frame data.
        width : int
            Actual JPEG width in pixels.
        height : int
            Actual JPEG height in pixels.
        camera_angle : float | None, optional
            Per-frame rotation angle used when recording with auto-rotation.
        """
        with self.record_lock:
            if not self.camera_recording:
                return

            current = (int(width), int(height))
            self.record_seen_resolutions.add(current)
            self.record_frame_count += 1

            if self.record_start_resolution and current != self.record_start_resolution:
                self.logger.info(f"RECORDING RESOLUTION CHANGE: {self.record_start_resolution} -> {width}x{height}")
                self.record_start_resolution = current

            proc = self.record_ffmpeg
            if proc is None or proc.stdin is None:
                return

            export_jpeg = self._transform_jpeg_for_export(jpeg, camera_angle) if self.record_auto_rotate else jpeg

            try:
                proc.stdin.write(export_jpeg)
            except (BrokenPipeError, OSError):
                self.logger.exception("RECORD: ffmpeg pipe failed")
                with suppress(Exception):
                    proc.stdin.close()
                self.record_ffmpeg = None
