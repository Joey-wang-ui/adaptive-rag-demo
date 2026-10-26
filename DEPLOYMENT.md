# Render 网页演示部署

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

原 `Dockerfile` 保留不变，它会安装额外的模型依赖；本次 Blueprint 不使用它。向真实模型升级应作为后续独立步骤，并重新验证生成、路由和判断行为。

当前 API 没有完整身份认证，会话隔离和人工确认也存在原项目已披露的限制。仅作隔离的个人演示，不应对不可信用户开放，不输入敏感信息，不连接真实业务库。容器中的 SQLite 不作持久性保证；限额、休眠和计费以 Render 页面为准。

Docker 监听 `0.0.0.0`，端口使用 Render 提供的 `PORT`，未提供时为 8000。前端、知识库均已打入镜像，不包含 `.env`、本地聊天数据库或 API Key。

`Dockerfile.demo` 中可选的 `proxy_ca` 构建挂载用于 Codex 云环境的受信代理 CA；Render 上无需提供。未关闭 TLS 或包完整性校验。
