#!/usr/bin/env python3
"""Render topic-specific article diagrams from a small JSON spec.

The layout mirrors the hand-made article images: a title with an accent
phrase, three numbered story panels showing components and a callout, and
a bottom strip summarising the key idea as a sequence of steps.
"""
import html
import json
import sys
from pathlib import Path

WIDTH, HEIGHT = 1536, 1024
FONT = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif"

ACCENTS = {
    'SRE': '#c084fc',
    'Platform Engineering': '#38bdf8',
    'Distributed Systems': '#fbbf24',
    'Observability': '#34d399',
    'AI & Automation': '#f472b6',
}
TONES = {
    'ok': '#34d399',
    'warn': '#fbbf24',
    'fail': '#f87171',
    'info': '#60a5fa',
}

# Character limits keep every label inside its box at the base font size.
LIMITS = {
    'title': 40, 'accent': 32, 'subtitle': 90,
    'heading': 30, 'caption': 95, 'node': 24, 'note': 90,
    'strip_title': 55, 'strip_caption': 110, 'step_label': 24, 'step_detail': 44,
}


def _node_label(value):
    if any(len(word) > 14 for word in value.split()):
        raise ValueError(f'Diagram node label word too long: {value}')
    return value


def validate_spec(spec):
    """Return a normalised spec or raise ValueError describing the problem."""
    if not isinstance(spec, dict):
        raise ValueError('Diagram spec must be an object')

    def text(value, field, limit, required=True):
        if value is None and not required:
            return ''
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Diagram {field} missing')
        value = ' '.join(value.split())
        if len(value) > limit:
            raise ValueError(f'Diagram {field} longer than {limit} characters')
        return value

    def tone(value):
        if value not in TONES:
            raise ValueError(f'Diagram tone must be one of {sorted(TONES)}')
        return value

    panels = spec.get('panels')
    if not isinstance(panels, list) or len(panels) != 3:
        raise ValueError('Diagram needs exactly three panels')
    steps = spec.get('steps')
    if not isinstance(steps, list) or not 3 <= len(steps) <= 5:
        raise ValueError('Diagram needs three to five steps')

    clean_panels = []
    for panel in panels:
        if not isinstance(panel, dict):
            raise ValueError('Malformed diagram panel')
        nodes = panel.get('nodes')
        if not isinstance(nodes, list) or not 2 <= len(nodes) <= 3:
            raise ValueError('Each diagram panel needs two or three nodes')
        clean_panels.append({
            'heading': text(panel.get('heading'), 'panel heading', LIMITS['heading']),
            'caption': text(panel.get('caption'), 'panel caption', LIMITS['caption']),
            'tone': tone(panel.get('tone')),
            'nodes': [_node_label(text(n, 'node label', LIMITS['node'])) for n in nodes],
            'note': text(panel.get('note'), 'panel note', LIMITS['note']),
        })

    clean_steps = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError('Malformed diagram step')
        clean_steps.append({
            'label': text(step.get('label'), 'step label', LIMITS['step_label']),
            'detail': text(step.get('detail'), 'step detail', LIMITS['step_detail']),
            'tone': tone(step.get('tone')),
        })

    return {
        'title': text(spec.get('title'), 'title', LIMITS['title']),
        'accent': text(spec.get('accent'), 'accent', LIMITS['accent']),
        'subtitle': text(spec.get('subtitle'), 'subtitle', LIMITS['subtitle']),
        'panels': clean_panels,
        'strip_title': text(spec.get('strip_title'), 'strip title', LIMITS['strip_title']),
        'strip_caption': text(spec.get('strip_caption'), 'strip caption', LIMITS['strip_caption']),
        'steps': clean_steps,
    }


def _char_width(size, bold):
    return size * (0.6 if bold else 0.54)


def _fit(value, width_px, size, max_lines, bold=False, min_size=None):
    """Largest font size (down to min_size) at which value wraps cleanly."""
    min_size = min_size or size
    while size > min_size:
        per_char = _char_width(size, bold)
        longest = max(len(word) for word in value.split())
        lines = _wrap(value, width_px, size, max_lines + 1, bold)
        if longest * per_char <= width_px and len(lines) <= max_lines:
            break
        size -= 1
    return size


def _wrap(value, width_px, size, max_lines, bold=False):
    """Greedy word wrap using an average glyph width estimate."""
    per_char = _char_width(size, bold)
    limit = max(1, int(width_px / per_char))
    lines, line = [], ''
    for word in value.split():
        candidate = f'{line} {word}'.strip()
        if len(candidate) <= limit or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip('.,;:') + '…'
    return lines


def _text(x, y, value, size, fill, width, max_lines=1, weight=400,
          anchor='start', line_height=1.25, min_size=None, middle=False):
    """Render wrapped text; with middle=True, y is the block's vertical centre."""
    bold = weight >= 600
    size = _fit(value, width, size, max_lines, bold, min_size)
    lines = _wrap(value, width, size, max_lines, bold=bold)
    if middle:
        block = (len(lines) - 1) * size * line_height
        y = round(y - block / 2 + size * 0.35)
    # Vertically centre multi-line text on y when anchored in the middle.
    spans = ''.join(
        f'<tspan x="{x}" dy="{0 if i == 0 else round(size * line_height)}">{html.escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}">{spans}</text>'), len(lines)


def _arrow(x1, y, x2, color):
    return (f'<path d="M {x1} {y} H {x2 - 10}" stroke="{color}" stroke-width="3" fill="none"/>'
            f'<path d="M {x2 - 14} {y - 7} L {x2} {y} L {x2 - 14} {y + 7} Z" fill="{color}"/>')


def _box(x, y, w, h, stroke, fill='#121c31', glow=False, radius=16):
    shadow = ' filter="url(#glow)"' if glow else ''
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2"{shadow}/>')


def _panel(index, panel, x, y, w, h):
    color = TONES[panel['tone']]
    parts = [_box(x, y, w, h, '#22324f', fill='#0f182b', radius=18)]

    parts.append(f'<text x="{x + 28}" y="{y + 62}" font-size="38" font-weight="800" fill="{color}">{index}.</text>')
    heading, heading_lines = _text(x + 72, y + 60, panel['heading'], 30, color, w - 100, 2, 700, min_size=26)
    parts.append(heading)
    caption_y = y + 60 + heading_lines * 38 + 4
    caption, _ = _text(x + 72, caption_y, panel['caption'], 20, '#b5c2d5', w - 100, 3, min_size=17)
    parts.append(caption)

    # Component chain: the last node carries the panel's tone.
    nodes = panel['nodes']
    gap = 34
    node_w = (w - 56 - gap * (len(nodes) - 1)) // len(nodes)
    node_h = 100
    node_y = y + 212
    for i, label in enumerate(nodes):
        nx = x + 28 + i * (node_w + gap)
        last = i == len(nodes) - 1
        stroke = color if last else '#4b6290'
        parts.append(_box(nx, node_y, node_w, node_h, stroke, glow=last and panel['tone'] == 'fail', radius=14))
        label_svg, _ = _text(round(nx + node_w / 2), node_y + node_h // 2, label, 19, '#f4f7fc',
                             node_w - 12, 3, 650, 'middle', 1.2, min_size=14, middle=True)
        parts.append(label_svg)
        if not last:
            parts.append(_arrow(nx + node_w + 4, node_y + node_h // 2, nx + node_w + gap - 4,
                                color if i == len(nodes) - 2 else '#6b84b5'))

    # Callout explaining what this stage means.
    note_y = node_y + node_h + 30
    note_h = h - (note_y - y) - 26
    parts.append(f'<rect x="{x + 28}" y="{note_y}" width="{w - 56}" height="{note_h}" rx="12" '
                 f'fill="{color}" fill-opacity="0.08" stroke="{color}" stroke-opacity="0.45"/>')
    parts.append(f'<rect x="{x + 28}" y="{note_y}" width="6" height="{note_h}" rx="3" fill="{color}"/>')
    note, _ = _text(x + 54, note_y + note_h // 2, panel['note'], 20, '#e6ecf7', w - 100, 3, 500,
                    min_size=17, middle=True)
    parts.append(note)
    return ''.join(parts)


def render_svg(spec, category):
    spec = validate_spec(spec)
    accent = ACCENTS.get(category, '#60a5fa')
    title_size = 60 if len(spec['title']) + len(spec['accent']) <= 42 else 50

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-label="{html.escape(spec["title"] + " " + spec["accent"] + ": " + spec["subtitle"])}" '
        f'font-family="{FONT}">',
        '<defs>'
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#0b1426"/><stop offset="1" stop-color="#070d1a"/></linearGradient>'
        '<filter id="glow" x="-30%" y="-30%" width="160%" height="160%">'
        '<feGaussianBlur stdDeviation="6" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        '</defs>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>',
        f'<rect width="{WIDTH}" height="6" fill="{accent}"/>',
        f'<text x="{WIDTH // 2}" y="92" font-size="{title_size}" font-weight="800" fill="#ffffff" '
        f'text-anchor="middle" letter-spacing="-1">{html.escape(spec["title"])} '
        f'<tspan fill="{accent}">{html.escape(spec["accent"])}</tspan></text>',
    ]
    subtitle, _ = _text(WIDTH // 2, 146, spec['subtitle'], 28, '#9fb0d0', WIDTH - 160, 1, 400, 'middle')
    parts.append(subtitle)

    margin, gap = 24, 16
    panel_w = (WIDTH - 2 * margin - 2 * gap) // 3
    for i, panel in enumerate(spec['panels']):
        parts.append(_panel(i + 1, panel, margin + i * (panel_w + gap), 182, panel_w, 520))

    # Bottom strip: the key idea as a left-to-right sequence.
    strip_y, strip_h = 718, 284
    parts.append(_box(margin, strip_y, WIDTH - 2 * margin, strip_h, '#22324f', fill='#0f182b', radius=18))
    parts.append(f'<text x="{margin + 32}" y="{strip_y + 54}" font-size="32" font-weight="700" '
                 f'fill="#ffffff">{html.escape(spec["strip_title"])}</text>')
    caption, _ = _text(margin + 32, strip_y + 90, spec['strip_caption'], 21, '#9fb0d0', WIDTH - 2 * margin - 64, 1)
    parts.append(caption)

    steps = spec['steps']
    step_gap = 56
    inner_w = WIDTH - 2 * margin - 64
    step_w = (inner_w - step_gap * (len(steps) - 1)) // len(steps)
    step_h, step_y = 120, strip_y + 126
    for i, step in enumerate(steps):
        color = TONES[step['tone']]
        sx = margin + 32 + i * (step_w + step_gap)
        parts.append(_box(sx, step_y, step_w, step_h, color, fill='#121c31', radius=14))
        label_size = _fit(step['label'], step_w - 24, 22, 2, True, 17)
        label_lines = len(_wrap(step['label'], step_w - 24, label_size, 2, True))
        detail_size = _fit(step['detail'], step_w - 24, 17, 2, False, 14)
        detail_lines = len(_wrap(step['detail'], step_w - 24, detail_size, 2))
        block = label_lines * label_size * 1.2 + 8 + detail_lines * detail_size * 1.25
        top = step_y + (step_h - block) / 2
        label, _ = _text(sx + step_w // 2, round(top + label_size * 0.9), step['label'], label_size, color,
                         step_w - 24, 2, 700, 'middle', 1.2)
        parts.append(label)
        detail_y = round(top + label_lines * label_size * 1.2 + 8 + detail_size * 0.9)
        detail, _ = _text(sx + step_w // 2, detail_y, step['detail'], detail_size, '#c9d4e8',
                          step_w - 24, 2, 400, 'middle')
        parts.append(detail)
        if i < len(steps) - 1:
            parts.append(_arrow(sx + step_w + 8, step_y + step_h // 2, sx + step_w + step_gap - 8,
                                TONES[steps[i + 1]['tone']]))

    parts.append('</svg>')
    return ''.join(parts)


def alt_text(spec):
    spec = validate_spec(spec)
    return f'{spec["title"]} {spec["accent"]}: {spec["subtitle"]}'


if __name__ == '__main__':
    # Usage: diagram.py SPEC.json CATEGORY OUT.svg
    spec_path, category, out = sys.argv[1:4]
    Path(out).write_text(render_svg(json.loads(Path(spec_path).read_text()), category), encoding='utf-8')
