"""图片渲染层：模板（templates）、上下文（contexts）、文本兜底（text_fallback）。"""

from . import contexts, templates, text_fallback  # noqa: F401

# 渲染选项：
#   - png：文字锐利（默认 jpeg quality=40 会糊）
#   - viewport_width/height：官方 t2i 服务据此设定截图视口（默认 800x720 会给
#     760px 宽的版面留白、把矮版面垫高）。宽=版面宽、高取小值，配合 full_page
#     让成图恰好框住有效内容；旧版/自建服务不认这两个键时会静默忽略，
#     模板 <head> 里的 <meta name="viewport"> 是同一语义的第二重保险。
RENDER_OPTIONS = {
    "type": "png",
    "full_page": True,
    "viewport_width": templates.VIEWPORT_WIDTH,
    "viewport_height": templates.VIEWPORT_HEIGHT,
}
