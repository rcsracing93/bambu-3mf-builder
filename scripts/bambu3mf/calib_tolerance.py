"""[calibration] type = "tolerance": not built yet (1.3.0 phase 4). See calibjobs.py for the frame."""
from .calibration import CalibrationError

KEYS = set()


def build(ctx):
    raise CalibrationError("type = \"tolerance\" is not built yet")
