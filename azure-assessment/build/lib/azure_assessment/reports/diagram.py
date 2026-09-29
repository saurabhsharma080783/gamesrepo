"""Network topology diagram drawn from the architecture analysis.

One layout is computed and rendered two ways: a PNG (matplotlib) for Word and inline SVG
for HTML. Hubs sit in a row with their spokes beneath them; on-premises sits above the hub
that terminates the gateway; VNets that are not attached to a hub go in a separate row.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

from ..analysis.architecture import Topology, VNetNode

COLS_PER_HUB = 3
BOX_W, BOX_H, HUB_H = 0.88, 0.62, 0.74


@dataclass
class Box:
    key: str
    x: float          # centre
    y: float          # top
    h: float
    kind: str         # hub / spoke / other / isolated / onprem
    lines: list[str]  # first line is the title
    note: str = ""    # small status line
    note_kind: str = ""  # ok / warn


@dataclass
class Edge:
    a: str
    b: str
    kind: str         # hub-spoke / hub-hub / spoke-spoke / other / onprem
    label: str = ""


@dataclass
class Layout:
    boxes: dict[str, Box]
    edges: list[Edge]
    width: float
    height: float
    other_row_y: float | None


def _box_lines(n: VNetNode) -> list[str]:
    lines = [n.name, n.subscription, f"{n.location} · {', '.join(n.address_space) or 'no address space'}"]
    if n.role == "hub" and n.platform_services:
        short = {"Azure Firewall": "Firewall", "Azure Bastion": "Bastion", "VPN/ExpressRoute gateway": "Gateway",
                 "ExpressRoute gateway": "ER gateway"}
        lines.append(" · ".join(short.get(s, s) for s in n.platform_services))
    return lines


def layout(t: Topology) -> Layout | None:
    if not t.nodes:
        return None
    boxes: dict[str, Box] = {}
    edges: list[Edge] = []
    hubs = sorted(t.hubs, key=lambda h: (t.nodes[h].location, t.nodes[h].name))
    top = 1.3 if t.onprem else 0.2
    cursor = 0.0
    max_row = 0
    for h in hubs:
        spokes = sorted((s for s in t.spokes if t.nodes[s].hub == h), key=lambda s: t.nodes[s].name)
        block = max(1, min(COLS_PER_HUB, len(spokes)))
        n = t.nodes[h]
        boxes[h] = Box(h, cursor + block / 2, top, HUB_H, "hub", _box_lines(n))
        for k, s in enumerate(spokes):
            row, col = divmod(k, COLS_PER_HUB)
            in_row = min(COLS_PER_HUB, len(spokes) - row * COLS_PER_HUB)
            x = cursor + (block - in_row) / 2 + col + 0.5
            y = top + 1.25 + row * 1.0
            sn = t.nodes[s]
            boxes[s] = Box(s, x, y, BOX_H, "spoke", _box_lines(sn),
                           "egress via hub firewall" if sn.default_route_to_nva else "egress not via firewall",
                           "ok" if sn.default_route_to_nva else "warn")
            max_row = max(max_row, row + 1)
        cursor += block + 0.3
    width = max(cursor - 0.3, 1.0)

    others = sorted((v for v in t.nodes if v not in boxes), key=lambda v: t.nodes[v].name)
    other_y = None
    if others:
        other_y = top + (1.25 + max_row * 1.0 + 0.35 if hubs else 0)
        per_row = max(3, int(width)) if hubs else max(3, min(4, len(others)))
        width = max(width, min(len(others), per_row))
        for k, v in enumerate(others):
            row, col = divmod(k, per_row)
            n = t.nodes[v]
            iso = n.role == "isolated"
            boxes[v] = Box(v, col + 0.5, other_y + 0.35 + row * 1.0, BOX_H, "isolated" if iso else "other",
                           _box_lines(n), "not peered to any network" if iso else "", "warn" if iso else "")
        height = other_y + 0.35 + ((len(others) - 1) // per_row + 1) * 1.0
    else:
        height = top + (1.25 + max_row * 1.0 if max_row else HUB_H + 0.2)

    if t.onprem:
        gw_hubs = sorted({o["hub"] for o in t.onprem if o["hub"] in boxes}, key=lambda h: boxes[h].x)
        if gw_hubs:
            kinds = sorted({o["type"] for o in t.onprem})
            x = sum(boxes[h].x for h in gw_hubs) / len(gw_hubs)
            boxes["onprem"] = Box("onprem", x, 0.15, 0.55, "onprem", ["On-premises", " + ".join(kinds)])
            for h in gw_hubs:
                edges.append(Edge("onprem", h, "onprem",
                                  " / ".join(sorted({o["type"] for o in t.onprem if o["hub"] == h}))))
    for a, b, kind in t.edges:
        if a in boxes and b in boxes:
            if kind == "hub-spoke" and t.nodes[a].role != "hub":
                a, b = b, a
            edges.append(Edge(a, b, kind))
    return Layout(boxes, edges, width, height, other_y)


# ---- styling shared by both renderers ------------------------------------------

LIGHT = {
    "hub_fill": "#E3ECF7", "hub_stroke": "#0F3B68", "spoke_fill": "#FFFFFF", "spoke_stroke": "#2A78D6",
    "other_fill": "#FFFFFF", "other_stroke": "#898781", "isolated_fill": "#FDF1F1", "isolated_stroke": "#D03B3B",
    "onprem_fill": "#F1F0EC", "onprem_stroke": "#52514E", "ink": "#0B0B0B", "ink2": "#52514E",
    "edge": "#6B7A8C", "hubhub": "#0F3B68", "bad": "#D03B3B", "ok": "#0A7F0A", "warn": "#B45309",
}


def _edge_points(L: Layout, e: Edge):
    a, b = L.boxes[e.a], L.boxes[e.b]
    if e.kind == "hub-hub":
        left, right = (a, b) if a.x < b.x else (b, a)
        y = left.y + left.h / 2
        return (left.x + BOX_W / 2, y), (right.x - BOX_W / 2, y), None
    if e.kind in ("spoke-spoke", "other"):
        p1 = (a.x, a.y + a.h)
        p2 = (b.x, b.y + b.h)
        ctrl = ((a.x + b.x) / 2, max(a.y + a.h, b.y + b.h) + 0.45)
        if abs(a.y - b.y) > 0.01:  # different rows: side to side
            p1, p2, ctrl = (a.x, a.y + a.h), (b.x, b.y), None
        return p1, p2, ctrl
    upper, lower = (a, b) if a.y < b.y else (b, a)
    return (upper.x, upper.y + upper.h), (lower.x, lower.y), None


LEGEND_STEP = 1.0


def _legend_pos(k: int, L: Layout) -> tuple[float, float]:
    per_row = max(2, min(4, int(max(L.width, 2.2) // LEGEND_STEP)))
    row, col = divmod(k, per_row)
    return col * LEGEND_STEP, L.height + 0.3 + row * 0.22


def _legend_rows(L: Layout) -> int:
    return 1 if max(2, min(4, int(max(L.width, 2.2) // LEGEND_STEP))) >= 4 else 2


def LEGEND_PNG(c):  # noqa: N802
    return (("Hub-spoke peering", c["edge"], "-"), ("Hub-to-hub peering", c["hubhub"], (0, (5, 3))),
            ("Spoke-to-spoke peering", c["bad"], (0, (4, 3))), ("On-premises link", c["ink2"], (0, (1, 2))))


# ---- PNG (Word) ----------------------------------------------------------------

def render_png(t: Topology, path: Path) -> Path | None:
    L = layout(t)
    if L is None:
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, PathPatch
    from matplotlib.path import Path as MPath

    c = LIGHT
    scale_x, scale_y = 3.0, 1.5
    extra = 0.22 * (_legend_rows(L) - 1)
    fig_w, fig_h = max(L.width, 2.2) * scale_x + 0.4, (L.height + 0.55 + extra) * scale_y
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(-0.1, max(L.width, 2.2) + 0.1)
    ax.set_ylim(L.height + 0.45 + extra, -0.1)
    ax.axis("off")

    for e in L.edges:
        p1, p2, ctrl = _edge_points(L, e)
        color = {"hub-hub": c["hubhub"], "spoke-spoke": c["bad"], "onprem": c["ink2"]}.get(e.kind, c["edge"])
        style = {"hub-hub": (0, (5, 3)), "spoke-spoke": (0, (4, 3)), "onprem": (0, (1, 2))}.get(e.kind, "-")
        if ctrl:
            curve = MPath([p1, ctrl, p2], [MPath.MOVETO, MPath.CURVE3, MPath.CURVE3])
            ax.add_patch(PathPatch(curve, fill=False, edgecolor=color, lw=1.6, linestyle=style, zorder=1))
            ax.text(ctrl[0], (p1[1] + p2[1]) / 4 + ctrl[1] / 2 + 0.1, "direct spoke-to-spoke peering", ha="center", va="center",
                    fontsize=9, color=color, zorder=3,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))
        else:
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=color, lw=1.6, linestyle=style, zorder=1)
            if e.kind == "hub-hub":
                ax.text((p1[0] + p2[0]) / 2, p1[1] - 0.08, "global peering", ha="center", va="bottom",
                        fontsize=9, color=color)
            if e.kind == "onprem" and e.label:
                ax.text(p1[0] + 0.04, (p1[1] + p2[1]) / 2, e.label, ha="left", va="center", fontsize=9,
                        color=color)

    for b in L.boxes.values():
        fill, stroke = c[f"{b.kind}_fill"], c[f"{b.kind}_stroke"]
        ax.add_patch(FancyBboxPatch((b.x - BOX_W / 2, b.y), BOX_W, b.h, boxstyle="round,pad=0,rounding_size=0.05",
                                    fc=fill, ec=stroke, lw=1.4 if b.kind == "hub" else 1.0,
                                    linestyle="--" if b.kind == "isolated" else "-", zorder=2))
        y = b.y + 0.12
        for i, line in enumerate(b.lines):
            ax.text(b.x, y, line, ha="center", va="center", zorder=3,
                    fontsize=11 if i == 0 else 9, fontweight="bold" if i == 0 else "normal",
                    color=c["ink"] if i == 0 else (c["hub_stroke"] if (b.kind == "hub" and i == 3) else c["ink2"]))
            y += 0.14
        if b.note:
            ax.text(b.x, b.y + b.h - 0.08, b.note, ha="center", va="center", fontsize=8.8, zorder=3,
                    color=c["ok"] if b.note_kind == "ok" else c["warn"])
    if L.other_row_y is not None and t.hubs:
        ax.text(0, L.other_row_y + 0.12, "Not attached to a hub", ha="left", va="center", fontsize=10,
                color=c["ink2"], fontweight="bold")
    # legend
    for k, (label, color, style) in enumerate(LEGEND_PNG(c)):
        lx, ly = _legend_pos(k, L)
        ax.plot([lx, lx + 0.18], [ly, ly], color=color, lw=1.6, linestyle=style, clip_on=False)
        ax.text(lx + 0.22, ly, label, fontsize=9, va="center", color=c["ink2"])
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


# ---- SVG (HTML) ----------------------------------------------------------------

def render_svg(t: Topology) -> str:
    L = layout(t)
    if L is None:
        return ""
    SX, SY = 230, 130
    W = max(L.width, 2.2) * SX + 20
    H = (L.height + 0.55 + 0.22 * (_legend_rows(L) - 1)) * SY
    X = lambda v: round(v * SX + 10, 1)  # noqa: E731
    Y = lambda v: round(v * SY + 10, 1)  # noqa: E731
    out = [f'<svg class="topology" viewBox="0 0 {round(W)} {round(H)}" role="img" '
           f'aria-label="Network topology: {escape(t.pattern)}" xmlns="http://www.w3.org/2000/svg">']
    for e in L.edges:
        p1, p2, ctrl = _edge_points(L, e)
        cls = f"edge edge-{e.kind}"
        if ctrl:
            out.append(f'<path class="{cls}" d="M{X(p1[0])},{Y(p1[1])} Q{X(ctrl[0])},{Y(ctrl[1])} '
                       f'{X(p2[0])},{Y(p2[1])}" fill="none"/>')
            out.append(f'<text class="edge-label bad" x="{X(ctrl[0])}" y="{Y((p1[1] + p2[1]) / 4 + ctrl[1] / 2 + 0.14)}" '
                       f'text-anchor="middle">direct spoke-to-spoke peering</text>')
        else:
            out.append(f'<line class="{cls}" x1="{X(p1[0])}" y1="{Y(p1[1])}" x2="{X(p2[0])}" y2="{Y(p2[1])}"/>')
            if e.kind == "hub-hub":
                out.append(f'<text class="edge-label" x="{X((p1[0] + p2[0]) / 2)}" y="{Y(p1[1]) - 6}" '
                           f'text-anchor="middle">global peering</text>')
            if e.kind == "onprem" and e.label:
                out.append(f'<text class="edge-label" x="{X(p1[0]) + 6}" y="{Y((p1[1] + p2[1]) / 2) + 4}">'
                           f'{escape(e.label)}</text>')
    for b in L.boxes.values():
        x0 = X(b.x - BOX_W / 2)
        out.append(f'<g class="node node-{b.kind}"><title>{escape(" | ".join(b.lines))}</title>'
                   f'<rect x="{x0}" y="{Y(b.y)}" width="{round(BOX_W * SX, 1)}" height="{round(b.h * SY, 1)}" rx="7"/>')
        y = b.y + 0.14
        for i, line in enumerate(b.lines):
            cls = "t-title" if i == 0 else ("t-svc" if (b.kind == "hub" and i == 3) else "t-sub")
            out.append(f'<text class="{cls}" x="{X(b.x)}" y="{Y(y)}" text-anchor="middle">{escape(line)}</text>')
            y += 0.135
        if b.note:
            out.append(f'<text class="t-note {b.note_kind}" x="{X(b.x)}" y="{Y(b.y + b.h - 0.07)}" '
                       f'text-anchor="middle">{escape(b.note)}</text>')
        out.append("</g>")
    if L.other_row_y is not None and t.hubs:
        out.append(f'<text class="t-group" x="{X(0)}" y="{Y(L.other_row_y + 0.14)}">Not attached to a hub</text>')
    for k, (label, kind) in enumerate((("Hub-spoke peering", "hub-spoke"), ("Hub-to-hub peering", "hub-hub"),
                                       ("Spoke-to-spoke peering", "spoke-spoke"), ("On-premises link", "onprem"))):
        lx, ly = _legend_pos(k, L)
        out.append(f'<line class="edge edge-{kind}" x1="{X(lx)}" y1="{Y(ly)}" x2="{X(lx + 0.16)}" y2="{Y(ly)}"/>'
                   f'<text class="edge-label" x="{X(lx + 0.2)}" y="{Y(ly) + 4}">{label}</text>')
    out.append("</svg>")
    return "\n".join(out)
