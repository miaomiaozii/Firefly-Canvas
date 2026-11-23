(function(){
    if(!window.StudioI18n) return;
    window.StudioI18n.register({
        "common.productName": { zh: "萤火画布" },
        "serviceVersion.restartTitle": { zh: "服务需要重启" },
        "serviceVersion.unknownTitle": { zh: "服务版本未确认" },
        "serviceVersion.stale": { zh: "后端已更新，需要重启服务。请在萤火画布托盘菜单选择“重启服务”；若使用 run.bat 启动，请先在原服务窗口按 Ctrl+C 停止，再运行 run.bat，然后重新载入页面。仅刷新缓存不会加载新的后端代码。" },
        "serviceVersion.legacy": { zh: "当前服务未提供后端版本信息，无法确认已加载新代码。请在萤火画布托盘菜单选择“重启服务”；若使用 run.bat 启动，请先在原服务窗口按 Ctrl+C 停止，再运行 run.bat，然后重新载入页面。" },
        "serviceVersion.unknown": { zh: "无法确认运行中的后端版本；这不代表代码版本不一致。请检查服务是否已启动，恢复连接后重新检查。" },
        "serviceVersion.retry": { zh: "重新检查" },
        "common.apiSettings": { zh: "API 设置", en: "API Settings" },
        "common.darkMode": { zh: "黑夜模式", en: "Dark Mode" },
        "common.lightMode": { zh: "白天模式", en: "Light Mode" },
        // #365：这里原来有一条语言下拉的标签文案（中文 / English）。下拉已经整个没了，
        // 这条也没人用了，跟着删：界面上已经没有任何能把语言切到英文的入口。
        "common.cancel": { zh: "取消", en: "Cancel" },
        "common.confirm": { zh: "确定", en: "Confirm" },
        // #347：应用内对话框（ui-dialog.js）的默认标题与撤销按钮；只加 zh（#365 方向：界面只留中文）
        "common.confirmTitle": { zh: "确认" },
        "common.undo": { zh: "撤销" },
        "common.save": { zh: "保存", en: "Save" },
        "common.delete": { zh: "删除", en: "Delete" },
        "common.edit": { zh: "编辑", en: "Edit" },
        "common.run": { zh: "运行", en: "Run" },
        "common.running": { zh: "运行中", en: "Running" },
        "common.custom": { zh: "自定义", en: "Custom" },
        "common.close": { zh: "关闭", en: "Close" },
        "common.loading": { zh: "加载中...", en: "Loading..." },
        "common.project": { zh: "项目主页", en: "Project" },
        "common.moreSettings": { zh: "更多设置", en: "More Settings" },
        "nav.textToImage": { zh: "文生图", en: "Text to Image" },
        "nav.localTools": { zh: "本地功能", en: "Local Tools" },
        "nav.imageEdit": { zh: "图片编辑", en: "Image Edit" },
        "nav.angle": { zh: "角度控制", en: "Angle Control" },
        "nav.online": { zh: "在线生图", en: "Online Image" },
        "nav.gpt": { zh: "GPT 对话", en: "GPT Chat" },
        "nav.canvas": { zh: "无限画布", en: "Firefly Canvas" },
        "nav.assetManager": { zh: "素材库", en: "Assets" },
        "bulk.manage": { zh: "管理", en: "Manage" },
        "bulk.selectAll": { zh: "全选", en: "Select All" },
        "bulk.deselectAll": { zh: "取消全选", en: "Deselect All" },
        "bulk.deleteSelected": { zh: "删除所选", en: "Delete" },
        "bulk.exit": { zh: "完成", en: "Done" },
        "bulk.selectedCount": { zh: "已选 {n} 张", en: "{n} selected" },
        "bulk.deleteConfirm": { zh: "确认删除选中的 {n} 张图片？此操作不可恢复。", en: "Delete the selected {n} image(s)? This cannot be undone." },
        "bulk.deleting": { zh: "删除中…", en: "Deleting…" },
        "bulk.noSelection": { zh: "请先选择要删除的图片", en: "Select images to delete first" }
    });
})();
