"""Static PNG charts (matplotlib) embedded in the Word and PowerPoint reports."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ..analysis.assessor import Assessment  # noqa: E402
from .theme import INK, INK_2, MUTED, GRID, SERIES, SEVERITY_COLORS, rating_color  # noqa: E402

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": INK_2,
    "xtick.color": MUTED, "ytick.color": INK_2, "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 200, "savefig.bbox": "tight", "savefig.facecolor": "white",
})


def _hbar(items, title, path, color=SERIES, value_fmt="{:,}", xlim=None, colors=None, value_labels=None):
    items = list(items)[::-1]
    labels = [i[0] for i in items]
    values = [i[1] for i in items]
    value_labels = value_labels[::-1] if value_labels else [value_fmt.format(v) for v in values]
    fig, ax = plt.subplots(figsize=(7, max(2.2, 0.38 * len(items) + 0.8)))
    bars = ax.barh(labels, values, color=colors[::-1] if colors else color, height=0.6)
    ax.set_title(title, loc="left", color=INK, fontsize=12, fontweight="bold", pad=12)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    top = xlim or (max(values + [0]) * 1.15 or 1)
    ax.set_xlim(0, top)
    for b, lbl in zip(bars, value_labels):
        ax.text(b.get_width() + top * 0.01, b.get_y() + b.get_height() / 2, lbl,
                va="center", color=INK if lbl != "n/a" else MUTED, fontsize=9)
    fig.savefig(path)
    plt.close(fig)
    return path


def resources_by_type(a: Assessment, path: Path):
    return _hbar(a.by_type(10), "Top resource types", path)


def resources_by_region(a: Assessment, path: Path):
    return _hbar(a.by_location(10), "Resources by region", path)


def resources_by_subscription(a: Assessment, path: Path):
    return _hbar(a.by_subscription(10), "Resources by subscription", path)


def pillar_scores(a: Assessment, path: Path):
    items = [(p.pillar, p.score or 0) for p in a.pillar_scores]
    colors = [rating_color(p.score) for p in a.pillar_scores]
    labels = [p.score_label if p.score is not None else "n/a" for p in a.pillar_scores]
    return _hbar(items, "Score by pillar (0–100)", path, xlim=110, colors=colors, value_labels=labels)


def findings_by_severity(a: Assessment, path: Path):
    sev = a.severity_counts
    items = [(k, v) for k, v in sev.items()]
    colors = [SEVERITY_COLORS[k] for k, _ in items]
    return _hbar(items, "Findings by severity", path, colors=colors)


def findings_by_pillar(a: Assessment, path: Path):
    """Stacked horizontal bar: severity mix per pillar."""
    ps = a.pillar_scores[::-1]
    fig, ax = plt.subplots(figsize=(7, 3.3))
    left = [0] * len(ps)
    for sev in ("High", "Medium", "Low"):
        vals = [getattr(p, sev.lower()) for p in ps]
        ax.barh([p.pillar for p in ps], vals, left=left, color=SEVERITY_COLORS[sev], height=0.6,
                label=sev, edgecolor="white", linewidth=1.5)
        left = [l + v for l, v in zip(left, vals)]
    for i, total in enumerate(left):
        ax.text(total + max(left + [1]) * 0.01, i, str(total), va="center", fontsize=9, color=INK)
    ax.set_title("Findings by pillar and severity", loc="left", color=INK, fontsize=12, fontweight="bold", pad=12)
    ax.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.12), fontsize=9)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(left + [1]) * 1.12)
    fig.savefig(path)
    plt.close(fig)
    return path


def render_all(a: Assessment, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    fns = {
        "by_type": resources_by_type, "by_region": resources_by_region,
        "by_subscription": resources_by_subscription, "pillars": pillar_scores,
        "severity": findings_by_severity, "pillar_findings": findings_by_pillar,
    }
    return {k: fn(a, out_dir / f"{k}.png") for k, fn in fns.items()}
