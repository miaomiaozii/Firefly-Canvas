# CLI 服务

本目录保留在线生图/视频适配器所需的 Windows CLI 安装脚本。项目本身不再包含
macOS、Linux、ComfyUI 或本地工作流运行入口。

## OpenAI Codex CLI

- 安装/更新：`CLI/windows/openai/1-install_openai_codex_cli.bat`
- 启动/登录：`CLI/windows/openai/2-start_openai_codex_cli.bat`

安装后打开新的 Windows 终端运行：

```bash
codex
```

首次运行会提示使用 ChatGPT 账号或 API Key 登录。

## Gemini CLI

- 安装/更新：`CLI/windows/gemini/1-install_gemini_cli.bat`
- 启动/登录：`CLI/windows/gemini/2-start_gemini_cli.bat`

安装后打开新的 Windows 终端运行：

```bash
gemini
```

首次运行会提示使用 Google 账号或 Gemini 凭据登录。

## Jimeng CLI

- 安装/更新：`CLI/windows/jimeng/install_jimeng_cli.bat`
- 登录/检查：`CLI/windows/jimeng/login_jimeng_cli.bat`
- WSL Ubuntu 辅助程序：`CLI/windows/jimeng/install_wsl_ubuntu.bat`

根目录的 `.bat` 文件仅作为兼容启动器保留；CLI 仍通过线上服务生成内容。
