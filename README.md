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
