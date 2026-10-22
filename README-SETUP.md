# The Reliability Loop — autonomous publisher

## Install
Copy `.github/workflows/publish-articles.yml`, `scripts/publish.py`, and `scripts/editorial_plan.json` into the matching paths in your EXISTING `the-reliability-loop` GitHub repository. Do not replace your Astro project, article layout, Cloudflare Worker, or D1 files.

## Required GitHub configuration
1. Repository → Settings → Secrets and variables → Actions → New repository secret: `OPENAI_API_KEY` (your OpenAI API platform key; never commit it).
2. Optional repository variable `OPENAI_MODEL` (default `gpt-4.1`). Verify model access and pricing in your API account.
3. Repository → Settings → Actions → General → Workflow permissions: **Read and write permissions**.
4. Confirm `main` accepts pushes from GitHub Actions. Branch protection/rulesets can block automatic commits.
5. Confirm Cloudflare's existing GitHub integration deploys commits to `main`. Some CI integrations do not trigger downstream GitHub Actions on `GITHUB_TOKEN` commits, but Cloudflare's direct Git integration may still deploy; verify on the first run.
6. Verify your OpenAI API account has billing and a sensible project spending limit. ChatGPT subscriptions do not include API usage.

## Launch
Commit the three files to `main`, open GitHub → Actions → **Autonomous article publishing** → Run workflow → batch **6**. The workflow generates six files, validates the Astro build, commits to `main`, and should trigger the existing Cloudflare deployment.

## Weekly
GitHub's scheduler runs every Wednesday at 18:00 UTC (11 AM PDT / 10 AM PST). It generates one article until there are 25 published `.md`/`.mdx` articles under `src/content/articles`. GitHub schedules can run late or be skipped under load. Scheduled workflows on public repos can be disabled after prolonged inactivity.

## Safety and limitations
- This is fully automatic. AI content can contain inaccuracies even if structure/build checks pass. Strongly recommended: inspect the first six and consider human review for any production-grade technical claims.
- Diagrams are original, deterministic SVG conceptual control-loop illustrations. They are intentionally reusable and generic, not article-specific technical architecture illustrations. Improve this later with topic-aware diagrams if desired.
- If generation or build fails, the job fails without pushing the newly generated articles. Re-run after resolving the issue.
- The script uses an editorial roadmap, avoiding duplicate planned slugs. It does not automatically discover other unpublished topical overlaps.
- The stop condition counts existing non-draft Markdown/MDX files, not remote website availability.
- Auto-generated articles are published on the day the workflow runs, not scheduled future dates.
- Comments, likes, views, D1, and Turnstile are unaffected.
- This pipeline does not perform live web research, independent fact verification, image generation via image models, or plagiarism checking. Do not treat the automated checks as a guarantee of accuracy.
