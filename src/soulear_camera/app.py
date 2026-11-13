"""Qt application and camera protocol implementation for the Hopefox YPC client."""

import argparse
import logging
import signal
import socket
import sys
from importlib.metadata import PackageNotFoundError, version

from PySide6.QtCore import QLoggingCategory
from PySide6.QtWidgets import QApplication

from .ui import MainWindow

try:
    __version__ = version("hopefox-ypc-linux")
except PackageNotFoundError:
    __version__ = "0.0.0"

# ======================================================================
# Main
# ======================================================================


def main() -> None:
    """Parse command-line options and run the Qt camera application.

    Returns
    -------
    None

    Raises
    ------
    SystemExit
        When argparse handles help/version output or Qt exits the application.

    """
    parser = argparse.ArgumentParser(description="Linux client for Soulear/YPC Wi-Fi inspection cameras")
    parser.add_argument(
        "--dumpcap",
        action="store_true",
        help="Use optional dumpcap packet capture instead of normal UDP sockets",
    )
    parser.add_argument(
        "--interface",
        "-i",
        default=None,
        help="Interface for --dumpcap. If omitted, it is detected via the route to --camera-ip",
    )
    parser.add_argument(
        "--local-ip",
        default=None,
        help="Local IPv4 address used for UDP sockets (default: detected via the route to --camera-ip)",
    )
    parser.add_argument(
        "--camera-ip",
        "--camera",
        dest="camera_ip",
        default="192.168.1.1",
        help="Camera IPv4 address (default: 192.168.1.1)",
    )
    parser.add_argument(
        "--broadcast-ip",
        "--broadcast",
        dest="broadcast_ip",
        default="192.168.1.255",
        help="Broadcast IPv4 address used by presence keepalives (default: 192.168.1.255)",
    )
    parser.add_argument(
        "-l",
        "--loglevel",
        type=str.upper,
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        metavar="LEVEL",
        help="Set logging level (default: INFO)",
    )
    parser.add_argument("--no-keepalive", action="store_true", help="Disable legacy presence keepalives")
    parser.add_argument("--no-online-monitor", action="store_true", help="Disable periodic CHECK_ONLINE")
    parser.add_argument(
        "--no-force-fallback",
        action="store_true",
        help="Disable OPEN_VIDEO_FORCE fallback",
    )
    parser.add_argument("--no-auto-reopen", action="store_true", help="Disable stream watchdog reopen")
    parser.add_argument("-V", "--version", action="version", version=f"hopefox-ypc-linux {__version__}")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.loglevel),
        format="%(levelname)s: %(message)s",
    )
    if args.loglevel != "DEBUG":
        QLoggingCategory.setFilterRules("qt.qpa.wayland.textinput.warning=false")

    if args.local_ip is None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((args.camera_ip, 9))
                args.local_ip = sock.getsockname()[0]
        except OSError as exc:
            parser.error(f"Could not detect local IP address: {exc}")
        logging.info("Detected local IP address: %s", args.local_ip)

    app = QApplication(sys.argv)
    window = MainWindow(args)
    window.show()
    signal.signal(signal.SIGINT, lambda _signum, _frame: window.close())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
