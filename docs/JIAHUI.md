# 嘉惠内部业务助手第一版

**日常使用请下载[Windows完整软件包](https://github.com/Joey-wang-ui/adaptive-rag-demo/releases/download/jiahui-desktop-v1.1.1/JiahuiAssistant-Windows-x64.zip)，完整解压后双击 `JiahuiAssistant.exe` 或 `START.cmd`。首次在软件窗口里创建账号、填写Key，以后直接打开使用。无需安装Python/Node，也不用执行下面的源码部署命令。**

桌面版操作见[使用说明](../desktop/使用说明.md)。以下内容主要面向源码开发与维护。

用于内部产品资料查询和业务知识问答。价格、库存、订单只预留只读接口，未连接管家婆时统一说明“尚未接入业务数据”；第一版不执行取消订单、退款或库存扣减。嘉惠为默认业务模式，微信审单启动器保留。

开发接入点为 `app/jiahui/business.py` 的 `ReadOnlyERP` 协议和 `UnconnectedERP` 默认实现，仅允许 `prices`、`inventory`、`orders` 查询，并接收服务端认证后的范围。后续应根据实际管家婆产品版本，使用厂商支持的 API 或经过授权的只读中间件替换适配器；不能把历史报价导入为当前成交价，也不应绕过岗位权限直接访问数据库。

## GitHub 源码包

下载固定版本：[jiahui-assistant-v1.0.0.zip](https://github.com/Joey-wang-ui/adaptive-rag-demo/archive/refs/tags/jiahui-assistant-v1.0.0.zip)。这是源码包，首次运行会安装依赖并构建网页，需要Python 3.12、Node.js和网络；不是免安装EXE。

GitHub包只包含代码、模板、演示格式和操作说明。已有本地部署的`.env`、账号及产品资料应先备份，再更新代码；这些文件没有上传GitHub。新解压目录需要自行初始化账号，没有默认密码。DeepSeek Key继续通过本地环境变量或未跟踪的`.env`安全配置。

## 启动

在仓库根目录操作。Windows 推荐已安装的 Python 3.12、Node.js 24；Linux 使用相同 Python 脚本。

第一次先安装依赖并构建网页：

```bat
start-jiahui.cmd --setup-only
```

有 `uv` 时使用仓库 `uv.lock` 冻结安装；没有时兼容使用 `pip install -e ".[dev]"` 并明确提示该路径不冻结 Python 依赖。前端执行 `npm ci`，不会修改锁文件。后续通过依赖指纹复用安装结果，网页会重新构建以包含代码更新。

初始化自己的管理员账号，密码至少12位，在终端隐藏输入，不要放进命令行、脚本或聊天中：

```bat
.venv\Scripts\python.exe -m app.jiahui.auth create-user --username jiahui-admin --role admin
start-jiahui.cmd
```

账号已经存在时初始化不会覆盖密码或权限。首次打开页面后使用刚创建的账号登录。默认只监听 `127.0.0.1`，自动选择 8000—8020 的空闲端口，保留窗口，按 Ctrl+C 停止。日志位置为 `logs/jiahui-server.log`。

已完成安装与构建后，可快速启动并指定端口：

```bat
start-jiahui.cmd --skip-setup --port 8000
```

Linux 对应命令：

```bash
python3 scripts/start_jiahui.py --setup-only
.venv/bin/python -m app.jiahui.auth create-user --username jiahui-admin --role admin
python3 scripts/start_jiahui.py --skip-setup --no-browser --port 8000
```

`--check` 只检查 Python、Node 和项目文件，不安装、不启动。Windows `.cmd` 在 Linux 无法实机验证；它调用与 Linux 相同的 `scripts/start_jiahui.py`。

## 账号与岗位范围

支持 `sales` 销售、`procurement` 采购、`finance` 财务、`warehouse` 仓储、`logistics` 物流和 `admin` 管理员。业务岗位默认只能访问 `products,knowledge`。管理员可配置范围 `products,knowledge,prices,inventory,orders,audit`；授予范围不代表已接入相应业务数据。

也可以为资料设置自定义范围标签，例如给采购专用产品记录填“访问范围”为 `procurement`，仅给应当阅读的账号增加该范围。标签与角色名称没有隐式映射，必须由管理员明确分配；修改权限后立即应用。管理员默认的六个范围也不会自动获得所有自定义标签。

```bat
.venv\Scripts\python.exe -m app.jiahui.auth create-user --username sales01 --role sales --scopes products,knowledge
.venv\Scripts\python.exe -m app.jiahui.auth set-access --username sales01 --role sales --scopes products
.venv\Scripts\python.exe -m app.jiahui.auth create-user --username buyer01 --role procurement --scopes products,knowledge,procurement
```

禁用账号在 `set-access` 后加 `--disable`。权限变更后服务端重新读取账号权限，不依赖前端传入的 `user_id`。身份数据库位于 `data/jiahui/auth.sqlite3`，请备份并限制该目录的操作系统访问权限。不要提交身份数据库、业务资料、备份或 `.env`。

## 产品目录与来源

嘉惠资料独立放在 `data/jiahui/`，不会读取电商 `knowledge/`、演示订单数据库或原有商品数据。正式产品尚未导入时可以正常启动，页面显示“尚未录入产品资料”。

使用 `docs/jiahui/` 中的 UTF-8 CSV 模板和中文字段说明。`demo-only/` 仅为演示格式，不能当作正式商品。商品编号必须按文本保存，保留 `06401`、`074` 等前导零；不同商品可以使用相同编号。以稳定的“内部记录ID”区分记录，以“内部记录ID + 版本”更新资料，不把商品编号作为唯一键。

建议将待导入 CSV 放在 `data/jiahui/incoming/`，原件放在 `data/jiahui/sources/`。PDF 记录必须填写页码或章节及原表行号；表格导入器还会自动保存 CSV 行号及输入文件副本。原文缺少的品牌、储温、保质期、单位、有效日期可以留空，不能按品类补全。

先导入草稿核查：

```bat
.venv\Scripts\python.exe -m app.jiahui.import_catalog --csv data/jiahui/incoming/products.csv --data-dir data/jiahui --status draft
```

业务负责人确认资料可作为现行依据后，再导入为现行版本：

```bat
.venv\Scripts\python.exe -m app.jiahui.import_catalog --csv data/jiahui/incoming/products.csv --data-dir data/jiahui --status current --scope products
```

CSV 的“状态”“访问范围”列如有填写，会优先于命令行默认值；请在核查时一并确认。`draft`、`historical` 和尚未生效的记录不会被普通现行查询作为依据。新版本沿用同一个内部记录ID并修改版本；导入新的现行版本时保留旧版本并标记历史。相同内容重复导入不重复新增。修改前自动保存 `data/jiahui/backups/` 备份，采用临时文件和原子替换；发生错误会停止并报告，不悄悄丢弃错误行。

更新导入后重新启动嘉惠服务，使运行中的资料视图和索引一致。备份恢复前停止服务并另存当前文件，再恢复经核查的 `products.json` 及对应来源。不要绕过导入器直接修改运行中的 JSON。

上传的《杭州嘉惠目录表2026年4月修改(2)(1).pdf》含重复编号、多规格、缺失单位和部分储存条件冲突；文件名中的日期不能证明当前有效。保留原始 PDF 作为来源，核对后再形成正式 CSV，不把未经复核的自动抽取当作确定产品主数据。

## 模型与降级状态

启动器保留已有 `.env` 和进程环境中的 DeepSeek、Ollama、Embedding、重排序配置，不写入或打印密钥，不使用原电商启动器中强制关闭模型的参数。云环境已登记 `DEEPSEEK_API_KEY` 配置需求，请在环境设置中安全填写，目标为 `api.deepseek.com`，通过平台代理访问。本地部署可以复用未跟踪的 `.env` 或进程环境变量；密钥不应发到聊天、提交到仓库或写入命令参数。

健康检查的 Python `ping` 可能只证明已配置 Key，不能证明真实生成可用。应运行实际操作检查：

```bat
.venv\Scripts\python.exe -m scripts.check_jiahui_models
```

它实际尝试一次小型 JSON 生成、两段 Embedding 和重排序预测，并只输出后端类型和成功状态。设置了 API Key 不等于生成成功；`hash` 是确定性向量降级，不是真实语义 Embedding；`lexical` 是词法排序，不是 Cross-Encoder；`offline_excerpt` 是资料摘录，不是生成式回答。云端新 checkout 不会自动带来用户电脑上的密钥或本地模型权重。

默认仅使用本机已有缓存权重，不自动下载大模型。需要主动安装真实本地 Embedding/重排序时，可另行安装项目 `rerank` 可选依赖，并在确认模型来源和网络可用后显式设置 `HF_HUB_OFFLINE=0`、`TRANSFORMERS_OFFLINE=0`。远程 API 请求始终保留原有代理和 TLS 校验；不要关闭证书验证。

## 查看错误与切回电商

- 启动错误：查看终端和 `logs/jiahui-server.log`。导入错误直接显示文件/行号；不应把真实密码、Key 或完整业务数据贴到公开日志。
- 401：账号尚未初始化、密码错误或会话到期，重新登录；403：由管理员确认岗位和资料范围。
- 多规格或重复编号：根据返回候选确认具体商品，不自动选择第一条。
- 没有资料依据：补充有来源的资料并确认版本，不让模型猜测。

### 附录：保留的原模式

本次交付与默认试用流程聚焦嘉惠业务，原电商模式不作为嘉惠功能验收项。需要使用保留的原演示时，停止嘉惠窗口，先设置 `BUSINESS_PROFILE=ecommerce`，再使用原来的 `start-local.cmd`、`start-api.cmd` 或已有启动方式。若自行在系统设置过 `BUSINESS_PROFILE=jiahui`，显式改成 `ecommerce`。微信审单仍使用其原有启动器。

## 食品原料示例问法

以下只有问题，没有预设标准答案；实际结果取决于已导入且当前账号有权访问的资料。

- 编号06401是什么产品？请附来源页码。
- 编号1564匹配到哪些商品？请先列出候选让我确认。
- 天成蛋黄液有哪些已录入规格？
- 我说的这个别名对应哪款产品？
- 比较这两款产品的规格、储存条件和产地，并分别给出来源。
- 这条资料是什么版本，是否已确认现行有效？
- 目录是否明确写了这款产品的保质期？
- 原文只写“冷藏”，有没有明确的温度范围？
- 970*12盒/箱中的970有明确计量单位吗？
- 这两款原料是否有经过确认的替代适用性资料？
- 已确认每箱12盒时，3箱是多少盒？请引用换算依据。
- 这款产品目前库存、当前成交价和订单状态是什么？

最后一类问题在管家婆尚未连接时只能回答“尚未接入业务数据”；历史报价不能当作今天的成交价。

## 本地资料准备情况（不随 GitHub 源码发布）

开发环境中的用户PDF原件及逐页文本在 `data/jiahui/sources/catalog-2026-04/`；此类业务文件未提交Git，也不包含在GitHub源码ZIP中。下载源码后的空目录可以正常启动，请按资料导入说明准备自己的目录。正文第4—22页已导入 `knowledge/manifest.json` 为 reference，权限标签 products；引用保留原PDF文件名、PDF页码和“现行有效性待确认”版本标记，不当作现行制度。

`data/jiahui/imports/杭州嘉惠目录_待复核.csv` 是从标准六列表格提取的324条**草稿候选**。异常报告在同目录，标出重复编号、缺单位、多规格和表格跳过情况。第6—7页双栏馅料与第19页非标准表未机械拆成SKU，仍可查阅参考原文。候选CSV不等于人工确认，品牌、保质期、单位换算等未提供字段保持空白。

候选导入只作为草稿存档，不进入现行产品查询；复核后需将相应行的“状态”改为current、确认版本/生效日期和访问范围，再运行产品导入命令。不会因为传入 `--status current` 就覆盖CSV中显式填写的draft状态。

```bash
python -m app.jiahui.import_catalog --csv data/jiahui/imports/杭州嘉惠目录_待复核.csv --data-dir data/jiahui --status draft
```

CSV对比和换算回答为程序根据结构化字段生成；知识问答在DeepSeek可用时让模型选择相关原文并逐字校验，不允许模型扩写未知食品属性，校验失败则明确退回原文节选。

实际浏览器验证脚本：`python scripts/smoke_jiahui_browser.py`（需要系统Python安装Playwright并提供Chromium；应用使用项目.venv）。脚本只用TemporaryDirectory演示数据和随机测试账号，不修改正式嘉惠目录；结果写入 `logs/jiahui-browser-smoke.json`。
