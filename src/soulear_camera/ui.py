"""Qt window and control widgets for the camera client."""

import argparse
import threading

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QImage, QPainter, QPainterPath, QPixmap, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from .camera import Signals, SoulearCamera


class MainWindow(QMainWindow):
    """Present the live camera preview and its recording/control actions."""

    def __init__(self, args: argparse.Namespace) -> None:
        """Build the camera window from parsed command-line options.

        Parameters
        ----------
        args : argparse.Namespace
            Parsed camera, network, and runtime options.
        """
        super().__init__()
        self.setWindowTitle("Soulear Linux Camera")
        self.resize(800, 700)
        self.current_pixmap = None

        self.image_label = QLabel("Warte auf Soulear …")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(640, 480)

        self.status_label = QLabel("Initialisiere …")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.stats_label = QLabel("Frames: 0")
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.save_button = QPushButton("Bild speichern")
        self.save_button.setEnabled(False)

        self.record_start_button = QPushButton("Videoaufnahme starten")
        self.record_start_button.setEnabled(True)
        self.record_stop_button = QPushButton("Videoaufnahme stoppen")
        self.record_stop_button.setEnabled(False)

        self.led_on_button = QPushButton("LED an")
        self.led_off_button = QPushButton("LED aus")

        self.auto_rotate_button = QPushButton("Auto-Rotation: AUS")
        self.auto_rotate_button.setCheckable(True)
        self.auto_rotate_button.setChecked(False)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.record_start_button)
        button_layout.addWidget(self.record_stop_button)
        button_layout.addWidget(self.led_on_button)
        button_layout.addWidget(self.led_off_button)
        button_layout.addWidget(self.auto_rotate_button)
        button_layout.addStretch()

        layout = QVBoxLayout()
        layout.addWidget(self.image_label, stretch=1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.stats_label)
        layout.addLayout(button_layout)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.signals = Signals()
        self.signals.status.connect(self.status_label.setText)
        self.signals.stats.connect(self.stats_label.setText)
        self.signals.recording_state.connect(self.set_recording_ui_state)

        self.camera = SoulearCamera(
            interface=args.interface,
            use_dumpcap=args.dumpcap,
            local_ip=args.local_ip,
            camera_ip=args.camera_ip,
            broadcast_ip=args.broadcast_ip,
            signals=self.signals,
            debug=args.loglevel == "DEBUG",
            keepalives=not args.no_keepalive,
            online_monitor=not args.no_online_monitor,
            fallback_force=not args.no_force_fallback,
            auto_reopen=not args.no_auto_reopen,
        )
        self.save_button.clicked.connect(self.camera.save_current_frame)
        self.record_start_button.clicked.connect(self.start_recording)
        self.record_stop_button.clicked.connect(self.stop_recording)
        self.led_on_button.clicked.connect(lambda: self.set_led(True))
        self.led_off_button.clicked.connect(lambda: self.set_led(False))
        self.auto_rotate_button.toggled.connect(self.set_auto_rotate)

        #  play the live JPEG FIFO at the same 20 fps cadence used by
        # the successful local recording.  This intentionally smooths camera
        # packet/frame jitter instead of displaying each arrival immediately.
        self.preview_timer = QTimer(self)
        self.preview_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.preview_timer.setInterval(50)  # 20 Hz
        self.preview_timer.timeout.connect(self.display_recording_style_frame)
        self.preview_timer.start()

        threading.Thread(target=self.camera.start, daemon=True).start()

    def start_recording(self) -> None:
        """Start recording on a worker thread and update the window state."""
        self.record_start_button.setEnabled(False)
        self.record_stop_button.setEnabled(True)
        self.auto_rotate_button.setEnabled(False)
        self.status_label.setText("Starte Kamera-Recording …")

        def worker() -> None:
            """Run the blocking recording startup and report its result."""
            ok = self.camera.start_video_recording()
            if ok:
                self.signals.recording_state.emit(True)
                mode = "auto-rotiert" if self.camera.record_auto_rotate else "original"
                self.signals.status.emit(f"Videoaufnahme gestartet - {mode}; Auto-Rotation bis zum Stop gesperrt")
            else:
                self.signals.status.emit("Lokale Videoaufnahme konnte nicht gestartet werden")
                self.signals.recording_state.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    def stop_recording(self) -> None:
        """Stop recording asynchronously and restore recording controls."""
        self.record_stop_button.setEnabled(False)
        self.status_label.setText("Stoppe Kamera-Recording …")

        def worker() -> None:
            """Stop the camera recording and publish completion status."""
            self.camera.stop_video_recording()
            self.signals.status.emit("Recording beendet")
            self.signals.recording_state.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    def set_recording_ui_state(self, active: bool) -> None:
        """Enable or disable recording controls for the current state.

        Parameters
        ----------
        active : bool
            Whether a recording is active.
        """
        active = bool(active)
        self.record_start_button.setEnabled(not active)
        self.record_stop_button.setEnabled(active)
        self.auto_rotate_button.setEnabled(not active)

    def set_led(self, enabled: bool) -> None:
        """Set the camera LED asynchronously and publish the command result.

        Parameters
        ----------
        enabled : bool
            True to turn the LED on, false to turn it off.
        """
        self.led_on_button.setEnabled(False)
        self.led_off_button.setEnabled(False)

        def worker() -> None:
            """Send the LED command and restore the button state."""
            ok = self.camera.set_led(enabled)
            if ok:
                self.signals.status.emit("LED eingeschaltet" if enabled else "LED ausgeschaltet")
            else:
                self.signals.status.emit("LED-Befehl fehlgeschlagen")
            self.led_on_button.setEnabled(True)
            self.led_off_button.setEnabled(True)

        threading.Thread(target=worker, daemon=True).start()

    def set_auto_rotate(self, enabled: bool) -> None:
        """Enable or disable orientation correction for preview and exports.

        Parameters
        ----------
        enabled : bool
            Whether to counter-rotate frames using the camera motion sensor.
        """
        if self.camera.camera_recording:
            # The recording pipeline freezes its orientation mode at start.
            # Reject programmatic/user changes while the file is open too.
            self.auto_rotate_button.blockSignals(True)
            self.auto_rotate_button.setChecked(self.camera.auto_rotate)
            self.auto_rotate_button.blockSignals(False)
            self.signals.status.emit("Auto-Rotation kann während einer Videoaufnahme nicht geändert werden")
            return

        self.camera.auto_rotate = bool(enabled)
        if enabled:
            self.camera.auto_rotate_zero = self.camera.last_camera_angle
        else:
            self.camera.auto_rotate_zero = None

        self.auto_rotate_button.setText("Auto-Rotation: AN" if enabled else "Auto-Rotation: AUS")
        angle = self.camera.last_camera_angle
        if enabled:
            self.signals.status.emit(
                "Auto-Rotation aktiviert - gepackter Soulear-Sensorwinkel"
                + (f" - Winkel {angle}°" if angle is not None else "")
            )
        else:
            self.signals.status.emit("Auto-Rotation deaktiviert")

    def display_recording_style_frame(self) -> None:
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

    def display_frame(self, jpeg: bytes, camera_angle: float | None = None) -> None:
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

    def update_scaled_pixmap(self) -> None:
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

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Rescale the preview after a Qt resize event.

        Parameters
        ----------
        event : QResizeEvent
            Qt event describing the new window size.
        """
        super().resizeEvent(event)
        self.update_scaled_pixmap()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop camera resources before accepting the window-close event.

        Parameters
        ----------
        event : QCloseEvent
            Qt close event to accept after shutdown.
        """
        self.camera.stop()
        event.accept()


# ======================================================================
# Main
# ======================================================================


