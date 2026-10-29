# 嘉惠食品 · 独立发货前审单

独立入口：`standalone_desktop.py`；Windows 程序名：`JiahuiShipmentAudit.exe`。
无需微信登录，无微信监听、鼠标键盘控制，不需要 Python、Node 或 Ollama（使用 Windows 构建包时）。原微信群版入口保持不变。

## 使用

1. 优先下载 `JiahuiShipmentAudit-Setup-Windows-x64` 安装包 artifact，解压后双击其中 `JiahuiShipmentAudit-Setup-Windows-x64.exe`，按向导安装到当前用户目录，无需管理员权限。也可下载经过成功构建的 `JiahuiShipmentAudit-Windows-x64` Actions artifact，解压外层下载包和内层软件 ZIP，完整保留 `JiahuiShipmentAudit` 文件夹。双击其中 EXE。不能只复制 EXE，`_internal` 包含运行环境和本地 OCR 模型。仅支持 Windows x64。
2. 默认 Base URL `https://api.deepseek.com`，模型 `deepseek-chat`。在遮挡的 Key 栏自行填写密钥，勿向他人发送密钥。可设置 OpenAI 兼容 HTTPS 服务及对应模型，Base URL 可含 `/v1`。兼容服务须支持聊天接口和 JSON 输出；本机服务可使用 HTTP。更换地址会清空当前 Key，防止发送到另一服务。
3. 粘贴一份单据文字，和/或上传同一单据的 1～4 张清晰图片，然后点击“添加为一份材料”。订单、出货单分别添加；同一单据的补充文字与图片可放同一份。无需固定模板、标签或指令。可选人工指定单据类型，无法确定则保持自动判断并人工核对。最多 8 份，每份文字与 OCR 合计不超过 20000 字；单张图片不超过 12 MiB / 3200 万像素，支持 PNG/JPG/WEBP/BMP。暂不直接支持 PDF、Excel。
4. 点击“开始审核”。尚未添加的编辑区内容会自动添加为最后一份材料。上传、粘贴和打开软件均不会调用模型。图片先本机 OCR，文字（含 OCR 文字）再发送到所配置 API。每份材料一般一次字段提取请求；多份会多次调用并计费。
5. 查看全部识别字段、逐行 evidence、OCR / 输入原文及核算问题。点击“复制结果”复制当前结果；“清空材料”会停止任务并清除材料、结果和剪贴板，保留 API 配置。复制结果包含业务原文，注意保密。材料和审核结果不自动写入磁盘。
6. “停止审核”或 Esc 会终止本次审核子进程，窗口保持打开，可重新审核。关闭窗口也停止任务。**已经发出的 API 请求可能仍产生费用，停止无法撤销供应商端已收到的请求。**

只提供出货单时，仅核算数量×明确同单位单价、行金额、总计、重复行和缺失字段；不验证成交价、实际库存或实物。数量单位不明或与计价单位不一致时不擅自换算。金额使用 Decimal 和分位四舍五入。折扣、运费、税费、OCR 低置信度等需人工确认。

仅恰好两份、类型明确为一张订单和一张出货单，并且客户和明确关联订单号一致时自动两单比对。比较商品、规格、编码、数量、单位、单价及计价单位；缺少关联信息、不确定类型、多单多份时分别自查并提示人工确认，不按相似名称强行配对。不能确认是最终版本时仍须人工核实。

补充审核要求会传给字段提取器，只能辅助提取，不能关闭程序内的金额核算、绕过人工确认或让软件自动批准发货。软件没有发货批准、微信发送或库存更新功能。模型及 OCR 均可能错识，报告不是财务审计结论。

## Key 与本机配置

默认不保存 Key，仅本次进程使用。勾选“仅在本机加密保存”并开始审核后，配置保存在 `%LOCALAPPDATA%\JiahuiShipmentAudit`：`config.json` 仅含地址、模型和补充要求；`key.dpapi` 使用 Windows 当前用户 DPAPI 加密（不是明文）。取消勾选并再次开始审核可删除已保存 Key；也可在关闭软件后直接删除 `key.dpapi`。相同 Windows 用户仍可能解密，不能防护已被入侵的用户账户。切勿提交该目录。密钥不会放进命令行参数、报告或构建包。

## 构建与验证边界

GitHub Actions 工作流：`.github/workflows/windows-standalone-audit.yml`，可在 Actions → **Build Windows standalone shipment audit** → Run workflow 启动。流水线仅安装独立依赖，运行模拟接口/核算测试，使用 PyInstaller 构建，再运行打包程序 `--self-test` 验证 Tk、真实本机 OCR 夹具、DPAPI 和不导入微信模块。之后还使用 Inno Setup 构建安装器，静默安装、运行已安装程序自检并卸载；全部通过才上传安装器、ZIP、SHA256 和包内 `packaged-self-test.json`。Artifact 保留 30 天，下载通常需登录有仓库读取权限的 GitHub 账号。校验：`Get-FileHash .\JiahuiShipmentAudit-Windows-x64.zip -Algorithm SHA256`，与 `SHA256SUMS.txt` 比较。

开发测试：Python 3.12，安装 `standalone-build-requirements.txt`，仓库根目录运行：

```powershell
python -m pytest -q wechat-audit/tests/test_standalone.py wechat-audit/tests/test_review.py wechat-audit/tests/test_auto_review.py wechat-audit/tests/test_text_materials.py
python wechat-audit/build_standalone.py
```

模拟测试不证明真实 API 余额、模型兼容性或真实单据精度；打包自检不证明最终用户电脑运行成功。首次使用请用脱敏样单人工核对，确认服务商、计费和识别结果。本次交付的真实验证记录见随附 `VALIDATION.md`，不要将未完成的 Actions 或实机验证视为已通过。

产品 PDF 与公司网页知识库留待后续接入，本版不加载或编造产品目录、实时价格、库存。
