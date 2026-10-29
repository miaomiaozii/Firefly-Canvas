"""#260 Upscale 菜单契约：生成历史详情里的 2K/4K/8K 档位（对照 ComfyArt UpscaleMenu，主笔记 §92.4）。

钉住三件事：
1. 档位菜单存在（2k/4k/8k 三个 `data-upscale-tier` 按钮），且 `.smart-log-detail-copy-2k` 这个
   类名与 2K 行为保持（#238/#249 的 ui_integrity 浏览器证据按这个选择器点击）。
2. 「先显示目标尺寸」：档位标签写的是按原图比例换算出的目标像素，不是只写 2K/4K/8K。
3. 「不可用时 disabled」：目标像素超过浏览器 canvas 预算的档位必须禁用并给出原因文案；
   重采样放大的档位可用，但 title 必须说清「不会增加细节」（ADR 0006：不承诺做不到的事）。

档位换算与禁用判定用 Node 求值 canvas-media-history.js 里的真实实现，不在 Python 里复制一份逻辑。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from tests.canvas_frontend_sources import ROOT

MEDIA_HISTORY_JS = ROOT / "static" / "js" / "canvas-media-history.js"
SMART_CANVAS_JS = ROOT / "static" / "js" / "smart-canvas.js"
I18N_SMART_JS = ROOT / "static" / "js" / "i18n" / "smart-canvas.js"


class UpscaleMenuContract(unittest.TestCase):
    def test_three_tiers_markup_and_legacy_selector(self):
        source = MEDIA_HISTORY_JS.read_text(encoding="utf-8")
        for tier in ("2k", "4k", "8k"):
            self.assertIn(f'data-upscale-tier="{tier}"', source, f"缺少 {tier} 档位按钮")
        self.assertIn('class="smart-log-detail-copy-2k"', source, "2K 按钮类名必须保持（ui_integrity 证据按它点击）")
        self.assertIn("smart-log-detail-upscale-size", source, "档位必须显示目标尺寸")
        self.assertIn("img.addEventListener('load'", source, "目标尺寸要在原图真实像素可得后同步")

    def test_copy_path_is_generalized(self):
        source = SMART_CANVAS_JS.read_text(encoding="utf-8")
        self.assertIn("async function smartHistoryImageUpscaledPngBlob(item, maxEdge=2048)", source)
        self.assertIn("async function copySmartHistoryImageUpscaled(item, maxEdge=2048)", source)
        # 旧的 2K 入口保留为薄壳，不能删（#238 的 ui_integrity 证据与耦合报告都按这个名字钉）。
        self.assertIn("function copySmartHistoryImage2k(item)", source)
        self.assertIn("return smartHistoryImageUpscaledPngBlob(item, 2048);", source)
        self.assertIn("const scale = targetEdge / longest;", source, "档位必须能把长边贴到目标（允许插值放大），不是只裁到 2K")
        self.assertIn("SMART_UPSCALE_MAX_PIXELS", source, "必须卡住浏览器 canvas 像素预算")

    def test_i18n_keys_defined(self):
        source = I18N_SMART_JS.read_text(encoding="utf-8")
        for key in ("smart.copy4k", "smart.copy8k", "smart.upscaleCopy", "smart.upscaleCopyDone", "smart.upscaleResampleHint", "smart.upscaleTooLarge"):
            parts = source.split(f'"{key}"', 1)
            self.assertEqual(len(parts), 2, f"i18n 缺少 {key}")
            self.assertIn("zh:", parts[1].splitlines()[0], f"{key} 缺少中文")
            self.assertIn("en:", parts[1].splitlines()[0], f"{key} 缺少英文")
        # 上面 Node 求值里断言的是理由键，这里钉住用户真看到的中文说法。
        self.assertIn("超出", source.split('"smart.upscaleTooLarge"', 1)[1].splitlines()[0])
        self.assertIn("重采样", source.split('"smart.upscaleResampleHint"', 1)[1].splitlines()[0])

    @unittest.skipIf(shutil.which("node") is None, "需要 node 求值档位换算")
    def test_target_size_and_disabled_behavior(self):
        harness = (
            "const fs=require('fs');"
            f"const src=fs.readFileSync({json.dumps(str(MEDIA_HISTORY_JS))},'utf8');"
            "const body=src.slice(src.indexOf('const SMART_UPSCALE_TIERS'), src.indexOf('// #238 Perf(W1)'));"
            "const api=new Function('tr','trf', body + ';return {SMART_UPSCALE_TIERS, syncSmartHistoryUpscaleButtons, SMART_UPSCALE_MAX_PIXELS, SMART_UPSCALE_MAX_SCALE};');"
            "const ops=api(k=>k,(k,v)=>k+':'+Object.entries(v).map(([a,b])=>a+'='+b).join(','));"
            "const make=tier=>{const b={dataset:{upscaleTier:tier},disabled:null,attributes:{},shown:''};"
            "b.setAttribute=(k,v)=>{b.attributes[k]=v;};"
            "b.querySelector=()=>({set textContent(v){b.shown=v;}});return b;};"
            "const buttons=ops.SMART_UPSCALE_TIERS.map(t=>make(t.key));"
            "const box={querySelector:()=>null,querySelectorAll:()=>buttons};"
            "const dimsOf=size=>{ops.syncSmartHistoryUpscaleButtons(box,{width:size.width,height:size.height},'3 / 8');"
            "return buttons.map(b=>({tier:b.dataset.upscaleTier,shown:b.shown,disabled:b.disabled,title:b.attributes.title}));};"
            "console.log(JSON.stringify({tiers:ops.SMART_UPSCALE_TIERS.map(t=>t.key),"
            "maxScale:ops.SMART_UPSCALE_MAX_SCALE,maxPixels:ops.SMART_UPSCALE_MAX_PIXELS,"
            "small:dimsOf({width:1024,height:683}),"
            "wide:dimsOf({width:4096,height:2304}),"
            "square:dimsOf({width:6000,height:6000})}));"
        )
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, f"node 求值失败：{result.stderr[:2000]}")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["tiers"], ["2k", "4k", "8k"])
        # 「先显示目标尺寸」：按原图比例换算，1024×683 → 2K=2048×1366 / 4K=4096×2732 / 8K=8192×5464
        small = {row["tier"]: row for row in payload["small"]}
        self.assertEqual([small[k]["shown"] for k in ("2k", "4k", "8k")], ["2048×1366", "4096×2732", "8192×5464"])
        self.assertFalse(small["2k"]["disabled"], "2 倍放大可用，不能禁用")
        self.assertIn("smart.upscaleResampleHint", small["2k"]["title"], "重采样放大必须在 title 里带上“不会增加细节”的理由键")
        self.assertIn("3 / 8", small["2k"]["title"], "title 必须带上是当前第几张原图")
        self.assertFalse(small["4k"]["disabled"], f"{payload['maxScale']} 倍整刚好在限内")
        # 1024 长边放大到 8192 = 8 倍，超过 SMART_UPSCALE_MAX_SCALE：不可用，且必须给原因。
        self.assertTrue(small["8k"]["disabled"], "超过倍率上限的档位必须禁用")
        self.assertIn("smart.upscaleMaxScale", small["8k"]["title"], "禁用必须给出原因文案（理由键）")
        # 4096×2304 的原图：2K/4K 是缩小或同尺寸复制（可用、不带重采样提示），
        # 8K 是 2 倍重采样（可用、带提示）——上限是 4 倍，所以不禁用。
        wide = {row["tier"]: row for row in payload["wide"]}
        self.assertEqual([wide[k]["shown"] for k in ("2k", "4k", "8k")], ["2048×1152", "4096×2304", "8192×4608"])
        for tier in ("2k", "4k"):
            self.assertFalse(wide[tier]["disabled"], f"{tier} 是缩小/同尺寸复制，不该禁用")
            self.assertNotIn("smart.upscaleResampleHint", wide[tier]["title"], "缩小/同尺寸复制不该带重采样提示")
        self.assertFalse(wide["8k"]["disabled"], "2 倍重采样在上限内，必须可用")
        self.assertIn("smart.upscaleResampleHint", wide["8k"]["title"])
        # 6000×6000 的原图放大到 8K = 8192×8192 = 67M px，超过像素预算：即使倍率只有 1.37 倍也要禁用。
        square = {row["tier"]: row for row in payload["square"]}
        self.assertEqual(square["8k"]["shown"], "8192×8192")
        self.assertFalse(square["4k"]["disabled"])
        self.assertTrue(square["8k"]["disabled"], "超过 canvas 像素预算的档位必须禁用")
        self.assertIn("smart.upscaleTooLarge", square["8k"]["title"], "禁用必须给出原因文案（理由键）")
        self.assertLess(payload["maxPixels"], 8192 * 8192, "像素预算必须低于 8K 方形像素数")

if __name__ == "__main__":
    unittest.main()
