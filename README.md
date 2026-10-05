# 🌍 跨时区软件发布窗口编排

在多时区环境下编排软件发布：给定**服务依赖、候选发布时间窗、团队禁发时段、维护冻结、回滚耗时与共享资源**，
系统用 Google OR-Tools CP-SAT 求出满足全部约束的可执行排程；无解时返回**阻塞它的约束组合**而不是部分结果。

## 约束语义

| 约束 | 规则 |
|---|---|
| 候选时间窗 | 每个服务的**部署 + 回滚整个占用区间**必须完整落在其某个候选窗内；支持多窗 |
| 依赖先后 | `下游开始 ≥ 上游部署结束 + gap_minutes` |
| 团队禁发 | 仅**部署区间**不得与所属团队禁发时段重叠（回滚允许进入禁发期） |
| 维护冻结 | **占用区间（部署+回滚）**不得与任何冻结重叠 |
| 共享资源 | 同名资源同一时间只能被一个服务占用（含回滚窗口） |
| 夏令时 | 不带偏移量的本地墙上时间若在 DST 切换时**重复**（回拨）或**不存在**（前拨），请求被拒绝（422），并给出 gap 两侧 / 重复两次的绝对时间供修正；带偏移量的绝对时间（如 `...+01:00`、`...Z`）无歧义，直接通过 |
| 无解 | 求解器返回一个足以导致矛盾的约束子集（sufficient assumption core），即“阻塞组合”，放松其中任意一项后可重新求解；**不返回部分排程** |

目标函数：所有服务部署开始时刻之和最小（排程尽量早、结果确定可复现）。

## 快速开始（Docker Compose）

```bash
docker compose up --build
```

- 操作页面：http://localhost:8000/
- 健康检查：http://localhost:8000/api/health
- 交互式 API 文档（Swagger）：http://localhost:8000/docs

停止：`docker compose down`。

## 仅用 Docker

```bash
docker build -t release-orchestrator:1.0 .
docker run --rm -p 8000:8000 release-orchestrator:1.0
```

## 本地直接运行（Python 3.11+）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

> 时区数据：镜像内同时安装了系统 `tzdata` 与 Python `tzdata` 包，保证 `zoneinfo` 可解析全部 IANA 时区
> （`Asia/Shanghai`、`Europe/Berlin`、`America/New_York` 等）。

## HTTP API

### `POST /api/schedule`

请求体：

```json
{
  "services": [
    {
      "id": "payments",
      "name": "支付服务",
      "timezone": "Europe/Berlin",
      "duration_minutes": 45,
      "rollback_minutes": 60,
      "resources": ["payment-db", "k8s-prod-east"],
      "windows": [
        {"start": "2026-11-13T22:00", "end": "2026-11-14T05:00", "timezone": "Europe/Berlin"}
      ]
    }
  ],
  "dependencies": [
    {"upstream": "payments", "downstream": "api-gateway", "gap_minutes": 30}
  ],
  "service_teams": [
    {"service": "payments", "team": "payment-team"}
  ],
  "team_blackouts": [
    {"team": "payment-team", "start": "2026-11-13T23:30", "end": "2026-11-14T00:30", "timezone": "Europe/Berlin"}
  ],
  "freezes": [
    {"label": "周末封网", "start": "2026-11-14T02:00", "end": "2026-11-14T03:00", "timezone": "UTC"}
  ],
  "max_solve_seconds": 10
}
```

时间串支持两种写法：

- **墙上时间 + 时区字段**：`"2026-11-13T22:00"` + `"timezone": "Europe/Berlin"`，按该时区解释，参与 DST 检查；
- **绝对时间**：`"2026-11-13T21:00:00Z"` 或 `"2026-11-13T22:00:00+01:00"`，无 DST 歧义。

**可行（200）**：

```json
{
  "feasible": true,
  "status": "optimal",
  "schedule": [
    {
      "service_id": "payments",
      "name": "支付服务",
      "deploy_start_utc": "2026-11-13T21:00:00+00:00",
      "deploy_end_utc": "2026-11-13T21:45:00+00:00",
      "occupied_until_utc": "2026-11-13T22:45:00+00:00",
      "local_time": "2026-11-13T22:00:00+01:00",
      "timezone": "Europe/Berlin",
      "window_used": "2026-11-13T21:00:00+00:00 ~ 2026-11-14T04:00:00+00:00 UTC（候选窗1）",
      "resources": ["payment-db"]
    }
  ],
  "blockers": null
}
```

**不可行（200，`feasible=false`）**：`schedule` 为 `null`，`blockers` 为联合导致无解的约束组合：

```json
{
  "feasible": false,
  "status": "infeasible",
  "schedule": null,
  "blockers": [
    {"code": "freeze:周末封网:a", "message": "周末封网（…）期间服务 A 不得部署或处于回滚窗口内"},
    {"code": "dependency:a->b", "message": "依赖顺序：B 必须在 A 部署完成之后才能开始"},
    {"code": "resource:shared-db", "message": "共享资源 shared-db 同时只能被一个服务占用…"}
  ]
}
```

**输入错误（422）**：DST 重复/不存在、未知时区、依赖环、区间非法、引用不存在的服务、候选窗短于部署+回滚占用等：

```json
{
  "errors": [
    {
      "code": "dst_nonexistent",
      "field": "services[x].windows[0].start",
      "message": "…本地时间在夏令时前拨时不存在（该时刻被跳过）…",
      "suggestions": [
        "2027-03-14T06:30:00+00:00  （gap 前，偏移 UTC-05:00）",
        "2027-03-14T07:30:00+00:00  （gap 后，偏移 UTC-04:00）"
      ]
    }
  ]
}
```

`dst_ambiguous`（重复时间）的建议分别给出回拨前后的两个绝对时刻（第一次/第二次）。
这些值可直接粘贴回输入框重新提交。

### `POST /api/schedule/diff`（变更影响预演）

提交**当前排程请求**（`base`）与**修改后的请求**（`candidate`），系统分别求解后按服务 ID
对齐结果，输出变更影响：

- `changed`：两侧都存在、但**部署开始时间**或**占用结束时间（含回滚）**发生变化的服务，
  含变化前后 UTC 时刻与变化分钟数（后 − 前）；
- `added` / `removed`：仅在新请求中出现 / 被移除的服务及其在所属排程中的时刻；
- 任一请求**输入错误**（422，逐侧给出完整错误）或**无解**（200，逐侧给出阻塞约束组合）时，
  **不输出差异结果**（`diff` 为 `null`）；
- 两份请求求解结果完全一致时返回空差异（`changed`/`added`/`removed` 均为 `[]`）。

请求体：

```json
{
  "base":      { "services": [ ... ], "dependencies": [ ... ], "...": "同 /api/schedule 请求体" },
  "candidate": { "services": [ ... ], "dependencies": [ ... ], "...": "同 /api/schedule 请求体" }
}
```

**两侧均可行（200，`status=ok`）**：

```json
{
  "status": "ok",
  "diff": {
    "changed": [
      {
        "service_id": "payments",
        "name": "支付服务",
        "deploy_start_before_utc": "2026-11-13T21:00:00+00:00",
        "deploy_start_after_utc": "2026-11-13T22:00:00+00:00",
        "deploy_start_delta_minutes": 60,
        "occupied_until_before_utc": "2026-11-13T22:45:00+00:00",
        "occupied_until_after_utc": "2026-11-13T23:45:00+00:00",
        "occupied_until_delta_minutes": 60
      },
      {
        "service_id": "ledger",
        "name": "账务批处理",
        "deploy_start_before_utc": "2026-11-13T22:45:00+00:00",
        "deploy_start_after_utc": "2026-11-13T23:45:00+00:00",
        "deploy_start_delta_minutes": 60,
        "occupied_until_before_utc": "2026-11-14T01:45:00+00:00",
        "occupied_until_after_utc": "2026-11-14T02:45:00+00:00",
        "occupied_until_delta_minutes": 60
      }
    ],
    "added": [
      {"service_id": "notify", "name": "通知服务",
       "deploy_start_utc": "2026-11-14T00:00:00+00:00",
       "occupied_until_utc": "2026-11-14T00:45:00+00:00"}
    ],
    "removed": [
      {"service_id": "legacy-report", "name": "旧报表",
       "deploy_start_utc": "2026-11-14T00:00:00+00:00",
       "occupied_until_utc": "2026-11-14T01:00:00+00:00"}
    ]
  },
  "base": null,
  "candidate": null
}
```

**某一侧无解（200，`status=base_infeasible | candidate_infeasible | both_infeasible`）**：
`diff` 为 `null`，对应侧给出完整阻塞约束组合（结构同 `/api/schedule` 的 `blockers`）：

```json
{
  "status": "candidate_infeasible",
  "diff": null,
  "base": null,
  "candidate": {
    "status": "infeasible",
    "blockers": [
      {"code": "freeze:周末封网:payments", "message": "周末封网（…）期间服务 支付服务（payments）不得部署或处于回滚窗口内"}
    ]
  }
}
```

**某一侧输入错误（422）**：逐侧给出完整校验错误（结构同 `/api/schedule` 的 422），无错误的一侧为 `null`：

```json
{
  "errors": {
    "base": null,
    "candidate": [
      {"code": "dst_nonexistent", "field": "services[x].windows[0].start",
       "message": "…在夏令时前拨时不存在…", "suggestions": ["…", "…"]}
    ]
  }
}
```

#### 调用示例（curl）

```bash
curl -s http://localhost:8000/api/schedule/diff \
  -H 'Content-Type: application/json' \
  -d '{
    "base": {
      "services": [
        {"id": "payments", "name": "支付服务", "timezone": "Europe/Berlin",
         "duration_minutes": 45, "rollback_minutes": 60,
         "resources": ["payment-db"],
         "windows": [{"start": "2026-11-13T22:00", "end": "2026-11-14T05:00", "timezone": "Europe/Berlin"}]},
        {"id": "ledger", "name": "账务批处理", "timezone": "America/New_York",
         "duration_minutes": 60, "rollback_minutes": 120,
         "resources": ["payment-db"],
         "windows": [{"start": "2026-11-13T16:00", "end": "2026-11-14T23:00", "timezone": "America/New_York"}]},
        {"id": "legacy-report", "name": "旧报表", "timezone": "UTC",
         "duration_minutes": 30, "rollback_minutes": 30,
         "windows": [{"start": "2026-11-14T00:00", "end": "2026-11-14T08:00", "timezone": "UTC"}]}
      ],
      "dependencies": [{"upstream": "payments", "downstream": "ledger", "gap_minutes": 60}]
    },
    "candidate": {
      "services": [
        {"id": "payments", "name": "支付服务", "timezone": "Europe/Berlin",
         "duration_minutes": 45, "rollback_minutes": 60,
         "resources": ["payment-db"],
         "windows": [{"start": "2026-11-13T23:00", "end": "2026-11-14T06:00", "timezone": "Europe/Berlin"}]},
        {"id": "ledger", "name": "账务批处理", "timezone": "America/New_York",
         "duration_minutes": 60, "rollback_minutes": 120,
         "resources": ["payment-db"],
         "windows": [{"start": "2026-11-13T16:00", "end": "2026-11-14T23:00", "timezone": "America/New_York"}]},
        {"id": "notify", "name": "通知服务", "timezone": "UTC",
         "duration_minutes": 15, "rollback_minutes": 30,
         "windows": [{"start": "2026-11-14T00:00", "end": "2026-11-14T08:00", "timezone": "UTC"}]}
      ],
      "dependencies": [{"upstream": "payments", "downstream": "ledger", "gap_minutes": 60}]
    }
  }'
```

上面的 `candidate` 把 payments 的候选窗整体后移 1 小时、移除 legacy-report 并新增 notify：
返回中 `changed` 给出 payments 与 ledger（受依赖推动）各后移 60 分钟的前后 UTC 时刻，
`added`/`removed` 分别列出 notify 与 legacy-report。

## 项目结构

```
app/
  main.py        FastAPI 入口（/api/schedule、/api/schedule/diff、/api/health、静态页面）
  models.py      请求/响应 Pydantic 模型
  times.py       时区解析与 DST 重复/不存在分类（zoneinfo fold 规则）
  validator.py   输入校验：DST、引用完整性、依赖环检测、区间合法性
  scheduler.py   CP-SAT 建模、求解、不可行假设核（blocking core）提取
  web/index.html 操作页面（原生 JS，内置 3 个示例场景）
Dockerfile
docker-compose.yml
requirements.txt
```

## 不可行核的工作原理

每条业务约束都挂在一个布尔“强制字面量”上（通过可选区间实现：字面量为假时区间在
`NoOverlap` 中缺席），求解时把全部字面量作为 assumptions 断言为真。
CP-SAT 在判定 INFEASIBLE 时返回 `SufficientAssumptionsForInfeasibility`：
其中任意一个字面量放松（对应约束被修改/移除），其余约束才有可能成立。
因此返回的不是“所有相关约束”，而是一个最小化的、可直接据此谈判的阻塞组合。
