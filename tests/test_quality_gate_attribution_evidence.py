"""#232：证据必须自己说明「这次归属是怎么来的」。

#230 之后证据里有 by_method，但读报告的人仍要自己比对
attributed_to_gate_tree 和 by_method["process-tree"] 是否一致，还要自己判断
域名兜底是不是占了全部。这两条把口径钉死：归属数字只从一个地方算；域名兜底
占全部时证据直接写明，不让人误以为真实跑里真有进程归属。
"""

from __future__ import annotations

import unittest

from tools.quality_gate import guard_attribution_evidence


class AttributionEvidenceContract(unittest.TestCase):
    def test_domain_only_run_says_so_in_the_evidence(self):
        # 真实 trial 跑就是这个形状（699 条全是域名兜底）。此时判红与否完全由
        # 域名清单决定，证据必须自己说出来。
        events = [
            {"kind": "browser-background-network", "target": "clients2.googleusercontent.com"},
            {"kind": "browser-background-network", "target": "ntp.msn.cn"},
            {"kind": "host-tool-network", "target": "api.anthropic.com"},
        ]

        attribution = guard_attribution_evidence(events)

        self.assertEqual(attribution["by_method"], {"domain-fallback": 3})
        self.assertEqual(attribution["blocking"], [])
        self.assertTrue(
            attribution["domain_fallback_only"],
            f"全部靠域名兜底却没在证据里标明：{attribution}",
        )

    def test_process_identity_fields_reach_the_evidence(self):
        # 期望 1 的可审计部分：带进程身份的归属证据必须带 ppid 和可执行路径
        # （pid 每台都变，换机器时只有这两个能对上）。
        # 既有契约：proxy-egress 不带 target 时才是 proxy-hop；带 target
        # （知道 CONNECT 连向哪）就算进程树归属。两条都要钉住。
        events = [
            {
                "kind": "proxy-request",
                "target": "api.elevenlabs.cn",
                "pid": 4242,
                "ppid": 100,
                "exe": "C:\\Python\\python.exe",
            },
            {"kind": "proxy-egress", "target": "", "pid": 4242, "ppid": 100, "exe": "C:\\Python\\python.exe"},
            {"kind": "browser-background-network", "target": "clients2.googleusercontent.com"},
        ]

        attribution = guard_attribution_evidence(events)

        self.assertEqual(
            attribution["by_method"],
            {"process-tree": 1, "proxy-hop": 1, "domain-fallback": 1},
        )
        # 期望 3：进程树计数只剩一个字段。留着两个同号的数字不会阻止它们
        # 各自漂移，所以钉的是「重复字段已经删掉」。
        self.assertNotIn(
            "attributed_to_gate_tree",
            attribution,
            "报告里还有与 by_method['process-tree'] 重复的计数字段",
        )
        self.assertFalse(attribution["domain_fallback_only"])
        self.assertEqual(
            [
                (event["kind"], event["target"], event["ppid"], event["exe"])
                for event in attribution["attributed_processes"]
            ],
            [("proxy-request", "api.elevenlabs.cn", 100, "C:\\Python\\python.exe")],
            "带进程身份的归属证据没把 ppid / 可执行路径带出来",
        )


if __name__ == "__main__":
    unittest.main()
