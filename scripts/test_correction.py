import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, "scripts")
import publish


class TestCorrectionFlow(unittest.TestCase):

    def test_unsafe_config_is_corrected_before_review(self):
        item = {
            "title": "Test Article",
            "category": "Observability",
        }

        unsafe = {
            "description": "A technical guide to Prometheus metrics, cardinality controls, configuration safety, and operational verification practices.",
            "tags": ["prometheus", "observability", "metrics"],
            "body": (
                "## Introduction\n\n"
                + "Reliability engineering requires careful validation. " * 300
                + "\n\n## Architecture\n\n"
                + "## Failure Modes\n\n"
                + "## Verification\n\n"
                + "## Conclusion\n\n"
                + "```yaml\n"
                + "metric_relabel_configs:\n"
                + "  - action: drop\n"
                + "    regex: .*\n"
                + "```\n"
            ),
        }

        corrected = {
            **unsafe,
            "body": unsafe["body"].replace(
                "action: drop",
                "action: labeldrop"
            ),
        }

        with patch.object(
            publish,
            "correct_validation_error",
            return_value=corrected
        ) as correction:

            article = unsafe

            for attempt in range(2):
                try:
                    validated = publish.validate(item, article)
                    break
                except ValueError as error:
                    if attempt == 1:
                        raise

                    article = publish.correct_validation_error(
                        item, article, error
                    )

            self.assertEqual(correction.call_count, 1)
            self.assertIn("action: labeldrop", validated[2])

            print("PASS: Unsafe configuration rejected")
            print("PASS: Correction function invoked")
            print("PASS: Corrected article passed validation")


if __name__ == "__main__":
    unittest.main()