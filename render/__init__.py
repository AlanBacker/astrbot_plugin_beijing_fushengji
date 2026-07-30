"""图片渲染层：模板（templates）、上下文（contexts）、文本兜底（text_fallback）。"""

from . import contexts, templates, text_fallback  # noqa: F401

# 渲染选项：png 保证文字锐利（默认 jpeg quality=40 会糊）
RENDER_OPTIONS = {"type": "png", "full_page": True}
