from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from proctrace.timeline import Timeline

_BYTES_PER_MB: Final[int] = 1024 * 1024


def _resolve_timeline(source: Timeline | dict[str, Any] | str | Path) -> Timeline:
    """Normalize a Timeline object, dictionary, JSON string, or file path into a Timeline."""
    from proctrace.timeline import Timeline

    if isinstance(source, Timeline):
        return source
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_file():
            return Timeline.from_json(path.read_text(encoding="utf-8"))
        if isinstance(source, str) and (source.startswith("{") or source.endswith("}")):
            return Timeline.from_json(source)
        raise FileNotFoundError(f"Timeline file not found: {source}")
    if isinstance(source, dict):
        if "timeline" in source and isinstance(source["timeline"], dict):
            return Timeline.from_dict(source["timeline"])
        return Timeline.from_dict(source)
    raise TypeError(f"Cannot convert {type(source).__name__} to Timeline")


def render_svg(
    timeline: Timeline | dict[str, Any] | str | Path,
    title: str = "proctrace Resource Timeline",
    width: int = 900,
    height: int = 450,
) -> str:
    """Generate a standalone SVG chart representing the resource timeline.

    Contains:
    - Left Y-axis: RSS memory curves and labels in MB.
    - Right Y-axis: Dual-axis curves and labels for VMS memory (MB) and open FDs.
    - Bottom X-axis: Elapsed time labels in milliseconds.
    - Visual peak marker and dashed guideline at `peak_at_ms`.
    - Embedded full JSON data inside `<script type="application/json">` for machine readability.
    """
    tl = _resolve_timeline(timeline)

    # Margins and plot bounding box
    margin_left = 75
    margin_right = 75
    margin_top = 65
    margin_bottom = 55

    plot_x = margin_left
    plot_y = margin_top
    plot_w = max(100, width - margin_left - margin_right)
    plot_h = max(100, height - margin_top - margin_bottom)

    n_samples = len(tl)
    json_payload = tl.to_json()

    if n_samples == 0:
        # Render empty state
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="100%" height="100%" style="font-family: system-ui, -apple-system, sans-serif;">\n'
            f'  <rect width="{width}" height="{height}" fill="#ffffff" rx="8" />\n'
            f'  <text x="{width / 2:.1f}" y="32" text-anchor="middle" font-size="16" font-weight="bold" fill="#0f172a">{title}</text>\n'
            f'  <text x="{width / 2:.1f}" y="{height / 2:.1f}" text-anchor="middle" font-size="14" fill="#94a3b8">No timeline samples recorded</text>\n'
            f'  <script type="application/json" id="proctrace-timeline-data">{json_payload}</script>\n'
            f'</svg>'
        )

    t_min = min(tl.t_ms)
    t_max = max(tl.t_ms)
    t_span = max(1.0, t_max - t_min)

    def x_pos(t: float) -> float:
        return plot_x + ((t - t_min) / t_span) * plot_w

    # Left Axis: RSS (MB)
    max_rss_raw = max(tl.rss) if tl.rss else 0
    rss_scale_max = max(1024 * 1024, int(max_rss_raw * 1.15))
    rss_scale_min = 0.0

    def y_rss(rss_b: float) -> float:
        denom = max(1.0, rss_scale_max - rss_scale_min)
        norm = (rss_b - rss_scale_min) / denom
        return plot_y + plot_h - norm * plot_h

    # Right Axis: VMS (MB) and FDs
    max_vms_raw = max(tl.vms) if tl.vms else 0
    vms_scale_max = max(1024 * 1024, int(max_vms_raw * 1.15))
    vms_scale_min = 0.0

    def y_vms(vms_b: float) -> float:
        denom = max(1.0, vms_scale_max - vms_scale_min)
        norm = (vms_b - vms_scale_min) / denom
        return plot_y + plot_h - norm * plot_h

    max_fds_raw = max(tl.fds) if tl.fds else 0
    fds_scale_max = max(10, int(max_fds_raw * 1.3))

    def y_fds(fds_count: float) -> float:
        norm = fds_count / max(1.0, float(fds_scale_max))
        return plot_y + plot_h - norm * plot_h

    # Build polylines
    pts_rss = " ".join(f"{x_pos(t):.1f},{y_rss(r):.1f}" for t, r in zip(tl.t_ms, tl.rss))
    pts_vms = " ".join(f"{x_pos(t):.1f},{y_vms(v):.1f}" for t, v in zip(tl.t_ms, tl.vms))
    pts_fds = " ".join(f"{x_pos(t):.1f},{y_fds(f):.1f}" for t, f in zip(tl.t_ms, tl.fds))

    # Grid lines and Y tick labels
    grid_svg: list[str] = []
    num_y_ticks = 5
    for i in range(num_y_ticks):
        ratio = i / (num_y_ticks - 1)
        y = plot_y + plot_h - ratio * plot_h
        # Horizontal grid line
        grid_svg.append(
            f'  <line x1="{plot_x}" y1="{y:.1f}" x2="{plot_x + plot_w}" y2="{y:.1f}" stroke="#e2e8f0" stroke-width="1" stroke-dasharray="2,3" />'
        )

        # Left tick label: RSS (MB)
        rss_tick_mb = (rss_scale_min + ratio * (rss_scale_max - rss_scale_min)) / _BYTES_PER_MB
        grid_svg.append(
            f'  <text x="{plot_x - 10}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#475569">{rss_tick_mb:.1f}</text>'
        )

        # Right tick label: VMS (MB)
        vms_tick_mb = (vms_scale_min + ratio * (vms_scale_max - vms_scale_min)) / _BYTES_PER_MB
        grid_svg.append(
            f'  <text x="{plot_x + plot_w + 10}" y="{y + 4:.1f}" text-anchor="start" font-size="11" fill="#7c3aed">{vms_tick_mb:.1f}</text>'
        )

    # X-axis ticks
    num_x_ticks = min(6, max(2, n_samples))
    for i in range(num_x_ticks):
        ratio = i / (num_x_ticks - 1)
        x = plot_x + ratio * plot_w
        t_tick = t_min + ratio * t_span
        grid_svg.append(
            f'  <line x1="{x:.1f}" y1="{plot_y + plot_h}" x2="{x:.1f}" y2="{plot_y + plot_h + 5}" stroke="#94a3b8" stroke-width="1" />'
        )
        grid_svg.append(
            f'  <text x="{x:.1f}" y="{plot_y + plot_h + 18}" text-anchor="middle" font-size="11" fill="#475569">{t_tick:.1f}</text>'
        )

    # Peak marker elements
    peak_svg: list[str] = []
    peak_at_ms = tl.peak_at_ms
    if peak_at_ms is not None and tl.rss:
        peak_idx = max(range(len(tl.rss)), key=lambda i: tl.rss[i])
        peak_rss_b = tl.rss[peak_idx]
        px = x_pos(peak_at_ms)
        py = y_rss(peak_rss_b)
        peak_mb = peak_rss_b / _BYTES_PER_MB

        peak_svg.append(
            f'  <line class="peak-line" x1="{px:.1f}" y1="{plot_y}" x2="{px:.1f}" y2="{plot_y + plot_h}" stroke="#ef4444" stroke-width="1.5" stroke-dasharray="4,4" />'
        )
        peak_svg.append(
            f'  <circle class="peak-marker" id="peak-marker" cx="{px:.1f}" cy="{py:.1f}" r="5" fill="#ef4444" stroke="#ffffff" stroke-width="2" data-peak-at-ms="{peak_at_ms:.1f}" data-peak-rss="{peak_rss_b}" />'
        )
        label_y = max(plot_y + 14, py - 10)
        peak_svg.append(
            f'  <text class="peak-label" x="{px:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="11" font-weight="700" fill="#dc2626">Peak: {peak_mb:.1f} MB @ {peak_at_ms:.1f} ms</text>'
        )

    grid_block = "\n".join(grid_svg)
    peak_block = "\n".join(peak_svg)

    # Legend at top
    legend_svg = (
        f'  <g id="legend" transform="translate({plot_x}, 38)">\n'
        f'    <line x1="0" y1="0" x2="20" y2="0" stroke="#2563eb" stroke-width="3" />\n'
        f'    <text x="25" y="4" font-size="12" font-weight="600" fill="#2563eb">RSS (MB)</text>\n'
        f'    <line x1="110" y1="0" x2="130" y2="0" stroke="#7c3aed" stroke-width="2" stroke-dasharray="4,2" />\n'
        f'    <text x="135" y="4" font-size="12" font-weight="600" fill="#7c3aed">VMS (MB)</text>\n'
        f'    <line x1="220" y1="0" x2="240" y2="0" stroke="#10b981" stroke-width="2" />\n'
        f'    <text x="245" y="4" font-size="12" font-weight="600" fill="#10b981">FDs (Count)</text>\n'
        f'    <circle cx="340" cy="0" r="4" fill="#ef4444" stroke="#ffffff" stroke-width="1.5" />\n'
        f'    <text x="350" y="4" font-size="12" font-weight="600" fill="#dc2626">Peak Marker</text>\n'
        f'  </g>'
    )

    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%" style="font-family: system-ui, -apple-system, sans-serif; background-color: #ffffff;">
  <rect width="{width}" height="{height}" fill="#ffffff" rx="8" />
  <text x="{width / 2:.1f}" y="24" text-anchor="middle" font-size="16" font-weight="bold" fill="#0f172a">{title}</text>
{legend_svg}

  <!-- Axes lines -->
  <line x1="{plot_x}" y1="{plot_y}" x2="{plot_x}" y2="{plot_y + plot_h}" stroke="#94a3b8" stroke-width="1.5" />
  <line x1="{plot_x + plot_w}" y1="{plot_y}" x2="{plot_x + plot_w}" y2="{plot_y + plot_h}" stroke="#94a3b8" stroke-width="1.5" />
  <line x1="{plot_x}" y1="{plot_y + plot_h}" x2="{plot_x + plot_w}" y2="{plot_y + plot_h}" stroke="#94a3b8" stroke-width="1.5" />

  <!-- Axis labels -->
  <text x="{plot_x - 48}" y="{plot_y + plot_h / 2:.1f}" text-anchor="middle" font-size="12" font-weight="bold" fill="#2563eb" transform="rotate(-90 {plot_x - 48} {plot_y + plot_h / 2:.1f})">RSS (MB)</text>
  <text x="{plot_x + plot_w + 48}" y="{plot_y + plot_h / 2:.1f}" text-anchor="middle" font-size="12" font-weight="bold" fill="#7c3aed" transform="rotate(90 {plot_x + plot_w + 48} {plot_y + plot_h / 2:.1f})">VMS (MB)</text>
  <text x="{plot_x + plot_w / 2:.1f}" y="{height - 12}" text-anchor="middle" font-size="12" font-weight="bold" fill="#0f172a">Time (ms)</text>

  <!-- Grid & Ticks -->
{grid_block}

  <!-- Curves -->
  <polyline class="curve-vms" fill="none" stroke="#7c3aed" stroke-width="2" stroke-linejoin="round" stroke-dasharray="4,2" points="{pts_vms}" />
  <polyline class="curve-fds" fill="none" stroke="#10b981" stroke-width="2" stroke-linejoin="round" points="{pts_fds}" />
  <polyline class="curve-rss" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linejoin="round" points="{pts_rss}" />

  <!-- Peak Indicator -->
{peak_block}

  <!-- Machine-readable JSON payload -->
  <script type="application/json" id="proctrace-timeline-data">
{json_payload}
  </script>
</svg>"""
    return svg_content


def render_html(
    timeline: Timeline | dict[str, Any] | str | Path,
    title: str = "proctrace Resource Timeline",
    width: int = 900,
    height: int = 450,
) -> str:
    """Generate a self-contained responsive HTML page containing the SVG plot and metadata."""
    tl = _resolve_timeline(timeline)
    svg_plot = render_svg(tl, title=title, width=width, height=height)
    json_payload = tl.to_json(indent=2)

    peak_rss_mb = ((tl.max_rss_bytes or 0) / _BYTES_PER_MB) if tl.rss else 0.0
    peak_at_ms = tl.peak_at_ms or 0.0
    slope = tl.rss_slope_bytes_per_s
    slope_str = f"{slope / _BYTES_PER_MB:+.2f} MB/s" if slope is not None else "N/A"
    r2_str = f"{tl.r2:.3f}" if tl.rss else "N/A"
    sample_count = len(tl)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      margin: 0;
      padding: 24px;
      background: #f8fafc;
      color: #0f172a;
    }}
    .container {{
      max-width: 960px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 24px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }}
    .chart-container {{
      width: 100%;
      overflow-x: auto;
    }}
    .stats-grid {{
      margin-top: 20px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .stat-card {{
      background: #f1f5f9;
      padding: 12px 16px;
      border-radius: 8px;
    }}
    .stat-label {{
      font-size: 11px;
      color: #64748b;
      text-transform: uppercase;
      font-weight: 700;
      letter-spacing: 0.05em;
    }}
    .stat-val {{
      font-size: 20px;
      font-weight: 700;
      color: #0f172a;
      margin-top: 4px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="chart-container">
{svg_plot}
    </div>
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">Peak RSS</div>
        <div class="stat-val">{peak_rss_mb:.2f} MB</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Peak Time</div>
        <div class="stat-val">{peak_at_ms:.1f} ms</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">RSS Slope</div>
        <div class="stat-val">{slope_str}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Linear Fit (R²)</div>
        <div class="stat-val">{r2_str}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Sample Count</div>
        <div class="stat-val">{sample_count}</div>
      </div>
    </div>
  </div>
  <script type="application/json" id="proctrace-timeline-data">
{json_payload}
  </script>
</body>
</html>"""
    return html_content


def plot_timeline(
    timeline: Timeline | dict[str, Any] | str | Path,
    output_path: str | Path,
    title: str = "proctrace Resource Timeline",
    width: int = 900,
    height: int = 450,
) -> None:
    """Render a timeline to an output file (.html or .svg based on suffix)."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".svg":
        content = render_svg(timeline, title=title, width=width, height=height)
    else:
        content = render_html(timeline, title=title, width=width, height=height)
    out.write_text(content, encoding="utf-8")


__all__ = [
    "plot_timeline",
    "render_html",
    "render_svg",
]
