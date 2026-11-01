# FreeWisdom/wxauto-4.0 接入评估

继续开发状态：已提供 [Windows 只读试验步骤](WXAUTO4-TRIAL.md)、固定提交隔离副本准备器和被动诊断入口；未启用生产接入，Windows 实测仍未执行。

评估日期：2026-11-01。用户指定仓库：<https://github.com/FreeWisdom/wxauto-4.0>。
核对的完整提交：`bd7c5233e79c0a185638a325bf6d30607244dfa8`。

结论：可作为 Windows UI 自动化的对照候选，**当前不能替代满足“最小化继续收消息、不抢焦点、可靠原图与稳定身份”的正式只读入口**。本次只拉取临时副本并静态核对，没有安装、导入、构造 WeChat 实例，没有操作用户微信或发送消息。运行机器为 Linux，无法提供 Windows 实机结果。

## 实际分发与授权

- `pyproject.toml` 声明包名 `wxauto4`、版本 `40.1.1`、Python `>=3.9,<3.14`，依赖 `pywin32`、`uiautomation`、`comtypes` 等 Windows 组件。这不是此前的 wx4auto，也不是 `wechatauto-replica`。
- README 只声明支持微信 **4.0.5**，不能据此推断支持 4.2.0.6 或当前用户客户端。
- 当前提交没有找到项目 LICENSE/COPYING 文件，`pyproject.toml` 也没有 license 字段；另有授权码/机器指纹相关工具，但这不能替代明确的软件使用许可。项目正式集成前需向维护者确认使用与分发条款。
- README 的 pip 与源码安装示例指向 `wxauto4` / `cluic/wxauto4`；不能声称 `pip install wxauto4` 安装的内容等同于用户指定的 FreeWisdom fork。后续测试应固定上述提交并重新核验，不盲装浮动同名包。

## 当前源码证据

以下链接固定到核对提交，避免 README 或分支后续更新改变结论。

| 原要求 | 核对结果 | 证据 |
| --- | --- | --- |
| 最小化仍持续收消息 | README 明确列为已知问题：窗口被最小化/关闭可能停止响应；尚未通过验收 | [README 已知问题](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/README.md) |
| 不占鼠标键盘/焦点 | `AddListenChat` 会打开独立聊天窗口；窗口创建可能切换聊天、搜索和点击，不能保证无焦点变化 | [wx.py](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/wxauto4/wx.py)、[ui/main.py](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/wxauto4/ui/main.py)、[ui/sessionbox.py](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/wxauto4/ui/sessionbox.py) |
| 稳定消息 ID | `BaseMessage.id = control.runtimeid`；不是已验证的微信服务端消息 ID。hash 使用控件尺寸和文本，不含客户/订单唯一性；重复消息可碰撞，UI 重建也可能改变标识 | [msgs/base.py](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/wxauto4/msgs/base.py) |
| 发送者 | UI 控件树读取群成员显示名，失败时有 OCR 回退；不能将显示名冒充稳定成员 ID，同名成员仍有混淆风险 | [msgs/mattr.py](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/wxauto4/msgs/mattr.py) |
| 真实发送时间/真实 @ | 当前公开消息字段未提供已核验的稳定发送时间和真实 @ ID 列表；不得以本地接收时间替代发送时间，也不得解析 `@昵称` 后声称是真正 @ | [消息字段文档](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/docs/api/message.md)、[消息基类](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/wxauto4/msgs/base.py) |
| 接收发货单原图 | `ImageMessage` 当前只有消息类型/构造方法；另有图片窗口 `WeChatImage.save`，使用菜单“复制”、剪贴板读取及 Esc，不是无界面后台原图下载接口 | [msgs/mtype.py](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/wxauto4/msgs/mtype.py)、[ui/component.py](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/wxauto4/ui/component.py) |
| 重启补拉与不重不漏 | 当前监听从聊天窗口轮询，无已核验的服务端游标或完整历史补拉保证；已有项目收件箱的幂等能力不能修复上游遗漏或不稳定 ID | [wx.py](https://github.com/FreeWisdom/wxauto-4.0/blob/bd7c5233e79c0a185638a325bf6d30607244dfa8/wxauto4/wx.py) |

文档中对 `KeepRunning()` 的描述与代码有差异：当前函数主体只是等待/处理 Ctrl+C，不能根据文档声称它已实现稳定的后台保活或焦点修复。

## 初始化的额外副作用

`WeChat.__init__()` 首先调用 `delete_update_files()`，监听循环每轮也会调用该函数；后者删除用户目录 `AppData/Roaming/Tencent/xwechat/update` 下的文件/子目录。即使仅准备收消息，直接构造实例也不符合本项目对只读阶段的预期，因此源码评估时没有执行；后续试验准备器已在隔离副本中将该函数替换为空操作，覆盖两种调用。

静态检查的当前业务代码未发现直接调用微信数据库解密或 `ReadProcessMemory` 的实现；不能据此保证所有依赖或未来版本均无敏感行为。仓库 `AUDIT.md` 还提及一个历史 `parse.py`/数据库密钥风险，但当前提交中未找到该文件，不能把该历史清单当作当前代码确实执行了解密的证据。如果后续包或依赖发现解密/内存读取，仍须先报告并取得用户明确批准。

## 后续可行路线

1. 当前继续使用已实现的共用审核引擎、工作台 API 和只读回放，不降低原验收要求。
2. 在 Windows 做对照实验前，确认实际微信版本与使用许可；先在隔离分支修正上游初始化删除更新文件的副作用并审查 diff，再在授权测试群手动启动。不要因自动化工具只支持旧版而默认降级用户微信或安装第三方客户端。
3. 实验只采集诊断数据：群显示名、发送者显示名、UI runtime ID、接收时间、消息类型/文本分别注明来源；不把它们伪装成现有严格消息模型所需的稳定账号/群/成员/消息 ID 与发送时间。没有可靠身份、时间和原图时，不自动推进订单审核。
4. 不调用 `SendMsg`、`SendFiles`、`quote`、转发或仓库的自动回复 workflow。测试原图下载还需单独记录窗口、焦点和剪贴板变化。
5. 若最小化、不抢焦点、原图和身份无法通过实测，将此库限定为人工可见的辅助工具，正式无人值守入口保留给能满足要求的方案。

本次结果是**完成候选源码评估，实时接入未启用**，不是 Windows 实机测试通过。
