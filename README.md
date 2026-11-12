# rfmseg · 拾光咖啡会员 RFM 分群与触达预算策略台

一句话：把「按累计消费三档群发券」换成可解释、可复现的 RFM 分层，并在同一张剖面图里把预算分到最值的那一层。

## 1. 真实场景

- **客户**：拾光咖啡连锁 · 会员运营 6 人。8 家门店、12 万会员（演示 600 人）、每月 POS 订单约 18 万笔（演示 2,400 笔）。
- **现有工作流**：运营按「累计消费 >5000 / 1000–5000 / <1000」三档发同一张券，群发后只看总核销数；分群口径在 Excel 里手改，下次无法复现。
- **三个痛点**：
  1. 一刀切分档，无法解释「谁不来了」；
  2. 券预算固定 8,000 元，不知道给哪一档最值；
  3. 分群结论不可复现（Excel 手改、没有随机种子与口径版本）。
- **上下游**：上游 = POS 订单事实 + 会员授权资料 → 本系统替代「人工拍分档 + 群发」 → 下游 = 门店券核销、企微触达、月度复盘。
- **POS 补传口径**：门店会补传退款单、撤销单与迟到订单。`order_facts` 以事件溯源方式保存全部事件（原单永不覆盖、永不删除）；会员分析只认**统计日 as-of 及之前的有效订单**——撤销单整笔剔除（F/M 同时冲减），退款单只冲减金额 M、不冲减订单数 F，统计日之后的迟到单/更正不进入该 as-of 视图，同一 `correction_no` 的更正重复投递只生效一次。
- **角色权限**：

| 角色 | 账户 | 主要页面 | 权限 | 典型动作 |
|---|---|---|---|---|
| admin 运营主管 | `admin@rfmseg.io` | 全部 | 分群参数、策略与预算、发布、审计 | 配置 8,000 元预算并发布 |
| analyst 会员分析师 | `analyst@rfmseg.io` | 分群 / 明细 / 名单 | 跑分群、看画像、调参、导出名单 | 以 as-of 2026-11-12 重跑 k 扫描 |
| viewer 店长 | `viewer@rfmseg.io` | 全部只读 | 只读结论与名单、下载已有导出 | 查看「高价值沉睡」名单 |

三个账号密码均为 `Demo@2026`；viewer 调写接口会收到真实 **HTTP 403**（后端 RBAC，非前端隐藏）。

### 一天的工作剧本

1. 09:10 分析师用 as-of `2026-11-12` 跑分群，得到 k=5、轮廓系数约 0.44；
2. 09:20 在总览看到「高价值沉睡」78 人，GMV 占比 36%；
3. 09:35 打开 RFM 明细，确认沉睡层 R 均值 1.0 分、M 均值 9,000 元；
4. 10:00 运营主管在策略页填预算 8,000 元，生成三档券面额（40 / 25 / 15 元）；
5. 10:10 发布策略到门店券核销系统；
6. 10:20 名单页导出「高价值沉睡」78 行 CSV（含 R/F/M 分数与券面额）；
7. 10:30 店长用 viewer 账号下载同一份名单，尝试导出被 403 拒绝；
8. 16:00 复盘：导出记录与审计日志可追溯操作人、渠道、行数。

## 2. 主要功能（除登录外 5 个业务模块）

1. **会员与订单数据**：`/api/members`、`/api/members/stores`、`/api/orders/summary?as_of=`（8 门店**有效**订单量 / 净 GMV / 券核销率，附原始事件数与更正概览）；订单事实在 seed 中落库，分群时按 as-of 聚合，RFM 明细页展示门店订单基数与整体核销率。
2. **POS 补传更正**：`POST /api/orders/corrections` 接收销售/迟到/退款/撤销事件，`correction_no` 幂等（首次 201、重复投递 200 且 `duplicate=true`，不二次冲减）；退款超额、撤销后退款、跨会员冲减等非法更正写入即被拒绝。
3. **RFM 分群**：`/api/segments/runs` 创建/查询批次、簇成员分页；k=auto（3–8 轮廓系数选优）或指定 k。批次快照内附 `order_ledger` 台账（毛 GMV / 退款 / 撤销 / 净 GMV / 采纳与重复更正计数）。
4. **簇画像与预算策略**：`/api/strategies` 创建预算分配、发布；算法输出三档券面额与预计核销/GMV。
5. **名单导出与触达记录**：`/api/strategies/{id}/exports` 生成 CSV 名单（含原累计、退款冲减、净 M）并写触达记录，`/download` 下载。
6. **更正对账**：`/api/reconciliation` 在同一响应里给齐原单视角、更正采纳/未采纳原因、会员 gross→net 消费、两批次分群人数差异与同预算重算的预算差异；`/api/reconciliation/corrections` 提供更正流水。退款/撤销单本身不进入任何簇名单。
7. 审计日志（admin）：`/api/audit/logs`；限流：写操作每用户 60 次/分钟。

## 3. 算法（Python 真算，前端只展示）

### 算法 1：RFM 五分位打分 + numpy 手写 KMeans

- 输入：会员主数据 + 订单事实（`amount / ordered_at / coupon_issued / coupon_redeemed / order_type / original_order_no / correction_no`）。
- 聚合：先由 `order_ledger.build_order_ledger` 生成 **as-of 有效订单视图**（剔除统计日之后事件与撤销单、扣减退款、更正去重），再按会员聚合 R = as-of 与最后有效订单相差天数；F = 有效订单数；M = 净累计金额（原累计 − 退款冲减，撤销单整笔不计）。
- 打分：每个维度做**等频五分位**（每档约 20%，R 反向），`score ∈ {1..5}`。
- 标准化：三维分数 z-score。
- KMeans：k-means++ 初始化 + 手写迭代（`numpy`，非 sklearn）；`random_state=5741 + k`。
- 选 k：k=3..8，取平均轮廓系数 `s=(b−a)/max(a,b)` 最大者（同分取较小 k）。
- 输出：簇画像（人数、可触达、GMV 占比、R/F/M 均值、券核销率、客单价）、每人分数、直方图、散点。
- 可复现：固定 as-of、固定 random_state、订单聚合口径写死（含更正台账口径）；seed 数据由固定随机种子生成。
- 失败态：会员 < 100 → `RFM-3001`（422/409 业务错误）。
- 实现：`backend/app/services/rfm_engine.py`；as-of 更正台账：`backend/app/services/order_ledger.py`；口径常量：`backend/app/constants/rfm.py`、`backend/app/constants/order_types.py`。

### 算法 2：预算分配与券面额建议

- 每簇期望基准 = 历史券核销率 × 簇客单价 × 可触达人数。
- 按期望基准排序，把簇分三档券面额（40 / 25 / 15 元）。
- 每簇保底 400 元；剩余预算以 100 元为步长做**边际收益贪心**：
  `marginal = 核销率 × 客单价 / 面额 × (1 − 已分配/该簇容量)`，容量 = 可触达人数 × 面额。
- 输出：每簇预算（合计严格等于输入）、券面额、预计核销券数、预计增量 GMV、ROI。
- 失败态：预算不在 1,000–200,000、合计不足保底、簇为空 → `RFM-3002` / 409。
- 实现：`backend/app/services/budget_engine.py`。

## 4. 快速启动

```bash
cp .env.example .env          # 首次
docker compose up -d --build  # 5 服务：db / redis / backend / worker / frontend
# 前端 http://localhost:18247，后端 http://localhost:19247/docs，DB localhost:17747
```

首次启动 `SEED_DEMO=true` 时自动执行 `backend/scripts/seed_demo.py`：

- 8 门店 / 600 会员 / 2,400 原始订单 + 4 条 POS 更正事件（1 迟到补传单、2 退款、1 撤销；另含 2 条同 `correction_no` 的重复投递用于演示幂等去重）/ 2 次分群 / 3 账号；
- 两个演示批次：`SEG-20261112-001`（as-of 2026-11-12，更正事件全在统计日之后，结果与历史口径一致）与 `SEG-20261130-001`（as-of 2026-11-30，采纳全部更正，用于对账页核对差异）；
- 结束打印一行：`SEED_SUMMARY brand=rfmseg stores=8 members=600 orders=2404 segments=2 users=3`；
- 幂等：用户按 email、会员按 member_no、订单按 order_no、更正按 correction_no、批次按 run_no 判重，重复执行不新增、不覆盖。

本地开发：

```bash
# backend
cd backend && python3.11 -m venv .venv && . .venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
uvicorn app.main:app --reload --port 19247
# worker
celery -A app.config.celery_app worker -l info
# frontend
cd frontend && npm install && npm run dev   # http://localhost:18247
```

## 5. 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Pinia + Vue Router；SVG 自绘直方图/散点/轮廓曲线 |
| 后端 | Python 3.11 + FastAPI + SQLAlchemy 2.0 + Pydantic v2 + PyJWT |
| 异步 | Celery 5 + Redis（分群任务在 worker 执行，broker 不可用时后端同步回退） |
| 数据库 | PostgreSQL 16（JSONB 存分群快照/预算/touch log） |
| 部署 | Docker Compose，5 服务全部 healthcheck；Nginx 托管 dist + `/api/` 反代 |

## 6. 目录

```
backend/app/{config,constants,controllers,middlewares,models,routes,schemas,services,tasks,utils}
backend/scripts/{seed_demo.py,seed_data.py}   backend/migrations/
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants,styles}
worker/{Dockerfile,entrypoint.sh,healthy.py,wait_for_deps.py}
docker-compose.yml  .env.example
```

关键服务：`services/order_ledger.py`（as-of 更正台账，纯 Python 可独立复算）、
`services/order_correction_service.py`（POS 补传幂等落库）、
`services/reconciliation_service.py`（原单/冲减/消费/分群/预算五维对账）、
`services/rfm_engine.py`（在有效订单视图上聚合 RFM 与 KMeans）。

## 7. 环境变量

见 `.env.example`：`COMPOSE_PROJECT_NAME`、`DB_*`、`JWT_SECRET`、`FRONTEND_PORT/BACKEND_PORT/DB_PORT`、`REDIS_URL`、`RUN_DAILY_QUOTA`、`RATE_WINDOW_*`、`SEED_DEMO`。

## 8. 演示账号

| 邮箱 | 密码 | 角色 |
|---|---|---|
| `admin@rfmseg.io` | `Demo@2026` | admin 运营主管 |
| `analyst@rfmseg.io` | `Demo@2026` | analyst 会员分析师 |
| `viewer@rfmseg.io` | `Demo@2026` | viewer 店长（只读） |

## 9. 共享枚举出现位置（前后端各一份）

| 枚举 | 后端 | 前端 |
|---|---|---|
| 角色 `admin/analyst/viewer` | `backend/app/constants/roles.py` | `frontend/src/constants/roles.ts` |
| 分群状态 `queued/running/completed/failed` | `backend/app/constants/segment_status.py` | `frontend/src/constants/segmentStatus.ts` |
| 策略状态 `draft/published/archived` | `backend/app/constants/strategy_status.py` | `frontend/src/constants/strategyStatus.ts` |
| 订单/更正类型 `SALE/LATE_SALE/REFUND/VOID` | `backend/app/constants/order_types.py` | `frontend/src/constants/orderTypes.ts` |
| 簇类型 `HIGH_VALUE_DORMANT/...` | `backend/app/constants/cluster_kinds.py` | `frontend/src/constants/clusterKinds.ts` |
| 错误码 `RFM-xxxx` | `backend/app/constants/error_codes.py` | `frontend/src/constants/messages.ts` |

## 10. Docker 说明

- `docker-compose.yml` 顶层 `name: rfmseg`，无 `version:`；命名卷 `pg_data / redis_data / artifacts`。
- backend/worker 共享 `artifacts` 卷；worker 心跳文件由 compose healthcheck 读取。
- 端口：前端 `${FRONTEND_PORT:-18247}`、后端 `${BACKEND_PORT:-19247}`、DB `${DB_PORT:-17747}`。

## 11. 视觉

地质分层剖面风：砂岩分层色带、等高线背景、深度刻度、Fraunces（标题）+ IBM Plex Sans（正文）、oklch 色板；三层响应式（≥1280 / ≥768 / <768）；空态与错误态可恢复。离线时 Google Fonts 回退到 Songti/Georgia 与系统无衬线（见 TESTING.md）。

## License

MIT（演示项目，数据全部为合成数据）。
