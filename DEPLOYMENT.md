# 本地启动与可选网页托管

## Windows 本地启动

1. 先安装 Python 3.12（含 `py` 启动器）与 Node.js 24 LTS。
2. 下载 GitHub ZIP 并完整解压，双击 `start-local.cmd`。不要在 ZIP 压缩窗口中直接运行。
3. 第一次运行会自动安装 Python 开发依赖、执行 `npm ci` 和前端构建。完成后自动打开浏览器，点击右下角 Agent 助手即可聊天。
4. 如果已有旧版项目，先备份个人 `.env` 和 `data/local`，将新版解压到另一个新目录，避免覆盖个人文件。本启动器不写入或复制 `.env`，不包含密钥，不改变已有凭据。
5. 启动器默认关闭本地 Embedding、Cross-Encoder 和 Unstructured，不下载模型权重；没有生成模型凭据时使用离线资料节选。已有远程模型配置仍会按应用原规则使用。
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
