from prioris_mcp.storage.chunking import detect_chunks


class TestDetectChunksBasics:
    """Test basic chunk detection: no headings, single heading, sibling headings."""

    def test_no_headings_returns_empty_list(self):
        assert detect_chunks("Just some plain text.\n\nNo headings here.") == []

    def test_single_top_level_heading_spans_to_end(self):
        markdown = "# Introduction\n\nSome text."
        chunks = detect_chunks(markdown)
        assert len(chunks) == 1
        assert chunks[0]["key"] == "Introduction"
        assert chunks[0]["start"] == 0
        assert chunks[0]["length"] == len(markdown)

    def test_two_sibling_headings_each_span_to_the_next(self):
        markdown = "# Introduction\n\nText A.\n\n# Methods\n\nText B."
        chunks = detect_chunks(markdown)
        assert [c["key"] for c in chunks] == ["Introduction", "Methods"]
        assert chunks[0]["start"] == 0
        assert chunks[0]["length"] == markdown.index("# Methods")
        assert chunks[1]["start"] == markdown.index("# Methods")
        assert chunks[1]["length"] == len(markdown) - markdown.index("# Methods")


class TestDetectChunksNesting:
    """Test nested heading behavior: subsections overlapping parent sections."""

    def test_subsection_overlaps_its_enclosing_section(self):
        markdown = "# Section\n\nIntro text.\n\n## Subsection\n\nDetail text.\n\n# Next Section\n\nMore."
        chunks = detect_chunks(markdown)
        assert [c["key"] for c in chunks] == ["Section", "Subsection", "Next Section"]
        next_section_start = markdown.index("# Next Section")
        subsection_start = markdown.index("## Subsection")
        assert chunks[0]["start"] == 0
        assert chunks[0]["length"] == next_section_start  # Section spans past its own subsection
        assert chunks[1]["start"] == subsection_start
        assert (
            chunks[1]["length"] == next_section_start - subsection_start
        )  # Subsection stops at the next sibling-or-shallower heading
        assert chunks[2]["start"] == next_section_start

    def test_deeper_heading_does_not_close_a_shallower_one_early(self):
        markdown = "# A\n\n## B\n\n### C\n\ntext\n\n## D\n\ntext"
        chunks = detect_chunks(markdown)
        a_chunk = next(c for c in chunks if c["key"] == "A")
        assert a_chunk["start"] == 0
        assert a_chunk["length"] == len(markdown)  # nothing shallower-or-equal after it


class TestDetectChunksFencedCodeBlocks:
    """Test fenced code block handling: headings inside fences should not be detected."""

    def test_hash_inside_fenced_code_block_is_not_a_heading(self):
        markdown = "# Real Heading\n\n```python\n# not a heading, just a comment\nx = 1\n```\n\nAfter code."
        chunks = detect_chunks(markdown)
        assert [c["key"] for c in chunks] == ["Real Heading"]

    def test_heading_after_a_closed_fence_is_still_detected(self):
        markdown = "```\n# inside fence\n```\n\n# Outside Fence"
        chunks = detect_chunks(markdown)
        assert [c["key"] for c in chunks] == ["Outside Fence"]


class TestDetectChunksHeadingSyntax:
    """Test ATX heading syntax: space requirement, hash levels, text stripping."""

    def test_requires_space_after_hashes(self):
        assert detect_chunks("#NotAHeading\n\ntext") == []

    def test_heading_text_is_stripped_of_leading_hashes_and_whitespace(self):
        chunks = detect_chunks("##   Spacey Heading   \n\ntext")
        assert chunks[0]["key"] == "Spacey Heading"

    def test_up_to_six_hash_levels_recognised(self):
        markdown = "###### Deepest\n\ntext"
        chunks = detect_chunks(markdown)
        assert chunks[0]["key"] == "Deepest"

    def test_seven_hashes_is_not_a_heading(self):
        assert detect_chunks("####### TooDeep\n\ntext") == []
