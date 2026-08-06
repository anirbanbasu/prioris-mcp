# tests/test_parsers_jats_xslt.py
import asyncio
import threading
import time

import pytest

from prioris_mcp.parsers.base import ParseError, ParserBackend
from prioris_mcp.parsers.jats_xslt import JatsXsltMarkdownBackend

_MINIMAL_JATS = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.2 20190208//EN"
  "JATS-journalpublishing1.dtd">
<article article-type="research-article">
  <front>
    <article-meta>
      <title-group><article-title>A Test Article</article-title></title-group>
    </article-meta>
  </front>
  <body>
    <p>Hello, world.</p>
  </body>
</article>
"""


class _RecordingHtmlBackend(ParserBackend):
    def __init__(self, markdown: str = "# A Test Article\\n\\nHello, world.") -> None:
        self.received_html: bytes | None = None
        self._markdown = markdown

    async def to_markdown(self, content: bytes) -> dict:
        self.received_html = content
        return {"markdown": self._markdown, "leaf_spans": [{"start": 0, "length": len(self._markdown)}]}


class TestJatsXsltMarkdownBackend:
    """JatsXsltMarkdownBackend.to_markdown: XSLT transform, delegation, and security bounds."""

    def test_transforms_jats_to_html_then_delegates_to_html_backend(self):
        html_backend = _RecordingHtmlBackend()

        async def scenario():
            backend = JatsXsltMarkdownBackend(html_backend)
            return await backend.to_markdown(_MINIMAL_JATS)

        result = asyncio.run(scenario())
        assert result == {
            "markdown": "# A Test Article\\n\\nHello, world.",
            "leaf_spans": [{"start": 0, "length": len("# A Test Article\\n\\nHello, world.")}],
        }
        assert html_backend.received_html is not None
        assert b"Hello, world." in html_backend.received_html

    def test_malformed_xml_raises_parse_error(self):
        async def scenario():
            backend = JatsXsltMarkdownBackend(_RecordingHtmlBackend())
            await backend.to_markdown(b"<article>not closed")

        with pytest.raises(ParseError):
            asyncio.run(scenario())

    def test_billion_laughs_style_entity_expansion_is_rejected_not_hung(self):
        malicious = b"""<?xml version="1.0"?>
<!DOCTYPE article [
  <!ENTITY a "a a a a a a a a a a">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
]>
<article><body><p>&c;</p></body></article>
"""

        async def scenario():
            backend = JatsXsltMarkdownBackend(_RecordingHtmlBackend())
            await backend.to_markdown(malicious)

        with pytest.raises(ParseError):
            asyncio.run(scenario())

    def test_slow_transform_raises_parse_error_within_bound(self, monkeypatch: pytest.MonkeyPatch):
        from prioris_mcp.parsers import jats_xslt

        monkeypatch.setattr(jats_xslt, "JATS_PARSE_TIMEOUT_SECONDS", 0.01)

        async def scenario():
            backend = JatsXsltMarkdownBackend(_RecordingHtmlBackend())
            await backend.to_markdown(_MINIMAL_JATS)

        # Not guaranteed to trip the timeout on a fast machine for a tiny document, but must
        # never raise anything other than ParseError if it does.
        try:
            asyncio.run(scenario())
        except ParseError:
            pass

    def test_slow_transform_deterministically_raises_parse_error(self, monkeypatch: pytest.MonkeyPatch):
        """A transform that genuinely blocks past the bound must raise ParseError at that bound.

        Unlike the lenient smoke test above, this stubs out the transform step itself with a
        deliberate `time.sleep()` well past a tiny timeout, so the `anyio.fail_after` +
        `to_thread.run_sync(..., abandon_on_cancel=True)` timeout path is deterministically
        exercised rather than merely possible.
        """
        from prioris_mcp.parsers import jats_xslt

        monkeypatch.setattr(jats_xslt, "JATS_PARSE_TIMEOUT_SECONDS", 0.05)

        backend = JatsXsltMarkdownBackend(_RecordingHtmlBackend())

        def _slow_transform(source_doc):
            time.sleep(0.5)
            return source_doc

        monkeypatch.setattr(backend, "_get_transform", lambda: _slow_transform)

        async def scenario():
            await backend.to_markdown(_MINIMAL_JATS)

        start = time.monotonic()
        with pytest.raises(ParseError):
            asyncio.run(scenario())
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, (
            f"expected ParseError near the {jats_xslt.JATS_PARSE_TIMEOUT_SECONDS}s bound, "
            f"not after waiting out the full 0.5s sleep (took {elapsed:.3f}s)"
        )

    def test_concurrent_transforms_are_bounded_by_the_semaphore(self, monkeypatch: pytest.MonkeyPatch):
        """No more than the configured cap ever run the transform step at the same time.

        Regression guard for the abandoned-thread resource-exhaustion risk described in
        docs/requirement-specification/05-security.md#a-bounded-per-call-failure-is-not-sufficient-on-its-own:
        an `anyio.CapacityLimiter` passed to `to_thread.run_sync` would release its slot as soon as
        the *caller* is cancelled, not when the worker thread actually finishes - this test proves
        the module's plain `threading.Semaphore` genuinely caps concurrent execution instead.
        """
        from prioris_mcp.parsers import jats_xslt

        monkeypatch.setattr(jats_xslt, "_TRANSFORM_SEMAPHORE", threading.Semaphore(1))

        state_lock = threading.Lock()
        concurrent_count = 0
        max_concurrent = 0

        def _slow_transform(source_doc):
            nonlocal concurrent_count, max_concurrent
            with state_lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)
            time.sleep(0.2)
            with state_lock:
                concurrent_count -= 1
            return source_doc

        backend_a = JatsXsltMarkdownBackend(_RecordingHtmlBackend())
        backend_b = JatsXsltMarkdownBackend(_RecordingHtmlBackend())
        monkeypatch.setattr(backend_a, "_get_transform", lambda: _slow_transform)
        monkeypatch.setattr(backend_b, "_get_transform", lambda: _slow_transform)

        async def scenario():
            await asyncio.gather(
                backend_a.to_markdown(_MINIMAL_JATS),
                backend_b.to_markdown(_MINIMAL_JATS),
            )

        asyncio.run(scenario())
        assert max_concurrent == 1


class TestToMarkdownReturnsLeafSpans:
    """Tests for leaf_spans in JatsXsltMarkdownBackend.to_markdown result."""

    def test_returns_whatever_the_injected_html_backend_returns(self):
        async def scenario():
            html_backend = _RecordingHtmlBackend(markdown="# Converted")
            backend = JatsXsltMarkdownBackend(html_backend)
            result = await backend.to_markdown(_MINIMAL_JATS)
            assert result == {"markdown": "# Converted", "leaf_spans": [{"start": 0, "length": len("# Converted")}]}

        asyncio.run(scenario())
