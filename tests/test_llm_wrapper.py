"""Test llm_wrapper.py — LM Studio client, image encoding, vision support."""
import json
import os
import pytest
from unittest.mock import MagicMock, patch, mock_open

from llm_wrapper import LMStudioWrapper


class TestLMStudioWrapperInit:
    def test_default_init(self):
        import config as _cfg
        wrapper = LMStudioWrapper()
        assert wrapper.model == _cfg.LLM_MODEL
        assert wrapper.base_url == "http://localhost:1234/v1"
        assert wrapper.timeout == 120

    def test_custom_init(self):
        wrapper = LMStudioWrapper(base_url="http://test:5000/v1", timeout=30, model="qwen/test")
        assert wrapper.model == "qwen/test"
        assert wrapper.base_url == "http://test:5000/v1"
        assert wrapper.timeout == 30

    def test_set_model(self):
        wrapper = LMStudioWrapper()
        wrapper.set_model("new-model")
        assert wrapper.model == "new-model"


class TestImageFormats:
    def test_image_formats(self):
        formats = LMStudioWrapper.IMAGE_FORMATS
        assert "qwen" in formats
        assert "gpt" in formats
        assert "llava" in formats


class TestEncodeImage:
    def test_encode_image(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b'test data')
        result = LMStudioWrapper.encode_image(str(img))
        assert isinstance(result, str)
        assert len(result) > 0


class TestImageURL:
    def test_returns_data_url(self):
        result = LMStudioWrapper._image_url("dGVzdA==")
        assert "data:image/png;base64,dGVzdA==" in result

    def test_image_url_from_dict(self):
        result = LMStudioWrapper._image_url({"b64": "BBBB", "mime": "png"})
        assert "base64,BBBB" in result
        assert "image/png" in result

    def test_image_url_converts_webp(self):
        result = LMStudioWrapper._image_url({"b64": "CCCC", "mime": "webp"})
        assert "image/png" in result
        assert "webp" not in result


class TestImagePart:
    def test_image_part_returns_dict(self):
        result = LMStudioWrapper._image_part("AAAA", "qwen/test")
        assert result["type"] == "image_url"
        assert "image_url" in result
        assert "url" in result["image_url"]


class TestToMessages:
    def test_to_messages_with_prompt(self):
        wrapper = LMStudioWrapper()
        result = wrapper._to_messages(prompt="Hello")
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_to_messages_with_images(self):
        wrapper = LMStudioWrapper()
        images = [{"b64": "AAAA", "mime": "png"}]
        result = wrapper._to_messages(prompt="Describe", images=images)
        assert len(result) == 1
        content = result[0]["content"]
        assert isinstance(content, list)
        assert len(content) >= 2

    def test_to_messages_empty(self):
        wrapper = LMStudioWrapper()
        assert wrapper._to_messages() == []

    def test_to_messages_only_images(self):
        wrapper = LMStudioWrapper()
        result = wrapper._to_messages(images=[{"b64": "AAAA", "mime": "png"}])
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert isinstance(result[0]["content"], list)
        assert result[0]["content"][-1]["type"] == "text"


class TestCompressMessages:
    def test_compress_string_content(self):
        wrapper = LMStudioWrapper()
        messages = [{"role": "user", "content": "  Hello   World  "}]
        result = wrapper._compress_messages(messages)
        assert result[0]["content"] == "Hello World"

    def test_compress_content_list(self):
        wrapper = LMStudioWrapper()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "  Hello   World  "},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
        ]}]
        result = wrapper._compress_messages(messages)
        text_part = result[0]["content"][0]
        assert text_part["text"] == "Hello World"
        img_part = result[0]["content"][1]
        assert img_part["type"] == "image_url"


class TestVisionKeywords:
    def test_supports_vision_qwen(self):
        assert LMStudioWrapper._supports_vision("qwen/qwen3.5-9b") is True

    def test_supports_vision_gemma(self):
        assert LMStudioWrapper._supports_vision("gemma-4-26b") is True

    def test_supports_vision_gpt(self):
        assert LMStudioWrapper._supports_vision("gpt-4-vision") is True

    def test_does_not_support_vision_deepseek(self):
        assert LMStudioWrapper._supports_vision("deepseek-chat-v3") is False

    def test_does_not_support_vision_none(self):
        assert LMStudioWrapper._supports_vision(None) is False
        assert LMStudioWrapper._supports_vision("") is False

    def test_vision_keywords_not_empty(self):
        assert len(LMStudioWrapper.VISION_KEYWORDS) > 0
        assert "qwen" in LMStudioWrapper.VISION_KEYWORDS
        assert "gemma" in LMStudioWrapper.VISION_KEYWORDS

    def test_images_skipped_for_non_vision_model(self):
        wrapper = LMStudioWrapper(model="deepseek-chat-v3")
        result = wrapper._to_messages(prompt="Hello", images=[{"b64": "AAAA", "mime": "png"}])
        assert len(result) == 1
        assert isinstance(result[0]["content"], str)
        assert result[0]["content"] == "Hello"

    def test_encode_image_size_limit(self, tmp_path):
        import config
        img = tmp_path / "huge.png"
        img.write_bytes(b'x' * (config.MAX_IMAGE_SIZE + 1))
        with pytest.raises(ValueError, match="Image too large"):
            LMStudioWrapper.encode_image(str(img))
