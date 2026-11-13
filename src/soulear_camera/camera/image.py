"""Image behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import os
import time

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QRectF,
    Qt,
)
from PySide6.QtGui import QImage, QPainter, QPainterPath


class _ImageMixin:
    def _transform_jpeg_for_export(self: SoulearCamera, jpeg: bytes, camera_angle: float | None) -> bytes:
        """Return the preview-style rotated/circular JPEG for file export.

        The angle belongs to the same completed camera frame as *jpeg*.
        JPEG has no alpha channel, therefore pixels outside the circular
        viewport are rendered black.

        Parameters
        ----------
        jpeg : bytes
            Encoded source frame.
        camera_angle : float | None
            Counter-rotation angle in degrees, or ``None`` to leave the frame unchanged.

        Returns
        -------
        bytes
            Original or transformed JPEG data.
        """
        if camera_angle is None:
            return jpeg

        image = QImage.fromData(jpeg, format="JPEG")  # ty: ignore[invalid-argument-type]
        if image.isNull():
            self.logger.error("EXPORT_ROTATE: JPEG decode failed")
            return jpeg

        side = min(image.width(), image.height())
        output = QImage(side, side, QImage.Format.Format_RGB32)
        output.fill(Qt.GlobalColor.black)

        painter = QPainter(output)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        clip = QPainterPath()
        clip.addEllipse(QRectF(0.0, 0.0, float(side), float(side)))
        painter.setClipPath(clip)
        painter.translate(side / 2.0, side / 2.0)
        painter.rotate(-float(camera_angle))
        painter.drawImage(
            int(-image.width() / 2),
            int(-image.height() / 2),
            image,
        )
        painter.end()

        encoded = QByteArray()
        buffer = QBuffer(encoded)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            self.logger.error("EXPORT_ROTATE: could not open QBuffer")
            return jpeg
        ok = output.save(buffer, b"JPEG", 95)
        buffer.close()
        if not ok:
            self.logger.error("EXPORT_ROTATE: JPEG encoding failed")
            return jpeg
        return bytes(encoded.data())

    def save_current_frame(self: SoulearCamera) -> None:
        """Save the most recently received JPEG, applying auto-rotation if enabled.

        Raises
        ------
        OSError
            If the output image cannot be created or written.
        """
        if self.last_jpeg is None:
            self.signals.status.emit("Noch kein Bild vorhanden")
            return

        jpeg = self.last_jpeg
        angle = self.last_jpeg_angle
        if self.auto_rotate:
            jpeg = self._transform_jpeg_for_export(jpeg, angle)

        filename = time.strftime("soulear_%Y-%m-%d_%H-%M-%S.jpg")
        with open(filename, "wb") as handle:
            handle.write(jpeg)
        suffix = " (auto-rotiert)" if self.auto_rotate else ""
        self.signals.status.emit(f"Gespeichert: {os.path.abspath(filename)}{suffix}")
