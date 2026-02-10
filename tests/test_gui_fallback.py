from gui_app import should_fallback_to_web


def test_fallback_on_new_macos_with_old_tk():
    assert should_fallback_to_web("Darwin", 26, 8.5) is True


def test_no_fallback_on_new_macos_with_modern_tk():
    assert should_fallback_to_web("Darwin", 26, 8.6) is False


def test_no_fallback_on_old_macos():
    assert should_fallback_to_web("Darwin", 14, 8.5) is False


def test_no_fallback_on_non_macos():
    assert should_fallback_to_web("Linux", 0, 8.5) is False
