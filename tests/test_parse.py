"""输入解析测试。"""

from __future__ import annotations

import pytest

from core import parse
from core.errors import GameError


class TestParseGood:
    @pytest.mark.parametrize(
        "token, idx",
        [
            ("1", 0), ("8", 7),
            ("香烟", 0), ("进口香烟", 0), ("烟", 0),
            ("汽车", 1), ("车", 1),
            ("vcd", 2), ("VCD", 2), ("光盘", 2), ("盗版", 2),
            ("假酒", 3), ("白酒", 3),
            ("古董", 4), ("字画", 4),
            ("玩具", 5),
            ("手机", 6),
            ("化妆品", 7), ("化妆", 7),
        ],
    )
    def test_accepts(self, token, idx):
        assert parse.parse_good(token) == idx

    @pytest.mark.parametrize("token", ["", "9", "0", "茅台", "假"])
    def test_rejects(self, token):
        with pytest.raises(GameError):
            parse.parse_good(token)


class TestParseLocation:
    @pytest.mark.parametrize(
        "token, idx",
        [("1", 0), ("10", 9), ("北京站", 0), ("西直门", 2), ("西", 2), ("苹果园", 9), ("苹果", 9)],
    )
    def test_accepts(self, token, idx):
        assert parse.parse_location(token) == idx

    @pytest.mark.parametrize("token", ["", "11", "0", "上海站", "门"])
    def test_rejects(self, token):
        with pytest.raises(GameError):
            parse.parse_location(token)

    @pytest.mark.parametrize("token", ["", "11", "火星"])
    def test_error_hint_carries_station_menu(self, token):
        with pytest.raises(GameError) as ei:
            parse.parse_location(token)
        assert "北京站" in ei.value.hint and "苹果园" in ei.value.hint


class TestParseQty:
    def test_values(self):
        assert parse.parse_qty("15") == 15
        for w in ["全", "全部", "所有", "all", "ALL", "梭哈"]:
            assert parse.parse_qty(w) is None

    @pytest.mark.parametrize("token", ["0", "-3", "1.5", "abc", ""])
    def test_rejects(self, token):
        with pytest.raises(GameError):
            parse.parse_qty(token)


class TestParseMoney:
    def test_plain_and_wan(self):
        assert parse.parse_money("500") == 500
        assert parse.parse_money("2万") == 20000
        assert parse.parse_money("1.5万") == 15000
        assert parse.parse_money("3w") == 30000
        assert parse.parse_money("1,000") == 1000
        assert parse.parse_money("全") is None

    def test_half(self):
        assert parse.parse_money("半", base_for_half=999) == 499
        assert parse.parse_money("一半", base_for_half=2000) == 1000
        with pytest.raises(GameError):
            parse.parse_money("半", base_for_half=1)

    @pytest.mark.parametrize("token", ["0", "-5", "钱", "", "0.4"])
    def test_rejects(self, token):
        with pytest.raises(GameError):
            parse.parse_money(token)
