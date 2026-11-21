import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from analysis.wave_analyst import MODEL, run


class GeminiWaveRun(unittest.TestCase):
    def test_scoped_requests_and_distinct_reader_counts(self):
        calls = []
        answer = {"themes": [{"theme": "Subscription cue", "ids": ["c01", "c02"]}]}

        def respond(request, timeout):
            calls.append(json.loads(request.data))
            result = {"modelVersion": MODEL, "candidates": [{"finishReason": "STOP",
                      "content": {"parts": [{"text": json.dumps(answer)}]}}]}
            return io.BytesIO(json.dumps(result).encode())

        with TemporaryDirectory() as directory, patch("analysis.wave_analyst.urlopen", respond):
            path = Path(directory) / "scored.csv"
            path.write_text("respondent,position,comment\nR17,1,Subscription cue\n"
                            "R17,2,Show subscription to me@example.com\n")
            record = run(path, "test-key")
            self.assertEqual(len(calls), 2)
            for body in calls:
                for excluded in ("R17", "me@example.com", "test-key"):
                    self.assertNotIn(excluded, json.dumps(body))
            self.assertEqual(record["comment_findings"][0]["support_count"], 1)
