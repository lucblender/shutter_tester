import os
import statistics
import math
from datetime import datetime
from pathlib import Path


def us_to_shutter_fraction(us: int) -> str:
    """Convert microseconds to shutter speed fraction 1/n format."""
    try:
        # Round to nearest common shutter speed denominator
        fraction = 1_000_000 / us
        
        # Common denominators
        common = [15, 20, 25, 30, 40, 50, 60, 80, 100, 125, 160, 200, 250, 
                  320, 400, 500, 640, 800, 1000, 1250, 1600, 2000, 2500, 
                  3200, 4000, 5000, 6400, 8000]
        
        # Find closest common denominator
        closest = min(common, key=lambda x: abs(x - fraction))
        return f"1/{closest}"
    except (ValueError, ZeroDivisionError):
        return "N/A"


def format_duration(us: int) -> str:
    """Format duration as shutter fraction with time in engineering format.
    
    Example: 12696 us -> 1/79 (12.70 ms)
    """
    try:
        fraction = us_to_shutter_fraction(us)
        
        # Convert to appropriate unit with engineering format
        if us >= 1_000_000:
            # seconds
            seconds = us / 1_000_000
            return f"{fraction} ({seconds:.2f} s)"
        elif us >= 1_000:
            # milliseconds
            ms = us / 1_000
            return f"{fraction} ({ms:.2f} ms)"
        else:
            # microseconds
            return f"{fraction} ({us:.0f} µs)"
    except (ValueError, ZeroDivisionError):
        return "N/A"


def calculate_statistics(measurements: list) -> dict:
    """Calculate average, median, and error for a list of measurements."""
    if not measurements:
        return {
            "average": 0,
            "median": 0,
            "stddev": 0,
            "min": 0,
            "max": 0,
            "count": 0
        }
    
    values = [m["us"] for m in measurements]
    count = len(values)
    average = statistics.mean(values) if count > 0 else 0
    median = statistics.median(values) if count > 0 else 0
    stddev = statistics.stdev(values) if count > 1 else 0
    
    return {
        "average": average,
        "median": median,
        "stddev": stddev,
        "min": min(values) if values else 0,
        "max": max(values) if values else 0,
        "count": count
    }


def create_curve_svg(curve_data: dict, width: int = 600, height: int = 200) -> str:
    """Create an SVG representation of a curve."""
    if not curve_data or not curve_data.get("points"):
        return ""
    
    points = curve_data["points"]
    if len(points) < 2:
        return ""
    
    # Find min/max for scaling
    times = [p[0] for p in points]
    values = [p[1] for p in points]
    
    min_time = min(times)
    max_time = max(times)
    min_val = min(values)
    max_val = max(values)
    
    time_range = max_time - min_time if max_time > min_time else 1
    val_range = max_val - min_val if max_val > min_val else 1
    
    # Padding
    padding = 40
    plot_width = width - 2 * padding
    plot_height = height - 2 * padding
    
    # Scale functions
    def scale_x(t):
        return padding + (t - min_time) / time_range * plot_width
    
    def scale_y(v):
        return height - padding - (v - min_val) / val_range * plot_height
    
    # Build SVG
    svg = f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">\n'
    svg += '  <style>\n'
    svg += '    line.grid { stroke: #e0e0e0; stroke-width: 0.5; }\n'
    svg += '    line.axis { stroke: #333; stroke-width: 1; }\n'
    svg += '    polyline.curve { stroke: #2563eb; stroke-width: 2; fill: none; }\n'
    svg += '    circle.point { fill: #2563eb; r: 3; }\n'
    svg += '    circle.trigger { fill: #dc2626; r: 4; }\n'
    svg += '    text.label { font-size: 11px; fill: #666; }\n'
    svg += '  </style>\n'
    
    # Grid and axes
    svg += f'  <line class="axis" x1="{padding}" y1="{height - padding}" '
    svg += f'x2="{width - padding}" y2="{height - padding}"/>\n'
    svg += f'  <line class="axis" x1="{padding}" y1="{padding}" '
    svg += f'x2="{padding}" y2="{height - padding}"/>\n'
    
    # Grid lines (vertical)
    for i in range(5):
        x = padding + (i / 4) * plot_width
        svg += f'  <line class="grid" x1="{x}" y1="{padding}" '
        svg += f'x2="{x}" y2="{height - padding}"/>\n'
    
    # Grid lines (horizontal)
    for i in range(5):
        y = padding + (i / 4) * plot_height
        svg += f'  <line class="grid" x1="{padding}" y1="{y}" '
        svg += f'x2="{width - padding}" y2="{y}"/>\n'
    
    # Axis labels
    svg += f'  <text class="label" x="{width - padding + 5}" y="{height - padding + 5}">Time (µs)</text>\n'
    svg += f'  <text class="label" x="{padding - 35}" y="{padding - 5}">Value</text>\n'
    
    # Draw polyline curve
    points_str = " ".join([f"{scale_x(p[0])},{scale_y(p[1])}" for p in points])
    svg += f'  <polyline class="curve" points="{points_str}"/>\n'
    
    # Draw points
    for p in points:
        x = scale_x(p[0])
        y = scale_y(p[1])
        svg += f'  <circle class="point" cx="{x}" cy="{y}"/>\n'
    
    # Mark trigger point
    trig_idx = curve_data.get("trig_index", 0)
    if 0 <= trig_idx < len(points):
        trig_point = points[trig_idx]
        x = scale_x(trig_point[0])
        y = scale_y(trig_point[1])
        svg += f'  <circle class="trigger" cx="{x}" cy="{y}"/>\n'
        svg += f'  <text class="label" x="{x + 5}" y="{y - 5}">Trig</text>\n'
    
    # Draw threshold line
    thr = curve_data.get("thr", 0)
    if thr:
        y_thr = scale_y(thr)
        svg += f'  <line class="axis" x1="{padding}" y1="{y_thr}" '
        svg += f'x2="{width - padding}" y2="{y_thr}" stroke="#ff9800" stroke-dasharray="4,4"/>\n'
        svg += f'  <text class="label" x="{padding + 5}" y="{y_thr - 5}" fill="#ff9800">Thr={thr}</text>\n'
    
    svg += '</svg>\n'
    return svg


class ReportGenerator:
    """Generate markdown reports from measurement data."""
    
    def __init__(self, output_dir: str = None):
        """Initialize report generator.
        
        Args:
            output_dir: Directory to save report. Defaults to current directory.
        """
        self.output_dir = output_dir or os.getcwd()
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        self.assets_dir = None
        self.timestamp = None
    
    def generate(self, report_data: dict) -> str:
        """Generate markdown report from report data.
        
        Args:
            report_data: Dictionary containing measurement data from the app
            
        Returns:
            Path to generated report file
        """
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"report_{self.timestamp}.md"
        filepath = os.path.join(self.output_dir, filename)
        
        # Create assets directory for SVG files
        self.assets_dir = os.path.join(self.output_dir, f"report_{self.timestamp}_assets")
        Path(self.assets_dir).mkdir(parents=True, exist_ok=True)
        
        markdown = self._build_report(report_data)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(markdown)
        
        return filepath
    
    def _build_report(self, report_data: dict) -> str:
        """Build complete markdown report."""
        lines = []
        
        # Header
        lines.append("# Shutter Speed Test Report\n")
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Camera info
        lines.append("## Camera Configuration\n")
        lines.append(f"- Camera: {report_data.get('camera', 'N/A')}")
        lines.append(f"- Lens: {report_data.get('lens', 'N/A')}")
        lines.append(f"- Trigger Value: {report_data.get('trigger_value', 'N/A')}")
        lines.append(f"- Minimum Speed: {report_data.get('min_speed', 0)} µs")
        lines.append(f"- Store Curve: {'Yes' if report_data.get('store_curve') else 'No'}")
        lines.append(f"- Repetitions per Speed: {report_data.get('repetitions', 'N/A')}\n")
        
        # Results
        lines.append("## Measurement Results\n")
        
        measurements = report_data.get("measurements", {})
        shutter_speeds = report_data.get("shutter_speeds", [])
        
        # Summary table
        lines.append("| Shutter Speed | Count | Average | Median | Std Dev | Min | Max |")
        lines.append("|---|---|---|---|---|---|---|")
        
        for speed in shutter_speeds:
            meas = measurements.get(speed, [])
            stats = calculate_statistics(meas)
            
            if stats["count"] == 0:
                lines.append(f"| {speed} | 0 | — | — | — | — | — |")
            else:
                avg_str = format_duration(stats['average'])
                med_str = format_duration(stats['median'])
                std_str = format_duration(stats['stddev'])
                min_str = format_duration(stats['min'])
                max_str = format_duration(stats['max'])
                
                lines.append(f"| {speed} | {stats['count']} | {avg_str} | {med_str} | {std_str} | {min_str} | {max_str} |")
        
        lines.append("")
        
        # Detailed results per speed
        lines.append("## Detailed Measurements\n")
        
        for speed in shutter_speeds:
            meas = measurements.get(speed, [])
            if not meas:
                continue
            
            stats = calculate_statistics(meas)
            
            lines.append(f"### {speed}\n")
            lines.append(f"- Expected: {speed}")
            lines.append(f"- Measured Average: {format_duration(stats['average'])}")
            lines.append(f"- Measured Median: {format_duration(stats['median'])}")
            lines.append(f"- Standard Deviation: {format_duration(stats['stddev'])}")
            lines.append(f"- Range: {format_duration(stats['min'])} – {format_duration(stats['max'])}")
            lines.append(f"- Repetitions: {stats['count']}\n")
            
            # Individual measurements
            lines.append("#### Individual Measurements\n")
            lines.append("| # | Duration | Timestamp | Curve |")
            lines.append("|---|---|---|---|")
            
            for i, m in enumerate(meas, 1):
                us = m["us"]
                timestamp = m.get("timestamp", "—")
                has_curve = "✓" if m.get("curve") else "—"
                lines.append(f"| {i} | {format_duration(us)} | {timestamp} | {has_curve} |")
            
            lines.append("")
            
            # Curve graphs
            if report_data.get("store_curve"):
                for i, m in enumerate(meas, 1):
                    if m.get("curve"):
                        svg = create_curve_svg(m["curve"])
                        if svg:
                            # Save SVG to file
                            svg_filename = f"{speed.replace('/', '_')}_curve_{i}.svg"
                            svg_path = os.path.join(self.assets_dir, svg_filename)
                            
                            with open(svg_path, 'w', encoding='utf-8') as f:
                                f.write(svg)
                            
                            # Reference in markdown using relative path
                            assets_folder = os.path.basename(self.assets_dir)
                            rel_path = f"{assets_folder}/{svg_filename}"
                            lines.append(f"**Curve {i}:**\n")
                            lines.append(f"![Curve {i}]({rel_path})\n")
                            lines.append("")
        
        return "\n".join(lines)
