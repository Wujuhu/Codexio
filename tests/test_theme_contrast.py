import pytest

from aiquota.theme import theme_colors


def luminance(hex_color):
    rgb = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in rgb]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + .05) / (dark + .05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_small_text_and_primary_actions_remain_readable(theme):
    colors = theme_colors(theme)
    for background in ("surface", "raised", "blue_tint", "teal_tint", "gold_tint"):
        assert contrast(colors["text"], colors[background]) >= 4.5
        assert contrast(colors["muted"], colors[background]) >= 4.5
    for background in ("action", "action_hover"):
        assert contrast(colors["action_text"], colors[background]) >= 4.5
    assert contrast(colors["header_text"], colors["header_bg"]) >= 4.5
    assert contrast(colors["scroll_thumb"], colors["scroll_track"]) >= 3
    for metric in ("chart_tokens", "chart_cost", "chart_input", "chart_cache_read", "chart_cache_write", "chart_output"):
        assert contrast(colors[metric], colors["surface"]) >= 4.5
