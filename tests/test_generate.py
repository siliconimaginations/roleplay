"""Unit tests for roleplay.generate module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from roleplay.generate import _strip_fences, generate_yaml_scenario


class TestStripFences:
    def test_strips_yaml_fence(self) -> None:
        assert _strip_fences("```yaml\nfoo: bar\n```") == "foo: bar"

    def test_strips_plain_fence(self) -> None:
        assert _strip_fences("```\nfoo: bar\n```") == "foo: bar"

    def test_no_fence_unchanged(self) -> None:
        assert _strip_fences("foo: bar") == "foo: bar"

    def test_partial_fence_unchanged(self) -> None:
        # Only opening fence — not a full match, returned as-is
        result = _strip_fences("```yaml\nfoo: bar")
        assert "foo: bar" in result


class TestGenerateYamlScenario:
    def _make_provider(self, text: str) -> MagicMock:
        from roleplay.providers.base import CompletionResponse

        p = MagicMock()
        p.complete = AsyncMock(return_value=CompletionResponse(text=text))
        return p

    @pytest.mark.asyncio
    async def test_returns_stripped_yaml(self) -> None:
        raw = "```yaml\nsession_id: test\n```"
        provider = self._make_provider(raw)
        result = await generate_yaml_scenario("two people talk", provider)
        assert result == "session_id: test"

    @pytest.mark.asyncio
    async def test_passes_prompt_to_provider(self) -> None:
        provider = self._make_provider("session_id: x\n")
        await generate_yaml_scenario("my prompt", provider)
        call_args = provider.complete.call_args[0][0]
        assert "my prompt" in call_args.prompt

    @pytest.mark.asyncio
    async def test_provider_error_propagates(self) -> None:
        from roleplay.providers.base import ProviderError

        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=ProviderError("boom"))
        with pytest.raises(ProviderError, match="boom"):
            await generate_yaml_scenario("test", provider)


class TestFixCyclesModelValidation:
    """fix_cycles must catch deprecated models via semantic validation."""

    _MINIMAL_YAML = """\
parties:
  - id: alice
    kind: person
    name: Alice
  - id: office
    kind: environment
    name: Office
config:
  default_provider: gemini
  default_model: gemini-1.5-pro
"""

    _FIXED_YAML = """\
parties:
  - id: alice
    kind: person
    name: Alice
  - id: office
    kind: environment
    name: Office
config:
  default_provider: gemini
  default_model: gemini-2.5-flash
"""

    def _make_provider(self, *texts: str) -> MagicMock:
        from roleplay.providers.base import CompletionResponse

        p = MagicMock()
        p.complete = AsyncMock(
            side_effect=[CompletionResponse(text=t) for t in texts]
        )
        return p

    @pytest.mark.asyncio
    async def test_deprecated_model_triggers_correction(self) -> None:
        """First response has gemini-1.5-pro; fix cycle should request a correction."""
        provider = self._make_provider(self._MINIMAL_YAML, self._FIXED_YAML)
        result = await generate_yaml_scenario("test", provider, fix_cycles=1)
        # Provider must have been called twice (initial + 1 fix)
        assert provider.complete.call_count == 2
        # The correction prompt must mention the deprecated model error
        correction_prompt = provider.complete.call_args_list[1][0][0].prompt
        assert "gemini-1.5-pro" in correction_prompt or "deprecated" in correction_prompt.lower() or "shut down" in correction_prompt.lower()
        # Final result is the fixed YAML
        assert "gemini-2.5-flash" in result

    @pytest.mark.asyncio
    async def test_no_fix_cycles_leaves_deprecated_model(self) -> None:
        """With fix_cycles=0 the deprecated model is returned as-is."""
        provider = self._make_provider(self._MINIMAL_YAML)
        result = await generate_yaml_scenario("test", provider, fix_cycles=0)
        assert provider.complete.call_count == 1
        assert "gemini-1.5-pro" in result
