# wxauto4 Windows 只读试验

此入口是针对用户指定 `FreeWisdom/wxauto-4.0` 的可执行试验，不替代正式审核入口。自动回复、图片菜单下载和审核引擎调用均未接入。当前机器是 Linux；已执行副本准备、完整性校验、预检和模拟诊断测试，Windows 实机结果仍待收集。

## 已完成的隔离措施

- 固定上游提交 `bd7c5233e79c0a185638a325bf6d30607244dfa8`，包版本 `40.1.1`。从该提交的 Git blobs 准备独立副本，不复制工作区修改、未跟踪文件或符号链接目标。
- `wxauto4_prepare` 仅在副本内将 `utils/tools.py` 的 `delete_update_files()` 改成空操作。**覆盖初始化与监听循环两处调用**。不删除用户微信更新文件，不修改上游 checkout。副本目标已存在时拒绝覆盖。
- 记录原始/修改后每个文件的 SHA-256。预检和 SDK 导入前校验文件，拒绝额外 Python 源文件；这些哈希用于复现和发现意外修改，不是防同机恶意用户伪造清单的安全签名。
- 关闭 SDK 文件日志、发送者 OCR 和消息 hash。使用独立进程、独立虚拟环境，不与现有微信机器人共用已导入的 SDK。
- **不调用 `AddListenChat`**，避免它打开新窗口。只从现有聊天子窗口中匹配明确群名，缺少目标、同名窗口重复、匹配到私聊或群身份变化时停止。
- 不调用 `Show`、`ChatWith`、`KeepRunning`、`StopListening`、`Close`、发送或图片保存；用户自己打开/最小化窗口，退出不会关闭窗口。枚举已打开子窗口不等于全群会话扫描；只有白名单群进入消息轮询。
- 每轮读取前后记录前台 HWND、剪贴板序号和指定群窗口的最小化状态，不记录剪贴板内容。采样变化只是观测，可能来自人的操作，不能自动认定为 SDK 抢焦点；极短暂的变化也可能落在采样间隔之间，仍需人工录屏/对照。

## Windows 准备步骤

需要 Git、Python 3.12 和已登录的电脑版微信。上游目前只声明支持微信 4.0.5；请先记录你实际的微信版本。不要为了试验直接降级日常使用的微信。许可证仍未找到明确声明，试验代码没有改变这个事实；正式业务使用、打包或分发前需向维护者确认授权。

在仓库的 `wechat-audit` 目录打开 PowerShell。以下目录都是当前项目已忽略的本地运行路径；已有同名目录时先保留，改用新目录名，脚本不会覆盖试验副本。

```powershell
py -3.12 -m venv .runtime/wxauto4-venv
git clone https://github.com/FreeWisdom/wxauto-4.0.git .runtime/wxauto4-upstream
git -C .runtime/wxauto4-upstream checkout --detach bd7c5233e79c0a185638a325bf6d30607244dfa8
.runtime/wxauto4-venv/Scripts/python.exe -m shipment_bot.wechat.wxauto4_prepare --source .runtime/wxauto4-upstream --destination .runtime/wxauto4-reviewed
.runtime/wxauto4-venv/Scripts/python.exe -m pip install -e .runtime/wxauto4-reviewed
```

安装使用准备后的本地副本；不要换成 `pip install wxauto4`，它未必对应这份 fork。依赖安装保留 TLS/包校验；目前未在 Windows 验证完整安装，出现错误应保留具体输出，不能以安装成功推断监听验收通过。

先只做预检（不导入 SDK，不连接微信）：

```powershell
.runtime/wxauto4-venv/Scripts/python.exe -m shipment_bot.wechat.wxauto4_diagnostics --bundle .runtime/wxauto4-reviewed --database data/wxauto4-trial.sqlite3 --group "授权测试群"
```

再手动把唯一的授权测试群打开为独立聊天窗口。启动 120 秒实机诊断需显式添加 `--live`：

```powershell
.runtime/wxauto4-venv/Scripts/python.exe -m shipment_bot.wechat.wxauto4_diagnostics --bundle .runtime/wxauto4-reviewed --database data/wxauto4-trial.sqlite3 --group "授权测试群" --seconds 120 --live
```

支持重复 `--group`，但首轮建议只测一个群。这里没有读取微信本地数据库或进程内存的代码，也没有开关启用这类功能。SDK 通过 Windows UIAutomation 读取界面；当前被动调用链仍需实机验证，不承诺完全不影响 UI。

## 怎样验收

1. 先保持群独立窗口可见，由其他成员发送带唯一编号的文字，核对发送者显示名与记录数量。
2. 在 VS Code 连续输入，同时发送编号文字；观察鼠标与焦点，在录屏中对照 `wxauto4_ui_samples` 的采样结果。
3. 手动最小化微信主窗口及测试群窗口，再发编号文字，区分两种窗口状态。没有新记录时不得宣称最小化监听成功。
4. 发送重复文本、两位同昵称成员的文本和图片、真 @ 与手动粘贴的 @文本。确认诊断中的 `sent_at`、`mention_ids`、`image_path` 都保持空值，没有伪造稳定字段。用 `/审核 A001` 可观察命令文本，但不会触发审核。
5. 连续发送编号 1～100，人工比对完整清单；程序不能知道它从未收到的消息，因此条目计数不是零遗漏证明。
6. Ctrl+C 后重启。旧观测保留，但新进程分配新 session，避免把重用的 UI ID 当成同一条真实消息。**当前不保证 SDK 重启期间补拉历史消息**；启动前已有消息通常被上游当作基线，不作为新消息返回。

结果在本地 SQLite：

| 表 | 内容 |
| --- | --- |
| `wxauto4_sessions` | 每次试验会话 ID、开始时间、固定上游提交 |
| `wxauto4_observations` | 白名单群显示名、UI runtime ID、接收时间、文本/类型/发送者显示名 |
| `wxauto4_ui_samples` | 每次读取前后的前台 HWND、剪贴板序号、测试窗口最小化状态 |
| `wxauto4_probe_events` | 绑定、停止、缺 ID 等状态事件 |

同一次 session 中按群与 UI ID 去重；相同 ID 出现不同内容则停止并报告冲突。不同 session 保留各自观测，**没有宣称跨重启零重复**。数据不会写入 `wechat_inbox` 或 `audit_tasks`。不需要模型密钥，也不会发送到模型服务。数据库包含群聊业务文本，请仅使用测试群数据并按公司规则限制访问与清理。

## 图片与正式接入的边界

本轮只能识别 image 类型，不能取得原图。上游现有图片保存使用窗口/菜单/剪贴板，尚不满足后台原图要求，因此没有包装成“下载成功”。待实机证明最小化、焦点、身份、原图和重启行为符合要求后，再决定修正上游或保留为人工辅助工具。正式引擎仍使用现有严格身份模型和共用 Decimal 规则，不能用显示名/UI ID/接收时间替换稳定标识后静默接线。
