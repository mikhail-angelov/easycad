from __future__ import annotations

import asyncio
import io
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

import app.ai_generation as ai
from app.ai_generation import GenerationError, validate_image_upload


def png_bytes(size=(10, 10)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, format="PNG")
    return output.getvalue()


class FakeProviderClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(400, text='{"error":{"message":"private upstream detail"}}', request=request)


class FakeProviderChoiceErrorClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "finish_reason": "error",
                    "error": {"metadata": {"error_type": "rate_limit_exceeded"}},
                    "message": {"content": '{"partial": '},
                }],
            },
            request=request,
        )


class InputSecurityTests(unittest.TestCase):
    def test_rejects_corrupt_image(self):
        with self.assertRaisesRegex(Exception, "Invalid image"):
            validate_image_upload(b"not-an-image", "bad.png", "image/png")

    def test_rejects_mime_content_mismatch(self):
        with self.assertRaisesRegex(Exception, "does not match MIME"):
            validate_image_upload(png_bytes(), "image.jpg", "image/jpeg")

    def test_rejects_dimension_limit(self):
        with self.assertRaisesRegex(Exception, "dimensions are too large"):
            validate_image_upload(png_bytes((ai.MAX_IMAGE_DIMENSION + 1, 1)), "wide.png", "image/png")

    def test_rejects_decompression_bomb_warning(self):
        with patch.object(Image, "MAX_IMAGE_PIXELS", 10):
            with self.assertRaisesRegex(Exception, "Invalid image"):
                validate_image_upload(png_bytes((10, 10)), "bomb.png", "image/png")

    def test_provider_error_body_is_not_returned_in_generation_error(self):
        with patch.object(ai.httpx, "AsyncClient", return_value=FakeProviderClient()):
            with self.assertRaises(GenerationError) as raised:
                asyncio.run(ai._chat_json("https://provider.invalid", "key", {"messages": []}, "test"))

        self.assertEqual(str(raised.exception), "Provider returned HTTP 400")
        self.assertEqual(raised.exception.detail, {"status_code": 400})
        self.assertNotIn("private upstream detail", str(raised.exception.detail))

    def test_choice_error_never_becomes_a_partial_json_object(self):
        with patch.object(ai.httpx, "AsyncClient", return_value=FakeProviderChoiceErrorClient()):
            with self.assertRaises(GenerationError) as raised:
                asyncio.run(ai._chat_json("https://provider.invalid", "key", {"messages": []}, "vision_analysis"))

        self.assertEqual(str(raised.exception), "Provider temporarily rate-limited the request. Please try again.")
        self.assertEqual(raised.exception.detail, {"provider_error": "rate_limit_exceeded"})


if __name__ == "__main__":
    unittest.main()
