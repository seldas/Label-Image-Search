# Labeling Image Query (DailyMed SPL)

离线本地化药品说明书图片（Labeling Image）检索系统：支持**以图搜图、以文搜图、图文混合检索**，结果以 **3×3 九宫格分页** 展示。

## 架构特点

- **纯 CPU Docker 部署**：无需独立显卡 / CUDA 环境，通过 Docker Compose 一键运行 `frontend`、`backend`、`worker` 三个服务。
- **数据完全本地化**：
  - `worker` 容器负责首次全量下载 DailyMed SPL Release，之后每周自动拉取上一周的 Weekly Update ZIP 并增量更新本地库。
  - 流式解压（不把上百 GB 压缩包全量解压到磁盘），自动提取 SPL XML 上下文、pHash 图片去重、生成 `300×300` WebP 缩略图。
  - 本地使用 **PostgreSQL + pgvector** 统一存储元数据、全文检索 (`tsvector`) 与 SigLIP 多模态向量（HNSW 索引）。
- **内网 vLLM (`gemma-4-31b`) 协同**：
  - 用户搜索时**不连外网**，仅调用内网 vLLM 接口做中文/模糊描述理解、图像 OCR/特征提取与可选精排。
  - 翻页采用搜索会话缓存（默认缓存 Top-90 即 10 页），翻页零延迟。

## 快速部署（目标 Linux 服务器）

1. 复制环境变量模板并修改内网 vLLM 地址：
   ```bash
   cp .env.example .env
   # 编辑 .env 中的 VLLM_BASE_URL, VLLM_MODEL 等配置
   ```

2. 创建持久化目录：
   ```bash
   mkdir -p data/db data/images data/downloads models
   ```
   > **离线环境提示**：如果目标服务器完全无法访问 HuggingFace，可提前在有网机器下载 `google/siglip-base-patch16-224` 放到 `./models/siglip-base-patch16-224`，并将 `.env` 中的 `EMBEDDING_MODEL` 设为 `/app/models/siglip-base-patch16-224`。

3. 一键启动所有容器并自动同步数据库表结构（含新增字段迁移）：
   ```bash
   python3 start_server.py
   ```
   > 后续代码更新后，重新执行 `python3 start_server.py` 即可自动重建容器并执行 `sync_db` 迁移新字段。

4. 访问前端：打开浏览器访问 `http://<server-ip>:8845`。

5. 手动触发同步（可选）：
   ```bash
   # 手动触发一次增量同步
   docker compose exec worker python -m app.worker --run-once weekly
   # 手动触发一次全量同步
   docker compose exec worker python -m app.worker --run-once full
   ```

## RESTful API (v1) 第三方接入

系统同时暴露了无状态、带静态 API Key 鉴权与限流保护的 `/api/v1` 接口供第三方服务调用（在线文档：`http://<server-ip>:8846/docs`）。

### 1. 配置 `.env`
```env
API_KEYS=liq_team_a_secret,liq_team_b_secret
API_REQUIRE_KEY=true
API_RATE_LIMIT_RPM=60
# 可选：指定返回图片链接的外部基础地址；留空时自动从请求头推导
PUBLIC_BASE_URL=http://<server-ip>:8845
```

### 2. JSON 图文查询（`POST /api/v1/search`）
支持请求头 `X-API-Key: <key>` 或 `Authorization: Bearer <key>`，返回包含完整 `thumb_url` / `orig_url` 链接的结果列表：
```bash
curl -X POST "http://<server-ip>:8845/api/v1/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: liq_team_a_secret" \
  -d '{"text": "white oval tablet scored L484", "top_k": 9, "use_ai": true}'
```

### 3. Multipart 文件上传查询（`POST /api/v1/search/upload`）
```bash
curl -X POST "http://<server-ip>:8845/api/v1/search/upload" \
  -H "Authorization: Bearer liq_team_a_secret" \
  -F "text=blue capsule" \
  -F "image=@/path/to/query.jpg" \
  -F "top_k=9"
```

### 4. Python 调用示例
```python
import base64
import requests

with open("pill.jpg", "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode("utf-8")

resp = requests.post(
    "http://<server-ip>:8845/api/v1/search",
    headers={"X-API-Key": "liq_team_a_secret"},
    json={"text": "white tablet", "image_base64": img_b64, "top_k": 9, "use_ai": True},
    timeout=30,
)
resp.raise_for_status()
data = resp.json()
for item in data["items"]:
    print(item["product_name"], item["score"], item["orig_url"])
```
