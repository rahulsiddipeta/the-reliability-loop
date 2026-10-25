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
# Shared writing rules for every prompt that produces article text; each one
# addresses a recurring reason the technical reviewer rejected drafts.
CODE_RULES = (
    'Put every command, query (including PromQL), API call, endpoint path and configuration snippet '
    'in a fenced code block with a language tag; never write them inline in prose. '
    'Do not use Prometheus metric_relabel_configs with action: drop or action: keep. '
    'If showing labeldrop, scope it to specific audited metrics, never drop le or quantile, and warn that '
    'merging counter series breaks rate() and increase(). '
    'Mark example hostnames and values as placeholders. '
    'Use at most three code blocks, each short and essential. '
    'Never put credentials, tokens or passwords in commands, including via variables or headers.'
)


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
    # Retry transient API failures (rate limits, 5xx, timeouts) with backoff.
    for api_attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                result = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or api_attempt == 3:
                raise
            print(f'OpenAI API returned {exc.code}; retrying', flush=True)
        except (urllib.error.URLError, TimeoutError) as exc:
            if api_attempt == 3:
                raise
            print(f'OpenAI API request failed ({exc}); retrying', flush=True)
        time.sleep(15 * (api_attempt + 1))
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
{CODE_RULES}
Use hypothetical examples with explicit labels. No fabricated incidents, measurements, citations, proprietary information, or first-hand employer claims. No external images, Mermaid, or Markdown H1. Avoid generic filler and LinkedIn-style single-sentence paragraphs.'''
    return ask_llm('You are a senior SRE technical author. Return valid JSON only.', prompt)


def review_article(item, article):
    body = article.get('body', '')

    if not isinstance(body, str):
        raise ValueError('Reviewer received invalid article body')

    # Identify fenced code blocks before requesting the review.
    # Each block must receive an explicit assessment.
    code_blocks = re.findall(
        r'(?ms)^```[^\n]*\n.*?^```[ \t]*$',
        body
    )

    numbered_blocks = [
        {
            'id': index,
            'content': block
        }
        for index, block in enumerate(code_blocks, start=1)
    ]

    prompt = f'''You are performing an independent production-engineering
technical audit for The Reliability Loop.

ARTICLE TITLE: {item['title']}
CATEGORY: {item['category']}

Do not assume the article is technically correct.

MANDATORY REVIEW REQUIREMENTS:

1. Examine every fenced code/configuration block independently.

2. For each block:
   - Identify its language or tool.
   - Explain what the commands or configuration actually do.
   - Check syntax and semantic correctness.
   - Identify destructive behavior, dropped data, security risks,
     misleading examples, and operational failure modes.
   - Determine whether the surrounding explanation matches
     the actual behavior.
   - Mark uncertain semantics as requiring verification.

3. Specifically distinguish:
   - Prometheus drop/keep versus labeldrop/labelkeep.
   - Metric-series cardinality versus sample ingestion volume.
   - Recording rules versus source-series elimination.
   - Metrics sampling versus trace sampling.
   - Hypothetical architecture versus deployable configuration.

4. Review the article's non-code technical claims, architecture,
   tradeoffs, operational recommendations, and numerical examples.

5. Do not approve articles containing unresolved critical or
   major technical issues.

6. If the article contains unfenced commands or configuration,
   report that as a major issue when it creates ambiguity.

7. Do not invent documentation, verification results, or tests
   that you have not performed.

CODE BLOCKS TO REVIEW:
{json.dumps(numbered_blocks, ensure_ascii=False)}

ARTICLE:
{json.dumps(article, ensure_ascii=False)}

Return ONLY a JSON object with this structure:

{{
  "approved": true,
  "code_reviews": [
    {{
      "id": 1,
      "language": "yaml",
      "actual_behavior": "Explain what the example actually does.",
      "semantics_verified": true,
      "issues": []
    }}
  ],
  "issues": [
    {{
      "severity": "major",
      "excerpt": "Exact problematic excerpt",
      "explanation": "Why it is incorrect or unsafe",
      "correction": "Specific correction"
    }}
  ]
}}

IMPORTANT:
SEVERITY RULES:
- Every issue must use exactly one of these lowercase severity values:
  "critical", "major", or "minor".
- Never return "critical|major|minor" as a literal value.
- Never use "high", "medium", "low", "warning", or "info".
- critical: Dangerous behavior, significant security exposure,
  destructive operations, or severe production reliability risks.
- major: Technically incorrect, misleading, or operationally
  unsafe guidance requiring correction before publication.
- minor: Noncritical improvements that do not materially affect
  technical correctness.
- Apply these rules to BOTH top-level issues and issues inside
  individual code_reviews.
- code_reviews must contain exactly one assessment per supplied block.
- Use semantics_verified=false when uncertain.
- An approved article must have no critical or major issues.
- Do not approve uncertain or unverified executable examples.
'''

    reviewer_system = (
        'You are a skeptical senior production reliability reviewer. '
        'Return valid JSON only. Every issue severity must be exactly '
        'critical, major, or minor.'
    )

    result = ask_llm(reviewer_system, prompt)

    def invalid_severities(response):
        if not isinstance(response, dict):
            return False

        issues = response.get('issues', [])
        reviews = response.get('code_reviews', [])

        if not isinstance(issues, list) or not isinstance(reviews, list):
            return False

        all_issues = list(issues)

        for review in reviews:
            if isinstance(review, dict) and isinstance(review.get('issues'), list):
                all_issues.extend(review['issues'])

        return any(
            isinstance(issue, dict)
            and issue.get('severity') not in {'critical', 'major', 'minor'}
            for issue in all_issues
        )

    if invalid_severities(result):
        print(
            'Reviewer returned invalid severity; retrying review once.',
            flush=True
        )

        result = ask_llm(
            reviewer_system,
            prompt + '\n\nRETRY INSTRUCTION: The previous review '
            'contained an invalid issue severity. Return a fresh, '
            'complete technical review. Use only critical, major, '
            'or minor for every issue severity. Assess every code block.'
        )

    if not isinstance(result, dict):
        raise ValueError('Reviewer response malformed')

    if type(result.get('approved')) is not bool:
        raise ValueError('Reviewer approval missing')

    if not isinstance(result.get('issues'), list):
        raise ValueError('Reviewer issues missing')

    reviews = result.get('code_reviews')

    if not isinstance(reviews, list):
        raise ValueError('Reviewer code assessments missing')

    expected_ids = set(range(1, len(code_blocks) + 1))
    received_ids = set()

    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError('Malformed code review')

        block_id = review.get('id')

        if type(block_id) is not int or block_id not in expected_ids:
            raise ValueError('Unexpected code review ID')

        if block_id in received_ids:
            raise ValueError('Duplicate code review ID')

        received_ids.add(block_id)

        if not isinstance(review.get('actual_behavior'), str) or not review['actual_behavior'].strip():
            raise ValueError('Missing code behavior explanation')

        if type(review.get('semantics_verified')) is not bool:
            raise ValueError('Missing code verification status')

        if not isinstance(review.get('issues'), list):
            raise ValueError('Malformed code review issues')

        if not review['semantics_verified']:
            result['approved'] = False
            result['issues'].append({
                'severity': 'major',
                'excerpt': f'Code block {block_id}',
                'explanation': 'Technical semantics could not be verified.',
                'correction': 'Correct or remove the uncertain example.'
            })

        for code_issue in review['issues']:
            if not isinstance(code_issue, dict):
                raise ValueError('Malformed code issue')

            if code_issue.get('severity') not in {
                'critical', 'major', 'minor'
            }:
                raise ValueError('Invalid code issue severity')

            result['issues'].append(code_issue)

    if received_ids != expected_ids:
        raise ValueError('Reviewer did not assess every code block')

    for issue in result['issues']:
        if not isinstance(issue, dict):
            raise ValueError('Malformed reviewer issue')

        if issue.get('severity') not in {
            'critical', 'major', 'minor'
        }:
            raise ValueError('Invalid reviewer issue severity')

    if any(
        issue['severity'] in {'critical', 'major'}
        for issue in result['issues']
    ):
        result['approved'] = False

    return result


def revise_article(item, article, review):
    prompt = f'''Revise this article to resolve every reviewer issue while preserving technical depth and the original topic.
Return JSON ONLY with exactly description, tags, body (Markdown without frontmatter or H1). Do not claim technical facts you cannot support. Remove or clearly mark questionable code as conceptual instead of inventing working syntax. Resolve each issue by correcting, simplifying or removing the flagged text; do not add new code blocks or commands, since new examples introduce new review issues. Keep 1500–2200 substantive words, 5–8 H2 sections, operational guidance, tradeoffs and a thoughtful conclusion.
{CODE_RULES}
Title: {item['title']}; category: {item['category']}.
REVIEW: {json.dumps(review, ensure_ascii=False)}
ARTICLE: {json.dumps(article, ensure_ascii=False)}'''
    return ask_llm('You are a senior SRE editor correcting specific technical errors. Return JSON only.', prompt)

def correct_validation_error(item, article, error):
    """Ask the model to correct a specific deterministic validation failure."""
    prompt = f'''Correct this technical article so it passes validation.

TITLE: {item['title']}
CATEGORY: {item['category']}

VALIDATION ERROR:
{str(error)}

MANDATORY INSTRUCTIONS:
- Fix the specific validation error.
- Preserve the article's technical accuracy and substantive content.
- If a Prometheus metric_relabel_configs example uses action: drop
  or action: keep, understand that these filter entire metric samples.
  They do NOT remove individual labels.
- If the intent is to remove a label, use action: labeldrop with an
  appropriate regex, and explain the risk of label-set collisions.
- Do not blindly replace drop/keep if sample filtering was intended.
  Remove uncertain examples instead.
- {CODE_RULES}
- Do not introduce fabricated configurations, unsupported claims,
  or new technical errors.
- Preserve 1500–2200 substantive words and at least five sections.
- Return ONLY JSON with description, tags, and body.

ARTICLE:
{json.dumps(article, ensure_ascii=False)}
'''
    return ask_llm(
        'You are a senior SRE editor correcting a specific technical '
        'validation failure. Return valid JSON only.',
        prompt
    )

def normalize_description(item, article):
    """Ensure article description is between 100 and 180 characters."""
    if not isinstance(article, dict):
        return article

    desc = article.get("description")

    if isinstance(desc, str) and 100 <= len(desc) <= 180:
        return article

    if not isinstance(desc, str):
        desc = ""

    desc = re.sub(r"\s+", " ", desc).strip()

    if len(desc) > 180:
        desc = desc[:177].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."

    if len(desc) < 100:
        fallback = (
            f"Explore {item['title'].lower()} with practical SRE "
            "engineering guidance, production considerations, "
            "implementation examples, and reliability trade-offs."
        )
        desc = fallback

    if len(desc) > 180:
        desc = desc[:177].rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."

    return {**article, "description": desc}

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
        # Normalize Markdown headings while preserving fenced code blocks.
    normalized_lines = []
    inside_fence = False
    fence_char = None
    fence_length = 0

    for line in body.splitlines():
        fence = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)

        if fence:
            marker = fence.group(1)

            if not inside_fence:
                inside_fence = True
                fence_char = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length:
                inside_fence = False

            normalized_lines.append(line)
            continue

        if not inside_fence:
            # Convert unexpected H1 headings to H2.
            line = re.sub(r'^#\s+', '## ', line)

        normalized_lines.append(line)

    body = '\n'.join(normalized_lines).strip()
    words = len(re.findall(r"\b[\w'-]+\b", body))
    if not 1200 <= words <= 2600:
        raise ValueError(f'Article length {words} outside 1200–2600')
        # Count section headings outside code fences.
    heading_count = 0
    inside_fence = False

    for line in body.splitlines():
        if re.match(r'^\s{0,3}(`{3,}|~{3,})', line):
            inside_fence = not inside_fence
            continue

        if not inside_fence and re.match(r'^#{2,3}\s+', line):
            heading_count += 1

    if heading_count < 5:
        raise ValueError(
            f'Insufficient section structure: found {heading_count} headings'
        )
    if re.search(r'(?i)(at paypal|our internal systems|in my team at paypal)', body):
        raise ValueError('Potential employer-specific reference')
    if re.search(r'(?i)\b(lorem ipsum|as an ai language model)\b', body):
        raise ValueError('Placeholder text')
    if re.search(r'(?m)^---\s*$', body):
        raise ValueError('Unexpected YAML separator in body')
    # Safety check for Prometheus metric relabeling examples.
    # Reject sample-dropping actions in metric_relabel_configs,
    # including unfenced examples and trailing YAML comments.
    #
    # This is deliberately conservative: drop/keep can be legitimate,
    # but generated examples require additional verification.

    lines = body.splitlines()

    in_metric_relabel = False
    relabel_indent = 0

    for line in lines:
        stripped = line.strip()

        if re.match(r'^metric_relabel_configs\s*:', stripped):
            in_metric_relabel = True
            relabel_indent = len(line) - len(line.lstrip())
            continue

        if not in_metric_relabel:
            continue

        # Exit when another YAML configuration key begins
        # at the same or a lower indentation level.
        indent = len(line) - len(line.lstrip())

        if (
            stripped
            and not stripped.startswith(('#', '-', '```', '~~~'))
            and indent <= relabel_indent
            and re.match(r'^[A-Za-z_][\w-]*\s*:', stripped)
        ):
            in_metric_relabel = False
            continue

        action = re.search(
            r'\baction\s*:\s*["\']?(drop|keep)["\']?\s*(?:#.*)?$',
            stripped,
            re.IGNORECASE,
        )

        if action:
            raise ValueError(
                'Unsafe Prometheus metric_relabel_configs example: '
                f'action={action.group(1)}. '
                'Sample-dropping actions require manual verification.'
            )
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

    # A topic that fails review is skipped, not fatal: the next unused topic
    # takes its place, so one difficult article cannot block the whole batch.
    published, failed = [], []
    max_failures = 6  # bounds API spend on a bad day
    for item in available:
        if len(published) == quantity or len(failed) == max_failures:
            break
        try:
            generate_article(item)
            published.append(item['slug'])
        except Exception as exc:
            failed.append(item['slug'])
            print(f'::warning::Skipping {item["slug"]}: {exc}', flush=True)

    print(f'Published: {published or "none"}', flush=True)
    print(f'Skipped (will be retried next run): {failed or "none"}', flush=True)
    if not published:
        sys.exit('No article passed validation and technical review')
    print('Generation complete; Astro build must pass before publishing.', flush=True)


def generate_article(item):
    """Draft, correct, review and write one article; raise if it cannot pass."""
    if item['category'] not in CATEGORIES:
        raise ValueError('Unknown category')
    for attempt in range(2):
        try:
            article = draft_article(item)
            for review_round in range(3):
                # Correct deterministic validation failures before review.
                for correction_round in range(2):
                    try:
                        article = normalize_description(item, article)
                        validated = validate(item, article)
                        break
                    except ValueError as validation_error:
                        if correction_round == 1:
                            raise
                        print(
                            f'Validation correction for {item["slug"]}: '
                            f'{validation_error}',
                            flush=True
                        )
                        article = correct_validation_error(
                            item,
                            article,
                            validation_error
                        )

                article = {
                    'description': validated[0],
                    'tags': validated[1],
                    'body': validated[2]
                }

                review = review_article(item, article)

                if review['approved']:
                    # Normalize metadata and perform final safety validation.
                    article = normalize_description(item, article)
                    validated = validate(item, article)
                    print(
                        f'Technical review passed for {item["slug"]} '
                        f'(round {review_round + 1})',
                        flush=True
                    )
                    write_article(item, validated)
                    return

                print(
                    f'Technical review rejected {item["slug"]}: '
                    f'{json.dumps(review["issues"], ensure_ascii=False)[:2000]}',
                    flush=True
                )

                if review_round == 2:
                    raise ValueError('Technical review failed after revision')

                article = revise_article(item, article, review)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as exc:
            print(f'Generation/review failed for {item["slug"]}, attempt {attempt+1}: {exc}', file=sys.stderr, flush=True)
            if attempt == 1:
                raise
            time.sleep(3)


if __name__ == '__main__':
    main()
