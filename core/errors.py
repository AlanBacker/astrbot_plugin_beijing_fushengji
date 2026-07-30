"""游戏业务异常。

引擎内所有"玩家操作不合法"的情况都抛出 GameError，
由插件层捕获并把 user_msg 直接回复给玩家。
引擎的代码缺陷（真正的 bug）不使用 GameError，让其自然抛出以便暴露。
"""

from __future__ import annotations


class GameError(Exception):
    """玩家可见的业务错误。

    Attributes:
        user_msg: 面向玩家的中文提示，可直接发送。
        hint: 可选的后续操作提示（如正确的命令用法）。
    """

    def __init__(self, user_msg: str, hint: str = ""):
        super().__init__(user_msg)
        self.user_msg = user_msg
        self.hint = hint

    def reply_text(self) -> str:
        """组合成一条完整的回复文本。"""
        if self.hint:
            return f"{self.user_msg}\n💡 {self.hint}"
        return self.user_msg
