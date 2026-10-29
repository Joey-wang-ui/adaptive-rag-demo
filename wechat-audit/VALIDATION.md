# 独立版交付验证记录

日期：2026-10-29。实现分支：`codex/standalone-audit`。
安装器构建对应提交：`5b024960a7b0b29732e16de03d0f4c72160ff98f`。

## 本机 Linux / Python 3.12

- 原电商项目：`pytest -q tests`，210 passed（1 条依赖弃用警告）。
- 全部已有审单及新增独立版测试：142 passed、2 skipped。跳过项是 Windows DPAPI 和实际 Tk 界面测试。
- 全新虚拟环境仅安装独立版构建依赖：独立版及复用逻辑的流水线所选测试 76 passed、2 skipped。初次误选完整旧微信 HTTP 服务套件缺少 FastAPI；该服务不属于独立运行依赖，未为独立版增加服务依赖。
- 真实本地 RapidOCR 识别生成图片 `TOTAL 100` 成功；没有调用真实 API。
- 新增 Python 文件的 Ruff lint / format、Git diff whitespace 检查通过。

模拟测试覆盖：纯文字、图片、图文混合、错误行金额和总计、重复行、缺失信息、关联两单、拒绝不明确配对、401/402/403/429/500 与无效 JSON、取消检查点、真实子进程在模拟 HTTP 请求进行时被停止且可重启、无微信模块导入及不保存 Key。

## GitHub Actions Windows x64

运行：https://github.com/Joey-wang-ui/adaptive-rag-demo/actions/runs/40848828230

已从真实运行页面核实以下步骤成功：

- 只安装独立依赖并执行所选测试，包括 Windows 当前用户 DPAPI 与真实 Tk 窗口中的开始、停止、Esc 和清空操作；网络 API 为模拟服务。
- PyInstaller 独立 Windows EXE 构建。
- 打包程序的 Tk、本地 OCR 夹具、DPAPI、spawn 子进程 IPC 自检；未导入微信模块。
- Inno Setup 安装器编译、当前用户静默安装、已安装程序自检与卸载。

前两次 Windows 测试失败；明确修复新增测试对 UTF-8 配置文件使用系统默认编码的问题后，后续测试和便携包构建成功。失败运行未作为交付成功依据。

下载包及 SHA256 由该运行生成；请以该运行实际成功上传的 artifact 为准。安装器采用当前用户安装，无需 Python、Node、Ollama 或微信；没有代码签名，不宣称经过发行商签名。

## 尚未验证

- 未使用真实 DeepSeek Key 或真实付费 API，不证明余额、供应商模型配置或生产识别精度。
- 未在最终用户电脑安装或用嘉惠真实业务单据试跑。
- 未验证实际库存、实物、成交价真实性；没有自动发货批准功能。
- 未接入产品 PDF、公司网页知识库，不提供实时价格或库存。
