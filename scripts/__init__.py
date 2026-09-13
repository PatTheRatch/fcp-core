# Makes `scripts` importable as a package, so one analysis script can reuse
# another's helpers (`from scripts.board_calibration import ...`) and mypy
# sees each file under exactly one module name.
