"""Deterministic page-text extraction: comments, scripts, styles, tags,
entities, zero-width characters, whitespace, size limits, edge documents."""

from tests.direct.flightattest_support import new_contract


def page_text(raw: str) -> str:
    module, _c = new_contract()
    return module._page_text(raw)


def retry_blocks(raw: str) -> str:
    module, _c = new_contract()
    return module._remove_blocks(raw, module._COMMENT_START, module._COMMENT_END)


def test_plain_text_passthrough_normalized():
    assert page_text("  hello   world \n again ") == "hello world again"


def test_tags_replaced_by_spaces():
    assert page_text("<p>hello</p><p>world</p>") == "hello world"


def test_script_block_removed():
    assert page_text("<p>a</p><script>var x = 1 < 2;</script><p>b</p>") == "a b"


def test_script_with_attributes_and_case_removed():
    assert page_text("a<SCRIPT type='text/javascript'>evil()</SCRIPT>b") == "ab"


def test_style_block_removed():
    assert page_text("a<style>.x { color: red; }</style>b") == "ab"


def test_comments_removed():
    assert page_text("a<!-- hidden <b>note</b> -->b") == "ab"


def test_unterminated_script_swallows_rest():
    assert page_text("a<script>var x = 'never closed'; b") == "a"


def test_unterminated_comment_swallows_rest():
    assert page_text("hello<!-- never closed world") == "hello"


def test_unterminated_tag_drops_rest():
    assert page_text("alpha <div class=") == "alpha"


def test_entities_decoded():
    assert page_text("fish &amp; chips &lt;ok&gt; &mdash; done") == "fish & chips <ok> — done"


def test_zero_width_characters_removed():
    module, _c = new_contract()
    zw = module.ZERO_WIDTH
    raw = "ca" + zw[0] + "ncelled" + zw[2]
    assert page_text(raw) == "cancelled"


def test_nested_like_markup():
    assert page_text("<div><span><b>deep</b></span></div>") == "deep"


def test_script_inside_comment_gone():
    raw = "a<!-- <script>alert(1)</script> -->b"
    assert page_text(raw) == "ab"


def test_full_document_happy_path():
    module, _c = new_contract()
    raw = (
        "<!doctype html><html><head><title>T</title>"
        "<script>ignore()</script></head><body>"
        "<h1>Flight ST221</h1><p>Cancelled due to weather</p>"
        "</body></html>"
    )
    # The documented procedure keeps <title> text: it is naive on purpose.
    assert module._page_text(raw) == "T Flight ST221 Cancelled due to weather"


def test_empty_document():
    assert page_text("") == ""


def test_only_markup():
    assert page_text("<div><span></span></div>") == ""


def test_multiple_scripts_and_styles():
    raw = "a<script>1</script>b<style>2</style>c<script>3</script>d"
    assert page_text(raw) == "abcd"


def test_attribute_with_gt_sign_inside_quotes_kept_simple():
    # The documented procedure is naive on purpose: the first '>' ends a tag.
    assert page_text("<div data-x='a>b'>text</div>") == "b'>text"


def test_newlines_tabs_collapsed():
    assert page_text("a\n\t b\r\n c") == "a b c"


def test_comment_builder_helper():
    assert retry_blocks("keep<!-- drop -->keep2") == "keepkeep2"


def test_sentinel_survives_extraction():
    # Test pages rely on FA_OBS{...} staying visible after extraction.
    from tests.direct.flightattest_support import make_obs, make_page

    text = page_text(make_page("Flight ST221 is on schedule and boarding at gate B7.", make_obs(status="ON_SCHEDULE")))
    assert "FA_OBS{" in text
    assert "gate B7." in text
