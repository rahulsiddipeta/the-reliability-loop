#!/usr/bin/env python3
"""Re-render article diagrams from scripts/diagrams/<slug>.json.

Writes public/images/articles/<slug>.svg and updates the article's image
alt text. Run after editing a spec or the renderer's styling.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagram

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / 'scripts/diagrams'
ARTICLES = ROOT / 'src/content/articles'
IMAGES = ROOT / 'public/images/articles'


def render(slug):
    article = ARTICLES / f'{slug}.md'
    text = article.read_text(encoding='utf-8')
    category = json.loads(re.search(r'(?m)^category:\s*(.+)$', text).group(1))
    spec = json.loads((SPECS / f'{slug}.json').read_text(encoding='utf-8'))

    (IMAGES / f'{slug}.svg').write_text(diagram.render_svg(spec, category), encoding='utf-8')

    image = f'/images/articles/{slug}.svg'
    alt = diagram.alt_text(spec).replace(']', ')').replace('[', '(')
    updated, count = re.subn(rf'!\[[^\]]*\]\({re.escape(image)}\)', f'![{alt}]({image})', text, count=1)
    if count != 1:
        raise ValueError(f'{slug}: image reference {image} not found')
    article.write_text(updated, encoding='utf-8')
    print(f'Rendered {slug}')


if __name__ == '__main__':
    slugs = sys.argv[1:] or sorted(p.stem for p in SPECS.glob('*.json'))
    for slug in slugs:
        render(slug)
