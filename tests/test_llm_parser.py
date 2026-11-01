import unittest

from pv.llm.client import parse_json


class ModelReplyParsing(unittest.TestCase):
    def test_empty_reply_returns_empty_object(self):
        self.assertEqual(parse_json(""), {})

    def test_fenced_json_reply_is_parsed(self):
        self.assertEqual(parse_json('```json\n{"ok": true}\n```'), {"ok": True})
