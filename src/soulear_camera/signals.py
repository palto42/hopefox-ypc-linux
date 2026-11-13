"""Qt signals shared by the camera pipeline and UI."""

from PySide6.QtCore import (
    QObject,
    Signal,
)


class Signals(QObject):
    """Qt signals used to publish camera frames and UI state across threads."""

    frame = Signal(bytes)
    status = Signal(str)
    stats = Signal(str)
    recording_state = Signal(bool)
