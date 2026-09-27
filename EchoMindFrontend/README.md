# EchoMind Frontend

EchoMind Python 后端的独立 Vue 调试界面。

项目目录：

```text
./EchoMindFrontend
```

## 功能

- 聊天调试、健康检查、监控摘要。
- 知识库检索、文档导入、文件上传。
- Skills 查看与热加载。
- 端到端评测运行。
- 工具调用 trace 查看。
- 支持 Docker + Nginx 部署。

## 接口字段

响应按 Python 后端的 snake_case 字段解析：`conv_id`、`request_id`、`agent_type`、`primary_agent`、`routing_confidence`、`latency_ms`、`knowledge_used`。

## 后端地址

前端只请求同源的 `/api` 前缀，实际后端由部署方式决定：

| 运行方式 | `/api` 指向 | 配置入口 |
|----------|-------------|----------|
| `npm run dev` | `http://localhost:8000` | `API_PROXY_TARGET`（`vite.config.js` 代理） |
| 单容器 nginx | `http://host.docker.internal:8000` | `API_URL`（`docker/entrypoint.sh` 注入） |
| 网关 compose | `http://echomind-python:8000` | `docker/nginx-gateway.conf` |

运行时优先级：`window.__ECHOMIND_CONFIG__.apiUrl` → `VITE_API_URL` → `/api`。

## 本地运行

```bash
npm install
npm run dev
```

访问 `http://localhost:5173`。后端端口不是 8000 时：

```bash
API_PROXY_TARGET=http://localhost:8000 npm run dev
```

## Docker 部署

```bash
docker compose up -d --build
```

前提是 `EchoMindFrontend` 的父目录下存在这两个目录：

```text
../EchoMind
./
```

访问 `http://localhost`（网关入口）或 `http://localhost:5174`（前端容器）。停止：

```bash
docker compose down
```

## 后端启动参考

Python 版默认 `http://localhost:8000`，详见 `../EchoMind/README.md`。
