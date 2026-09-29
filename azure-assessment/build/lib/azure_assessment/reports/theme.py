"""Shared brand colours for all report formats."""

PRIMARY = "#0F3B68"      # deep navy used for headings / title bars
ACCENT = "#2A78D6"       # Azure-like blue
SERIES = "#2A78D6"       # single-series chart colour
INK = "#0B0B0B"
INK_2 = "#52514E"
MUTED = "#898781"
GRID = "#E1E0D9"
SURFACE = "#F5F7FA"

# Status palette (severity & rating). Always paired with a text label, never colour alone.
CRITICAL = "#D03B3B"
SERIOUS = "#EC835A"
WARNING = "#FAB219"
GOOD = "#0CA30C"

SEVERITY_COLORS = {"High": CRITICAL, "Medium": SERIOUS, "Low": WARNING}


def rating_color(score: int | None) -> str:
    if score is None:
        return MUTED
    return GOOD if score >= 80 else WARNING if score >= 60 else CRITICAL


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
