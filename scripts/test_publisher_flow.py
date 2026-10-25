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


if __name__ == "__main__":
    unittest.main()
