#!/usr/bin/env python3
"""Generate, review, validate and stage Astro articles. Publishing is handled by CI."""
import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / 'src/content/articles'
IMAGES = ROOT / 'public/images/articles'
PLAN = json.loads((ROOT / 'scripts/editorial_plan.json').read_text(encoding='utf-8'))
CATEGORIES = {'SRE', 'Platform Engineering', 'Distributed Systems', 'Observability', 'AI & Automation'}
MODEL = os.getenv('OPENAI_MODEL', 'gpt-5-mini')


def existing_articles():
    return [p for p in ARTICLES.rglob('*') if p.is_file() and p.suffix in {'.md', '.mdx'}
            and not re.search(r'(?m)^draft:\s*true\s*$', p.read_text(encoding='utf-8'))]


def slug_used(slug):
    return any(p.stem == slug for p in ARTICLES.rglob('*') if p.is_file() and p.suffix in {'.md', '.mdx'})


def ask_llm(system, user):
    payload = json.dumps({
        'model': MODEL,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'response_format': {'type': 'json_object'},
        'max_completion_tokens': 9000,
    }).encode('utf-8')
    request = urllib.request.Request('https://api.openai.com/v1/chat/completions', data=payload,
        headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=240) as response:
        result = json.load(response)
    choice = result['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('AI response incomplete: ' + str(choice.get('finish_reason')))
    content = choice['message'].get('content')
    if not content:
        raise ValueError('AI returned empty content')
    return json.loads(content)


def draft_article(item):
    prompt = f'''Write an original, technically rigorous article for The Reliability Loop.
Title: {item['title']}. Category: {item['category']}.
Return ONLY JSON with description (140–160 characters), tags (4–6 strings), body (Markdown, NO H1/frontmatter).
Aim for 1500–2200 substantive words, 5–8 H2 sections, 2–4 sentences per ordinary paragraph.
Include operational tradeoffs, failure modes, verification guidance and at least one useful technical example.
If including configuration or code, verify exact syntax and behavior in your reasoning. Distinguish Prometheus metric_relabel_configs action: drop (drops entire samples) from labeldrop (removes labels); keep filters whole samples too. Avoid fabricated capabilities or pseudocode masquerading as deployable configuration.
Use hypothetical examples with explicit labels. No fabricated incidents, measurements, citations, proprietary information, or first-hand employer claims. No external images, Mermaid, or Markdown H1. Avoid generic filler and LinkedIn-style single-sentence paragraphs.'''
    return ask_llm('You are a senior SRE technical author. Return valid JSON only.', prompt)


def review_article(item, article):
    prompt = f'''Independently audit this proposed technical article. Do NOT assume it is correct.
Title: {item['title']}; category: {item['category']}.
Focus on exact semantics of code/configuration, vendor/tool behavior, cardinality and sampling, architecture feasibility, misleading generalizations, unsupported numerical claims, safety/security risks, and conceptual accuracy. Identify specific line or excerpt and explain the failure mode. For example, Prometheus metric_relabel_configs action: drop removes matching metric samples, not just a label; action: labeldrop removes matching label names. Look for other mistakes too.
Return JSON ONLY: {{"approved": boolean, "issues": [{{"severity":"critical|major|minor", "excerpt":"...", "explanation":"...", "correction":"..."}}]}}.
Approve only when no critical or major issues remain. If uncertain about technical semantics, mark a major issue and request verification; do not guess. An independent LLM review is not proof of correctness.
ARTICLE JSON:\n{json.dumps(article, ensure_ascii=False)}'''
    result = ask_llm('You are a skeptical senior production reliability reviewer. Return JSON only.', prompt)
    if not isinstance(result, dict) or type(result.get('approved')) is not bool or not isinstance(result.get('issues'), list):
        raise ValueError('Reviewer response malformed')
    for issue in result['issues']:
        if not isinstance(issue, dict) or issue.get('severity') not in {'critical', 'major', 'minor'}:
            raise ValueError('Reviewer issue malformed')
    if any(i['severity'] in {'critical', 'major'} for i in result['issues']):
        result['approved'] = False
    return result


def revise_article(item, article, review):
    prompt = f'''Revise this article to resolve every reviewer issue while preserving technical depth and the original topic.
Return JSON ONLY with exactly description, tags, body (Markdown without frontmatter or H1). Do not claim technical facts you cannot support. Remove or clearly mark questionable code as conceptual instead of inventing working syntax. Keep 1500–2200 substantive words, 5–8 H2 sections, operational guidance, tradeoffs and a thoughtful conclusion.
Title: {item['title']}; category: {item['category']}.
REVIEW: {json.dumps(review, ensure_ascii=False)}
ARTICLE: {json.dumps(article, ensure_ascii=False)}'''
    return ask_llm('You are a senior SRE editor correcting specific technical errors. Return JSON only.', prompt)


def validate(item, obj):
    if not isinstance(obj, dict):
        raise ValueError('Invalid article JSON')
    desc, tags, body = obj.get('description'), obj.get('tags'), obj.get('body')
    if not isinstance(desc, str) or not 100 <= len(desc) <= 180:
        raise ValueError('Description length invalid')
    if not isinstance(tags, list) or not 3 <= len(tags) <= 8 or not all(isinstance(t, str) and 2 <= len(t) <= 50 for t in tags):
        raise ValueError('Tags invalid')
    if not isinstance(body, str):
        raise ValueError('Body missing')
    # Normalize generated H1 headings outside fenced code blocks.
    # Astro displays the article title separately.
    body = re.sub(r'\A\s*#\s+[^\n]+\n+', '', body).strip()

    parts = re.split(r'(?m)(^```[^\n]*\n.*?^```\s*$)', body, flags=re.DOTALL)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r'(?m)^#\s+', '## ', parts[i])
    body = ''.join(parts).strip()
    words = len(re.findall(r"\b[\w'-]+\b", body))
    if not 1200 <= words <= 2600:
        raise ValueError(f'Article length {words} outside 1200–2600')
    if len(re.findall(r'(?m)^##\s+', body)) < 5:
        raise ValueError('Insufficient section structure')
    if re.search(r'(?m)^#\s+', body):
        raise ValueError('Body contains duplicate H1')
    if re.search(r'(?i)(at paypal|our internal systems|in my team at paypal)', body):
        raise ValueError('Potential employer-specific reference')
    if re.search(r'(?i)\b(lorem ipsum|as an ai language model)\b', body):
        raise ValueError('Placeholder text')
    if re.search(r'(?m)^---\s*$', body):
        raise ValueError('Unexpected YAML separator in body')
    # Prevent recurrence of the known sample-dropping mistake; human-independent checks are still limited.
    for match in re.finditer(r'(?ms)^```(?:yaml|yml)\s*\n(.*?)^```', body):
        snippet = match.group(1)
        if 'metric_relabel_configs:' in snippet and re.search(r'(?m)^\s*action:\s*(?:drop|keep)\s*$', snippet):
            raise ValueError('Metric relabel example uses sample-dropping action; requires manual verification')
    return desc, tags, body


def diagram_svg(item):
    title = html.escape(item['title'])
    palette = {
        'SRE': ('#9400d3', '#d8bfd8'),
        'Platform Engineering': ('#087fdb', '#9bd9f5'),
        'Distributed Systems': ('#d97706', '#f1c77c'),
        'Observability': ('#07876f', '#9bd9c8'),
        'AI & Automation': ('#8b36d9', '#ed80e9'),
    }
    accent, light = palette[item['category']]
    stages = ['Observe', 'Understand', 'Decide', 'Act', 'Verify']
    cards = []
    for i, stage in enumerate(stages):
        x = 40 + i * 224
        cards.append(f'<rect x="{x}" y="208" width="190" height="106" rx="15" fill="#182336" stroke="{light}" stroke-opacity=".5"/><text x="{x+95}" y="268" fill="#f4f7fc" font-size="22" text-anchor="middle" font-weight="650">{stage}</text>')
        if i < len(stages) - 1:
            cards.append(f'<path d="M {x+195} 261 h 23" stroke="{light}" stroke-width="3"/><path d="M {x+211} 254 l 8 7 -8 7" fill="none" stroke="{light}" stroke-width="3"/>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="420" viewBox="0 0 1200 420" role="img" aria-label="Conceptual reliability control loop for {title}">
<rect width="1200" height="420" fill="#0c1423"/><rect width="1200" height="8" fill="{accent}"/><text x="40" y="65" font-family="Arial,sans-serif" font-size="20" font-weight="bold" letter-spacing="3" fill="{light}">{html.escape(item['category'].upper())}</text><text x="40" y="112" font-family="Arial,sans-serif" font-size="31" font-weight="bold" fill="#ffffff">Engineering control loop</text><text x="40" y="151" font-family="Arial,sans-serif" font-size="19" fill="#b5c2d5">A conceptual workflow — adapt validation and controls to the system</text><g font-family="Arial,sans-serif">{''.join(cards)}</g><path d="M 1090 326 v 35 H 135 v -35" fill="none" stroke="{accent}" stroke-width="3" stroke-dasharray="8 8"/><text x="600" y="393" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" fill="#b5c2d5">Feedback informs the next decision</text></svg>'''


def write_article(item, validated):
    desc, tags, body = validated
    slug = item['slug']
    ARTICLES.mkdir(parents=True, exist_ok=True)
    IMAGES.mkdir(parents=True, exist_ok=True)
    article_path = ARTICLES / (slug + '.md')
    image_path = IMAGES / (slug + '.svg')
    if article_path.exists() or image_path.exists():
        raise ValueError(f'Article or image already exists: {slug}')
    meta = '\n'.join([
        '---', 'title: ' + json.dumps(item['title'], ensure_ascii=False),
        'description: ' + json.dumps(desc, ensure_ascii=False),
        'pubDate: ' + dt.date.today().isoformat(),
        'author: Rahul Siddipeta', 'category: ' + json.dumps(item['category']),
        'tags: ' + json.dumps(tags, ensure_ascii=False),
        'readTime: ' + json.dumps(f'{max(1, round(len(body.split()) / 210))} min read'),
        'featured: false', 'draft: false', '---', '',
    ])
    article_path.write_text(meta + f'![Conceptual engineering control loop for {item["title"]}](/images/articles/{slug}.svg)\n\n' + body + '\n', encoding='utf-8')
    image_path.write_text(diagram_svg(item), encoding='utf-8')
    print(f'Created {article_path.relative_to(ROOT)}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=int, choices=[1, 6], required=True)
    args = parser.parse_args()
    if not os.environ.get('OPENAI_API_KEY'):
        sys.exit('Missing OPENAI_API_KEY')
    count = len(existing_articles())
    if count >= 25:
        print(f'Publication has {count} articles; stopping.')
        return
    available = [p for p in PLAN if not slug_used(p['slug'])]
    quantity = min(args.batch, 25 - count, len(available))
    if quantity == 0:
        sys.exit('No unused editorial topics remain')
    print(f'Existing published articles: {count}; generating {quantity} article(s) with {MODEL}', flush=True)
    for item in available[:quantity]:
        if item['category'] not in CATEGORIES:
            raise ValueError('Unknown category')
        last_error = None
        for attempt in range(2):
            try:
                article = draft_article(item)
                for review_round in range(2):
                    validated = validate(item, article)
                    article = {'description': validated[0], 'tags': validated[1], 'body': validated[2]}
                    review = review_article(item, article)
                    if review['approved']:
                        print(f'Technical review passed for {item["slug"]} (round {review_round+1})', flush=True)
                        write_article(item, validated)
                        break
                    print(f'Technical review rejected {item["slug"]}: {json.dumps(review["issues"], ensure_ascii=False)[:2000]}', flush=True)
                    if review_round == 1:
                        raise ValueError('Technical review failed after revision')
                    article = revise_article(item, article, review)
                break
            except (ValueError, KeyError, json.JSONDecodeError, urllib.error.HTTPError, urllib.error.URLError) as exc:
                last_error = exc
                print(f'Generation/review failed for {item["slug"]}, attempt {attempt+1}: {exc}', file=sys.stderr, flush=True)
                if attempt == 1:
                    raise
                time.sleep(3)
        if last_error and not (ARTICLES / (item['slug'] + '.md')).exists():
            raise last_error
    print('Generation complete; Astro build must pass before publishing.', flush=True)


if __name__ == '__main__':
    main()
