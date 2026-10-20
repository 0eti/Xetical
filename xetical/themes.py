"""테마(색 조합) 미리 만든 세트(2-6). 글꼴 종류·크기는 그대로 두고 색과 투명도만 바꾼다."""

THEME_KEYS = ("cell_bg", "holiday_bg", "today_border", "today_fill", "title_button_color", "holiday_text",
              "lunar_text", "transparency", "glass", "weekday_colors")
FONT_COLOR_KEYS = ("date_font", "item_font")

PRESETS = {
    "DesktopCal 기본": {
        "cell_bg": "#808080", "holiday_bg": "#505050", "today_border": "#282828", "today_fill": "",
        "title_button_color": "#ffffff", "holiday_text": "#ff9d1c", "lunar_text": "#bdbdbd",
        "transparency": 20, "glass": False, "weekday_colors": {},
        "date_color": "#ffffc0", "item_color": "#ffffff",
    },
    "차콜 (어둡게)": {
        "cell_bg": "#4a4a4a", "holiday_bg": "#2e2e2e", "today_border": "#ffd54f", "today_fill": "",
        "title_button_color": "#e0e0e0", "holiday_text": "#ff8a65", "lunar_text": "#9e9e9e",
        "transparency": 10, "glass": False, "weekday_colors": {},
        "date_color": "#ffe082", "item_color": "#f5f5f5",
    },
    "밝은 종이": {
        "cell_bg": "#ffffff", "holiday_bg": "#e9e4dc", "today_border": "#e53935", "today_fill": "#fff3c4",
        "title_button_color": "#333333", "holiday_text": "#d32f2f", "lunar_text": "#757575",
        "transparency": 5, "glass": False, "weekday_colors": {"5": "#1e63c6", "6": "#d32f2f"},
        "date_color": "#333333", "item_color": "#222222",
    },
    "반투명 유리": {
        "cell_bg": "#9e9e9e", "holiday_bg": "#6d6d6d", "today_border": "#ffffff", "today_fill": "",
        "title_button_color": "#ffffff", "holiday_text": "#ffb74d", "lunar_text": "#e0e0e0",
        "transparency": 60, "glass": True, "weekday_colors": {},
        "date_color": "#ffffff", "item_color": "#ffffff",
    },
    "블루 나이트": {
        "cell_bg": "#3b5b8c", "holiday_bg": "#273f63", "today_border": "#ffeb3b", "today_fill": "",
        "title_button_color": "#e3f2fd", "holiday_text": "#ffab91", "lunar_text": "#b3c7e6",
        "transparency": 20, "glass": False, "weekday_colors": {},
        "date_color": "#fff59d", "item_color": "#ffffff",
    },
    "포레스트": {
        "cell_bg": "#4f7a52", "holiday_bg": "#36553a", "today_border": "#fff176", "today_fill": "",
        "title_button_color": "#f1f8e9", "holiday_text": "#ffcc80", "lunar_text": "#c5e1a5",
        "transparency": 20, "glass": False, "weekday_colors": {},
        "date_color": "#fff9c4", "item_color": "#ffffff",
    },
    "로즈": {
        "cell_bg": "#a0566b", "holiday_bg": "#73394b", "today_border": "#ffffff", "today_fill": "",
        "title_button_color": "#fff0f3", "holiday_text": "#ffe082", "lunar_text": "#f8bbd0",
        "transparency": 20, "glass": False, "weekday_colors": {},
        "date_color": "#fff1f4", "item_color": "#ffffff",
    },
}


def theme_values(theme, cfg):
    """테마 → 설정에 적용할 값(dict). 글꼴은 색만 바꾼다."""
    out = {k: theme[k] for k in THEME_KEYS if k in theme}
    if "date_color" in theme:
        out["date_font"] = dict(cfg["date_font"], color=theme["date_color"])
    if "item_color" in theme:
        out["item_font"] = dict(cfg["item_font"], color=theme["item_color"])
    return out


def capture(cfg):
    """지금 설정의 색 조합을 '내 테마'로 저장할 형태로."""
    out = {k: cfg[k] for k in THEME_KEYS}
    out["date_color"] = cfg["date_font"]["color"]
    out["item_color"] = cfg["item_font"]["color"]
    return out
