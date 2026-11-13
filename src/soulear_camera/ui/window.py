"""Main camera window and user actions."""

import argparse
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from ..camera import Signals, SoulearCamera
from .preview import _PreviewMixin


class MainWindow(_PreviewMixin, QMainWindow):
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

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop camera resources before accepting the window-close event.

        Parameters
        ----------
        event : QCloseEvent
            Qt close event to accept after shutdown.
        """
        self.camera.stop()
        event.accept()
