# Agent and developer instructions

## Project domain

This is a Linux Python client for a Hopefox-branded Soulear/YPC-compatible
inspection camera. The protocol is based on experiments with one confirmed
Hopefox device; do not assume that every similarly branded camera behaves the
same way.

- Normal operation receives video over UDP sockets. `dumpcap` is an optional
  diagnostics backend, not a runtime requirement.
- The tested camera advertises 640×480 in its transport header but sends
  480×480 JPEGs. Use the JPEG dimensions for image output and keep this
  distinction in mind when changing the receive or recording paths.
- `docs/protocol.md`, `docs/findings.md`, and `docs/orientation.md` distinguish
  observed behavior from assumptions. Preserve that distinction when updating
  protocol code or documentation.
- Running the application or sending control commands can interact with a
  physical camera. Do not do so as a test unless the task calls for hardware
  validation.

## Setup and dependency management

- Runtime support is Python 3.12+; the project uses `uv` and `pyproject.toml`.
- Set up the environment with `uv sync`. Use `uv run` for project tools and
  commands; do not install project dependencies with `pip`.
- Add runtime and development dependencies with `uv add <package>` and
  `uv add --dev <package>`, respectively. Keep dependencies minimal and let
  `uv` maintain `uv.lock`; do not edit the lockfile by hand.
- The app entry point is `uv run soulear-camera`. Use `uv run soulear-camera
  --help` to inspect CLI options without starting a camera session.
- Do not stage, commit, or push changes unless asked.

## Coding conventions

- Preserve existing behavior and public interfaces unless the task explicitly
  calls for a change. Keep changes focused and update directly related docs.
- Add parameter and return annotations to functions and methods, including
  internal helpers. Prefer specific types, guards, and narrowed values over
  `Any`, unchecked casts, or ambiguous containers.
- Follow the NumPy docstring convention configured in `pyproject.toml`. Add
  docstrings to public modules, classes, and functions, and explain non-obvious
  protocol assumptions or behavior. Document parameters, returns, and raised
  exceptions when useful to callers.
- Let Ruff format the code and use f-strings for interpolated strings. The
  configured line length is 120; treat it as a style setting, not a reason to
  make code less readable.
- Aim for cohesive modules around 200 lines and generally below 300 lines.
  Treat these as soft targets: split by responsibility when it improves
  clarity, not just to satisfy a line count.
- Follow nearby naming, logging, localization, and error-handling patterns.
  Report errors explicitly; avoid broad catches and silent fallbacks.
- Keep protocol findings evidence-based. Identify the tested camera and label
  uncertain behavior rather than presenting it as confirmed.

## Project structure and boundaries

- `src/soulear_camera/app.py` parses CLI options and starts the Qt application.
- `src/soulear_camera/camera/` contains the camera facade and its protocol,
  socket, capture, packet, video, and recording modules.
- `src/soulear_camera/ui/` contains the main window and preview.
- `src/soulear_camera/signals.py` defines signals shared by the camera and UI.
- `docs/` contains protocol findings and architecture documentation.
- Keep `soulear_camera.camera` exporting `SoulearCamera` and `Signals`, and
  `soulear_camera.ui` exporting `MainWindow`. Camera behavior modules share
  state through the `SoulearCamera` facade; update its composition and callers
  together when moving or adding behavior.
- There is no established automated test suite. For behavior changes, add a
  focused regression test when practical; prefer the standard-library
  `unittest` unless the task justifies a new test dependency. Put tests in
  `tests/` and run them with `uv run python -m unittest discover -s tests`.
- Treat `.venv/`, `dist/`, `build/`, `*.egg-info/`, Python caches, and ignored
  camera outputs as generated or local data. Do not edit or commit them by
  hand. Packet captures can contain unrelated local traffic; sanitize them
  before sharing.

## Quality gates

Run checks relevant to the change:

```bash
uv run ruff format --check src/soulear_camera
uv run ruff check --no-fix src/soulear_camera
uv run ty check
```

Run tests when they exist or when adding them. Run `uv build` when changing
package structure, imports, or packaging configuration. Documentation-only
changes do not require code checks. Report any relevant checks that could not
be run and any hardware-dependent validation that was not performed.
