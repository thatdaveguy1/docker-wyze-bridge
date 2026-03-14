#!/usr/bin/env python3

import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))

from wyzebridge.wyze_api import WyzeApi


class TestThumbnail404Logging(unittest.TestCase):
    def test_thumbnail_404_logs_warning_not_error(self):
        api = WyzeApi()
        response = Mock(status_code=404)
        error = requests.HTTPError("404 Client Error", response=response)

        with (
            patch.object(
                WyzeApi,
                "get_thumbnail",
                return_value="https://example.test/thumb.jpg?X-Amz-Date=1",
            ),
            patch("wyzebridge.wyze_api.get", side_effect=error),
            patch("wyzebridge.wyze_api.logger.warning") as warning,
            patch("wyzebridge.wyze_api.logger.error") as err,
        ):
            result = api.save_thumbnail("hamster", "")

        self.assertFalse(result)
        warning.assert_called_once()
        err.assert_not_called()
        warning_message = warning.call_args.args[0]
        self.assertIn("Thumbnail unavailable for hamster", warning_message)
        self.assertIn("https://example.test/thumb.jpg", warning_message)
        self.assertNotIn("X-Amz-Date=1", warning_message)
        self.assertNotIn("404 Client Error", warning_message)


if __name__ == "__main__":
    unittest.main()
