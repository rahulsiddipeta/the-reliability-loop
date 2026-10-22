#!/usr/bin/env python3
"""Generate validated Astro articles and commit them via the GitHub workflow."""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTICLES = ROOT / 'src/content/articles'
IMAGES = ROOT / 'public/images/articles'
PLAN = json.loads((ROOT / 'scripts/editorial_plan.json').read_text())
CATEGORIES = {'SRE', 'Platform Engineering', 'Distributed Systems', 'Observability', 'AI & Automation'}


def existing_articles():
    return [p for p in ARTICLES.rglob('*') if p.is_file() and p.suffix in {'.md', '.mdx'} and not re.search(r'(?m)^draft:\s*true\s*$', p.read_text(encoding='utf-8'))]


def slug_used(slug):
    return any(p.stem == slug for p in ARTICLES.rglob('*') if p.suffix in {'.md', '.mdx'})


def ai_article(item, model):
    prompt = f'''Write an original, technically rigorous article for The Reliability Loop, a serious engineering publication by Rahul Siddipeta. Title: {item['title']}. Category: {item['category']}.
Return ONLY a JSON object with keys description (140-160 characters), tags (array of 4-6 specific strings), and body (Markdown string, without frontmatter or H1 title).
Requirements: 1500-2200 words of substantive original technical content; 2-4 sentences per ordinary paragraph; 5-8 descriptive H2 headings; one practical code/config snippet when appropriate; an explicit tradeoff/failure-mode analysis; practical verification/operational guidance; a thoughtful conclusion. Include an original conceptual architecture description using clear numbered steps in the prose. Do not include Mermaid or external image references. Do not invent real-world incidents, benchmark results, citations, quotes, proprietary PayPal information, or unverified numbers. Clearly label hypothetical examples. Avoid hype, fluff, repetitive statements, and LinkedIn-style one-line paragraphs. Do not claim first-hand incidents or company-specific implementations.'''
    payload = json.dumps({
        'model': model,
        'messages': [
            {'role': 'system', 'content': 'You are a rigorous senior SRE technical editor. Return valid JSON only.'},
            {'role': 'user', 'content': prompt},
        ],
        'response_format': {'type': 'json_object'},
        'max_completion_tokens': 9000,
    }).encode()
    req = urllib.request.Request('https://api.openai.com/v1/chat/completions', data=payload, headers={
        'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'],
        'Content-Type': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=180) as response:
        result = json.load(response)
    return json.loads(result['choices'][0]['message']['content'])


def diagram_svg(item):
    # Deterministic original SVG; no third-party assets or unsupported diagrams.
    import html
    title = html.escape(item['title'])
    cat = item['category']
    palette = {
        'SRE': ('#9400d3', '#d8bfd8'),
        'Platform Engineering': ('#087fdb', '#9bd9f5'),
        'Distributed Systems': ('#d97706', '#f1c77c'),
        'Observability': ('#07876f', '#9bd9c8'),
        'AI & Automation': ('#8b36d9', '#ed80e9'),
    }
    accent, light = palette[cat]
    stages = ['Observe', 'Understand', 'Decide', 'Act', 'Verify']
    cards = []
    for i, stage in enumerate(stages):
        x = 40 + i * 224
        cards.append(f'<rect x="{x}" y="208" width="190" height="106" rx="15" fill="#182336" stroke="{light}" stroke-opacity=".5"/><text x="{x+95}" y="268" fill="#f4f7fc" font-size="22" text-anchor="middle" font-weight="650">{stage}</text>')
        if i < len(stages)-1:
            cards.append(f'<path d="M {x+195} 261 h 23" stroke="{light}" stroke-width="3"/><path d="M {x+211} 254 l 8 7 -8 7" fill="none" stroke="{light}" stroke-width="3"/>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="420" viewBox="0 0 1200 420" role="img" aria-label="Conceptual reliability control loop for {title}">
<rect width="1200" height="420" fill="#0c1423"/><rect width="1200" height="8" fill="{accent}"/><text x="40" y="65" font-family="Arial,sans-serif" font-size="20" font-weight="bold" letter-spacing="3" fill="{light}">{html.escape(cat.upper())}</text><text x="40" y="112" font-family="Arial,sans-serif" font-size="31" font-weight="bold" fill="#ffffff">Engineering control loop</text><text x="40" y="151" font-family="Arial,sans-serif" font-size="19" fill="#b5c2d5">A conceptual workflow — adapt validation and controls to the system</text><g font-family="Arial,sans-serif">{''.join(cards)}</g><path d="M 1090 326 v 35 H 135 v -35" fill="none" stroke="{accent}" stroke-width="3" stroke-dasharray="8 8"/><text x="600" y="393" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" fill="#b5c2d5">Feedback informs the next decision</text></svg>'''


def validate(item, obj):
    if not isinstance(obj, dict): raise ValueError('Invalid JSON response')
    desc, tags, body = obj.get('description'), obj.get('tags'), obj.get('body')
    if not isinstance(desc, str) or not 100 <= len(desc) <= 180: raise ValueError('Description length invalid')
    if not isinstance(tags, list) or not 3 <= len(tags) <= 8 or not all(isinstance(t, str) and 2 <= len(t) <= 50 for t in tags): raise ValueError('Tags invalid')
    if not isinstance(body, str): raise ValueError('Body missing')
    words = len(re.findall(r"\b[\w'-]+\b", body))
    if not 1200 <= words <= 2600: raise ValueError(f'Article length {words} outside 1200–2600')
    if len(re.findall(r'(?m)^##\s+', body)) < 5: raise ValueError('Insufficient section structure')
    if re.search(r'(?m)^#\s+', body): raise ValueError('Body contains duplicate H1')
    if re.search(r'(?i)(at paypal|our internal systems|in my team at paypal)', body): raise ValueError('Potential employer-specific reference')
    if re.search(r'(?i)\b(lorem ipsum|as an ai language model)\b', body): raise ValueError('Placeholder text')
    if re.search(r'(?m)^---\s*$', body): raise ValueError('Unexpected YAML separator in body')
    return desc, tags, body


def write_article(item, obj):
    desc, tags, body = validate(item, obj)
    slug = item['slug']
    ARTICLES.mkdir(parents=True, exist_ok=True)
    IMAGES.mkdir(parents=True, exist_ok=True)
    image_path = IMAGES / (slug + '.svg')
    article_path = ARTICLES / (slug + '.md')
    if article_path.exists() or image_path.exists(): raise ValueError(f'Article or image already exists: {slug}')
    # JSON string literals are also valid YAML quoted strings.
    meta = '\n'.join([
        '---',
        'title: ' + json.dumps(item['title'], ensure_ascii=False),
        'description: ' + json.dumps(desc, ensure_ascii=False),
        'pubDate: ' + dt.date.today().isoformat(),
        'author: Rahul Siddipeta',
        'category: ' + json.dumps(item['category']),
        'tags: ' + json.dumps(tags, ensure_ascii=False),
        'readTime: ' + json.dumps(f'{max(1, round(len(body.split()) / 210))} min read'),
        'featured: false',
        'draft: false',
        '---', '',
    ])
    article_path.write_text(meta + f'![Conceptual engineering control loop for {item["title"]}](/images/articles/{slug}.svg)\n\n' + body.strip() + '\n', encoding='utf-8')
    image_path.write_text(diagram_svg(item), encoding='utf-8')
    print(f'Created {article_path.relative_to(ROOT)}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=int, choices=[1, 6], required=True)
    args = parser.parse_args()
    if not os.environ.get('OPENAI_API_KEY'): sys.exit('Missing OPENAI_API_KEY')
    count = len(existing_articles())
    remaining = max(0, 25 - count)
    if remaining == 0:
        print(f'Publication has {count} articles; stopping.')
        return
    available = [p for p in PLAN if not slug_used(p['slug'])]
    quantity = min(args.batch, remaining, len(available))
    if quantity == 0: sys.exit('No unused editorial topics remain; no articles generated.')
    model = os.getenv('OPENAI_MODEL', 'gpt-5-mini')
    print(f'Existing published articles: {count}; generating {quantity} article(s) with {model}')
    for item in available[:quantity]:
        if item['category'] not in CATEGORIES: raise ValueError('Unknown category')
        for attempt in range(3):
            try:
                article = ai_article(item, model)
                write_article(item, article)
                break
            except (ValueError, urllib.error.HTTPError, urllib.error.URLError) as exc:
                print(f'Generation/validation failed for {item["slug"]}, attempt {attempt+1}: {exc}', file=sys.stderr)
                if attempt == 2: raise
                time.sleep(3)
    print('Generation complete; Astro build must pass before publishing.')


if __name__ == '__main__':
    main()
