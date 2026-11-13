"""Frame queuing and rendering for the camera preview."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPixmap, QResizeEvent


class _PreviewMixin:
    def display_recording_style_frame(self: MainWindow) -> None:
        """Play the live stream like the MJPEG recording: fixed 20 Hz.

        We initially buffer four frames (~200 ms).  That trades a small amount
        of latency for a much more regular cadence.  If the camera temporarily
        falls below 20 fps, the last displayed frame is repeated instead of
        making the GUI stall.  If a burst grows too large, old frames are
        discarded to keep latency bounded.
        """
        item = None

        with self.camera.gui_frame_lock:
            q = self.camera.gui_frame_queue

            if not self.camera.gui_preview_started:
                if len(q) < self.camera.gui_preview_target_buffer:
                    return
                self.camera.gui_preview_started = True
                self.camera.logger.info(f"PREVIEW20: Start mit {len(q)} gepufferten Frames")

            # A receive burst can briefly outrun the 20-Hz player. Keep only a
            # few recent frames so the preview never drifts seconds behind.
            if len(q) > self.camera.gui_preview_max_latency_frames:
                target = self.camera.gui_preview_target_buffer
                dropped = 0
                while len(q) > target:
                    q.popleft()
                    dropped += 1
                self.camera.logger.debug(f"PREVIEW20: {dropped} alte Frames verworfen (Latenzbegrenzung)")

            if q:
                item = q.popleft()
                self.camera.gui_last_frame = item
            else:
                # Same idea as a constant-rate video timeline: hold the most
                # recent frame until the next camera frame arrives.
                item = self.camera.gui_last_frame

        if item is not None:
            jpeg, camera_angle = item
            self.display_frame(jpeg, camera_angle)

    def display_frame(self: MainWindow, jpeg: bytes, camera_angle: float | None = None) -> None:
        """Decode and render one JPEG frame in the preview widget.

        Parameters
        ----------
        jpeg : bytes
            Encoded camera frame.
        camera_angle : float | None, optional
            Relative rotation angle in degrees for auto-rotated display.
        """
        image = QImage.fromData(jpeg, format="JPEG")  # ty: ignore[invalid-argument-type]
        if image.isNull():
            self.camera.logger.error("Qt could not decode JPEG frame")
            return

        pixmap = QPixmap.fromImage(image)

        if self.camera.auto_rotate and camera_angle is not None:
            #  show only the centered circular region that is guaranteed
            # to exist for every rotation.  Render into a FIXED square canvas
            # so the visible circle never changes size at 0/45/90 degrees.
            side = min(pixmap.width(), pixmap.height())
            rotated = QPixmap(side, side)
            rotated.fill(Qt.GlobalColor.transparent)

            painter = QPainter(rotated)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            clip = QPainterPath()
            clip.addEllipse(QRectF(0.0, 0.0, float(side), float(side)))
            painter.setClipPath(clip)

            painter.translate(side / 2.0, side / 2.0)
            painter.rotate(-float(camera_angle))
            painter.drawPixmap(
                int(-pixmap.width() / 2),
                int(-pixmap.height() / 2),
                pixmap,
            )
            painter.end()

            pixmap = rotated

        self.current_pixmap = pixmap
        self.update_scaled_pixmap()
        self.save_button.setEnabled(True)

    def update_scaled_pixmap(self: MainWindow) -> None:
        """Scale the current preview image to fit the image widget."""
        if self.current_pixmap is None:
            return
        self.image_label.setPixmap(
            self.current_pixmap.scaled(
                self.image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self: MainWindow, event: QResizeEvent) -> None:
        """Rescale the preview after a Qt resize event.

        Parameters
        ----------
        event : QResizeEvent
            Qt event describing the new window size.
        """
        super().resizeEvent(event)
        self.update_scaled_pixmap()
