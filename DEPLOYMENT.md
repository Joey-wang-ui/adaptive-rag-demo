# 本地启动与可选网页托管

## Windows 本地启动

1. 先安装 Python 3.12（含 `py` 启动器）与 Node.js 24 LTS。
2. 下载 GitHub ZIP 并完整解压，双击 `start-local.cmd`。不要在 ZIP 压缩窗口中直接运行。
3. 第一次运行会自动安装 Python 开发依赖、执行 `npm ci` 和前端构建；每次启动前在独立的 `data/local/launcher-demo.db` 中补齐演示商品和订单 `ORD10001`–`ORD10003`。完成后自动打开浏览器，点击右下角 Agent 助手即可聊天。演示物流不代表真实快递查询。
4. 如果已有旧版项目，先备份个人 `.env` 和 `data/local`，将新版解压到另一个新目录，避免覆盖个人文件。本启动器不写入或复制 `.env`，不包含密钥，不改变已有凭据。
5. 启动器默认关闭本地 Embedding、Cross-Encoder 和 Unstructured，不下载模型权重；没有生成模型凭据时使用离线资料节选。已有远程模型配置仍会按应用原规则使用。本地启动进程强制使用上述独立 SQLite 文件、内存缓存和内存检索，不使用 `.env` 中的数据库、Redis 或 Qdrant 地址；不改写 `.env` 或原数据库。种子只插入缺失行，不重置已经取消等订单状态。
6. 服务只监听本机 `127.0.0.1`，自动在 8000–8020 中选择空闲端口，网页和 API 由同一服务提供；没有公网网址。保留命令窗口，按 Ctrl+C 退出。服务器日志在 `logs/local-server.log`。
7. 之后可继续双击同一个脚本启动。项目依赖声明和锁文件未变化时复用已安装依赖；网页每次重新构建。安装失败会停留在窗口显示错误，不关闭 TLS 验证，不修改系统执行策略。

启动器的 Python 检查与服务启动逻辑已在 Codex Linux 环境验证；Windows CMD 的实际双击流程仍需在用户 Windows 电脑确认。

当前采用 Windows 本地运行。以下云端配置仅作为备选，尚未验证公网部署成功。Docker Space、免费硬件和账号验证是否可用，以账号实际页面为准；若要求订阅或绑卡，可以继续使用本地版本。

## Hugging Face Docker Space

1. 注册或登录 https://huggingface.co ，完成账号要求的邮箱验证。
2. 打开 https://huggingface.co/new-space ，名称可用 `adaptive-rag-demo`。
3. SDK 选择 Docker，模板选择 Blank（如显示此选项）；硬件如有免费的 CPU Basic 可选择，公开演示选择 Public。若 Docker SDK 标为付费或要求订阅 PRO，先停止，不要为本演示开通收费服务。
4. 创建后，将本仓库内容上传到 Space。可下载 https://github.com/Joey-wang-ui/adaptive-rag-demo/archive/refs/heads/main.zip ，解压后进入 `adaptive-rag-demo-main`，上传其中的文件和文件夹，不要把这一层目录套在外面。
5. 在 Space 的 Files 页面确认根目录直接包含 `Dockerfile`、`README.md`、`pyproject.toml`、`app/`、`frontend/`、`knowledge/`，并包含 `.dockerignore`。README 原有的 YAML 元数据应保留，`sdk: docker`、`app_port: 8000`。
6. 提交文件后会触发构建，查看 Logs；等待显示 Running 后打开 App。Dockerfile、README 配置端口统一为 8000；无需添加 PORT、API Key 或模型下载配置。

上传的是代码，不包括 GitHub 的历史、本地 `.env` 或数据库。也可以通过 Hugging Face 官方 Git/Hub API 上传，需账号拥有写入目标 Space 的权限；不要将访问令牌写入代码或聊天。

仓库里的 `Dockerfile` 与 `Dockerfile.demo` 当前相同，均为轻量离线镜像，支持用户 ID 1000。上游较重的镜像配置保留在 `Dockerfile.models`，本演示不使用它。

## Ollama 本地生成模型

1. 安装 https://ollama.com/download/windows ，保持其本地服务运行。
2. 双击根目录 `start-local-model.cmd`。启动器会复用或启动本机 `127.0.0.1:11434` 的 Ollama，下载缺失的 `qwen2.5:3b`，进行一次真实的 JSON 生成测试，然后打开聊天网页。默认模型下载约 2GB，运行还需要模型、上下文缓存及系统内存；CPU 推理可能较慢。
3. 也可在 CMD 中执行 `start-local-model.cmd qwen2.5:1.5b` 选择较小模型；选择以本机内存和性能为准，不保证小模型的复杂路由、规划或裁判质量。
4. 使用 Ollama 原生 `/api/chat` 接口和 JSON 格式，不借用 DeepSeek 密钥、不写 `.env`。仅本次应用进程设置 `LLM_BACKEND=ollama`、模型名称、4096 上下文预算。现有远程生成配置不会在此模式下被选择。Embedding 和重排仍是轻量降级模式，不宣称完整真实向量 RAG。
5. 模型和 JSON 预热检查失败时停止启动并显示错误，避免把离线回复标成模型接入成功。运行期间个别节点调用失败仍遵循原项目的规则/节选降级，需检查日志和实际回答质量。
6. 默认不对公网开放 Ollama；复用已有本地服务时不会关闭它。仅当启动器自己启动 Ollama 时，退出启动器会清理该子进程。
7. 当前测试覆盖原生 API 请求格式、用量解析、未安装模型与错误响应等模拟情况，以及原有离线路径回归。Windows Ollama 安装、真实权重下载、真实生成质量需在用户电脑验证，不能视为已验证通过。

手动配置应用时，可设置 `LLM_BACKEND=ollama`、`OLLAMA_BASE_URL=http://127.0.0.1:11434`、`OLLAMA_MODEL=qwen2.5:3b`，然后运行 Uvicorn。默认 `LLM_BACKEND=auto` 保留原 DeepSeek/离线选择逻辑。

### 模型下载遇到 Fake-IP / non-public 错误

若 Ollama 报 `redirect target not allowed ... resolves to non-public 198.18.x.x`，可关闭失败的启动窗口，双击 `repair-model-download.cmd`。此入口仅下载官方 `qwen2.5:3b`：通过 Python 标准 HTTPS 下载器（支持 Windows 系统代理）读取官方清单，限制跳转到官方仓库/CDN，逐文件校验清单中的大小与 SHA-256，再将清单原子写入 Ollama 模型目录，随后调用原模型启动器做真实 JSON 生成检查。默认目录为 `%USERPROFILE%\.ollama\models`，如设置 `OLLAMA_MODELS` 则使用该目录；Ollama 服务必须使用相同目录及 Windows 用户。修复时不要同时用其他窗口下载同一模型。

不修改系统 DNS、hosts 或代理设置，不关闭 TLS 验证，不下载其他模型，不清除已有模型。网络中断保留 `.repair-part` 分片供下次续传；校验失败不发布新清单。正常的网络/代理连接仍然需要可用，若官方地址不可达仍会失败。只有修复后的原启动器真实生成检查通过，才确认模型可用。备用下载路径的模拟测试不等于已在用户 Windows 网络完成真实模型下载。

## Render（备选）

本仓库保留 JEen-wang/Adaptive-rag 原始实现与 MIT 许可证，增加聊天弹窗和独立部署配置。首版验证网页、检索、API 和 SSE 的运行链路，不宣称真实模型效果。

## 部署

1. 登录 Render，打开仓库 README 顶部的 Deploy to Render 按钮。
2. 按界面要求授权 Render 读取 `Joey-wang-ui/adaptive-rag-demo`。
3. 检查 Blueprint：服务类型 Web Service，Dockerfile 为 `Dockerfile.demo`，计划 Free，健康检查为 `/api/v1/health`。
4. 不需要配置 Render API Key、DeepSeek Key、Redis 或 Qdrant。若提示收费或 Free 不可用，先停止确认，不要直接选择付费计划。
5. 创建服务并查看构建日志。等待 Render 显示服务已上线后，使用平台实际提供的网址，不要自行猜测子域名。

如果使用手动创建 Web Service，选择本仓库和 `main` 分支、Docker 运行时，将 Dockerfile Path 设置为 `./Dockerfile.demo`；其余同上。

## 部署后验证

- 打开网页，检查右下角的「Agent 助手」，发送「你好」。
- 发送政策问题，检查是否收到回答和引用；离线结果应明确说明未配置生成模型。检索链路可运行不代表返回的证据一定相关。
- `/api/v1/health` 应返回 `{"status":"ok"}`。`/api/v1/ready` 不能证明真实模型可用，原实现可能将离线 LLM 标成 configured。
- 前后端由同一个 FastAPI 服务提供，网页在根路径 `/`，流式接口为 `/api/v1/chat/stream`，无需再部署 Static Site。
- 订单演示数据默认不自动初始化；没有查到订单不等于服务未启动。

## 当前模式与边界

`Dockerfile.demo` 安装基础 Python 依赖，构建 React 静态文件，关闭本地 Embedding、Cross-Encoder、Unstructured 和 MCP，使用 SQLite、内存索引、哈希向量及词面重排。不下载模型。

原模型镜像配置保存在 `Dockerfile.models`，它会安装额外的模型依赖；本次演示不使用它。向真实模型升级应作为后续独立步骤，并重新验证生成、路由和判断行为。

当前 API 没有完整身份认证，会话隔离和人工确认也存在原项目已披露的限制。仅作隔离的个人演示，不应对不可信用户开放，不输入敏感信息，不连接真实业务库。容器中的 SQLite 不作持久性保证；限额、休眠和计费以 Render 页面为准。

Docker 监听 `0.0.0.0`，端口使用 Render 提供的 `PORT`，未提供时为 8000。前端、知识库均已打入镜像，不包含 `.env`、本地聊天数据库或 API Key。

`Dockerfile.demo` 中可选的 `proxy_ca` 构建挂载用于 Codex 云环境的受信代理 CA；Render 上无需提供。未关闭 TLS 或包完整性校验。
