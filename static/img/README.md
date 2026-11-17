# 萤火画布品牌图标（#420）

唯一母版是 `firefly-logo.svg`，来自用户选定的 B3 简洁版：
`.local/agent-work/design/logo/firefly-logo.svg`（见 #420 定稿评论）。
本目录 SVG 原样复制，SHA256：
`69a17f340eaf1fedb91242906c95515345b0ca5ed772737adc0dc8789fa41f0c`。

- `firefly-logo.svg`：矢量网页图标，viewBox `0 0 64 64`。
- `firefly-logo.ico`：同一母版的 16、32、48、256px 四个尺寸；网页兜底，#419 的托盘/快捷方式可复用。
- `apple-touch-icon.png`：同一母版直接渲染的 180×180 RGBA PNG。

导出方法：浏览器打开 SVG，视口分别设为 256×256、180×180，
`deviceScaleFactor=1`，透明背景截图。256px PNG 用 Pillow 封装 ICO：

```python
from PIL import Image
Image.open("firefly-logo-256.png").save(
    "firefly-logo.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)]
)
```

不另画小尺寸近似版。品牌图标不是画布操作图标，不加入操作图标 sprite。
