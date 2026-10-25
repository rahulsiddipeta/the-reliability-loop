import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, "scripts")
import publish


ITEM = {"slug": "test-article", "title": "Test Article", "category": "Observability"}

GOOD = {
    "description": "A technical guide to Prometheus metrics, cardinality controls, configuration safety, and operational verification practices.",
    "tags": ["prometheus", "observability", "metrics"],
    "body": (
        "## Introduction\n\n"
        + "Reliability engineering requires careful validation. " * 300
        + "\n\n## Architecture\n\n## Failure Modes\n\n## Verification\n\n## Conclusion\n"
    ),
}

APPROVED = {"approved": True, "code_reviews": [], "issues": []}


def plan(n):
    return [{"slug": f"topic-{i}", "title": f"Topic {i}", "category": "SRE"} for i in range(n)]


class TestMainFlow(unittest.TestCase):

    def run_main(self, batch, generate):
        with patch.object(publish, "PLAN", plan(10)), \
             patch.object(publish, "existing_articles", return_value=[None] * 5), \
             patch.object(publish, "slug_used", return_value=False), \
             patch.object(publish, "generate_article", side_effect=generate) as gen, \
             patch.object(sys, "argv", ["publish.py", "--batch", str(batch)]), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            publish.main()
        return [c.args[0]["slug"] for c in gen.call_args_list]

    def test_failed_topic_is_skipped_and_batch_is_filled(self):
        def generate(item):
            if item["slug"] in {"topic-0", "topic-3"}:
                raise ValueError("Technical review failed after revision")

        tried = self.run_main(6, generate)
        self.assertEqual(tried, [f"topic-{i}" for i in range(8)])

    def test_all_failures_exit_nonzero(self):
        def generate(item):
            raise ValueError("rejected")

        with self.assertRaises(SystemExit) as ctx:
            self.run_main(1, generate)
        self.assertIn("No article passed", str(ctx.exception))

    def test_failures_are_capped(self):
        def generate(item):
            raise ValueError("rejected")

        with patch.object(publish, "generate_article", side_effect=generate) as gen:
            with self.assertRaises(SystemExit):
                self.run_main(6, gen)
        self.assertEqual(gen.call_count, 6)

    def test_stops_at_25_articles(self):
        with patch.object(publish, "existing_articles", return_value=[None] * 25), \
             patch.object(publish, "generate_article") as gen, \
             patch.object(sys, "argv", ["publish.py", "--batch", "6"]), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "test"}):
            publish.main()
        gen.assert_not_called()


class TestGenerateArticle(unittest.TestCase):

    def test_rejected_article_is_revised_then_written(self):
        rejected = {"approved": False, "code_reviews": [],
                    "issues": [{"severity": "major", "excerpt": "x", "explanation": "y", "correction": "z"}]}
        with patch.object(publish, "draft_article", return_value=GOOD), \
             patch.object(publish, "review_article", side_effect=[rejected, APPROVED]), \
             patch.object(publish, "revise_article", return_value=GOOD) as revise, \
             patch.object(publish, "write_article") as write:
            publish.generate_article(ITEM)
        self.assertEqual(revise.call_count, 1)
        self.assertEqual(write.call_count, 1)

    def test_never_approved_is_never_written(self):
        rejected = {"approved": False, "code_reviews": [],
                    "issues": [{"severity": "major", "excerpt": "x", "explanation": "y", "correction": "z"}]}
        with patch.object(publish, "draft_article", return_value=GOOD), \
             patch.object(publish, "review_article", return_value=rejected), \
             patch.object(publish, "revise_article", return_value=GOOD), \
             patch.object(publish, "write_article") as write, \
             patch.object(publish.time, "sleep"):
            with self.assertRaises(ValueError):
                publish.generate_article(ITEM)
        write.assert_not_called()


class TestReviewerRetry(unittest.TestCase):

    def bad(self):
        return {"approved": True, "code_reviews": [],
                "issues": [{"severity": "critical|major|minor", "excerpt": "x", "explanation": "y", "correction": "z"}]}

    def test_invalid_severity_retries_once(self):
        with patch.object(publish, "ask_llm", side_effect=[self.bad(), APPROVED]) as ask:
            result = publish.review_article(ITEM, GOOD)
        self.assertEqual(ask.call_count, 2)
        self.assertTrue(result["approved"])

    def test_two_invalid_reviews_fail_closed(self):
        with patch.object(publish, "ask_llm", side_effect=[self.bad(), self.bad()]) as ask:
            with self.assertRaises(ValueError):
                publish.review_article(ITEM, GOOD)
        self.assertEqual(ask.call_count, 2)


class TestModelAndRevision(unittest.TestCase):

    def test_unavailable_model_falls_back_to_mini(self):
        import urllib.error
        unavailable = urllib.error.HTTPError("u", 404, "model_not_found", {}, None)
        with patch.object(publish, "MODEL", "gpt-5"), \
             patch.object(publish, "_ask_llm", side_effect=[unavailable, {"ok": True}]) as ask:
            self.assertEqual(publish.ask_llm("s", "u"), {"ok": True})
            self.assertEqual(publish.MODEL, publish.FALLBACK_MODEL)
        self.assertEqual(ask.call_count, 2)

    def test_last_revision_removes_flagged_code(self):
        rejected = {"approved": False, "code_reviews": [],
                    "issues": [{"severity": "major", "excerpt": "x", "explanation": "y", "correction": "z"}]}
        with patch.object(publish, "draft_article", return_value=GOOD), \
             patch.object(publish, "review_article", side_effect=[rejected, rejected, APPROVED]), \
             patch.object(publish, "revise_article", return_value=GOOD) as revise, \
             patch.object(publish, "write_article") as write:
            publish.generate_article(ITEM)
        self.assertEqual([c.kwargs["final"] for c in revise.call_args_list], [False, True])
        write.assert_called_once()

    def test_final_revision_prompt_deletes_code(self):
        with patch.object(publish, "ask_llm", return_value={}) as ask:
            publish.revise_article(ITEM, GOOD, {}, final=True)
        self.assertIn("delete every code block", ask.call_args.args[1])


class TestDescription(unittest.TestCase):

    def test_long_description_is_rewritten_not_truncated(self):
        good = "Readiness probes control traffic routing, not health; how they work, how they fail, and how to design them for production."
        with patch.object(publish, "ask_llm", return_value={"description": good}):
            result = publish.normalize_description(ITEM, {**GOOD, "description": "x " * 150})
        self.assertEqual(result["description"], good)

    def test_failed_rewrite_falls_back_to_truncation(self):
        with patch.object(publish, "ask_llm", side_effect=ValueError("boom")):
            result = publish.normalize_description(ITEM, {**GOOD, "description": "word " * 60})
        self.assertTrue(100 <= len(result["description"]) <= 180)
        self.assertTrue(result["description"].endswith("..."))


if __name__ == "__main__":
    unittest.main()
