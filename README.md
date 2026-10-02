# 周期平铺织物组织设计器

设计师在网页上录入周期平铺的织物组织（交织格），系统按环向浮长规则校验原稿、
标出超限段，并求出**翻转未锁定格数量最少**的可行修补（同数时取按行展开
字典序最小的格值序列），展示翻转位置与修补预览；确认预览后才覆盖原稿。

修补预览支持**候选比选**：在同一草稿的环向浮长、锁定格与可选 `onesRange`
约束下，先求全局最少翻转数，再返回按格值行展开字典序排列的**前 3 个互不相同
且同样最优**的可行网格（不足 3 个则按实际数量返回）。网页可逐个查看并选择；
确认只能选择该令牌预览中返回的方案。

- 后端：Python + Flask + SQLite（求解器基于 PySAT 的 MaxSAT/ SAT 编码）
- 前端：React（Vite 构建，由后端统一托管）
- 部署：Dockerfile + Docker Compose 一键启动，含依赖安装与数据库初始化

## 规则说明

- 格值：`0` 表示经线在上，`1` 表示纬线在上。
- 尺寸：1~16 行、1~8 列的非空矩形。
- 浮长：每行、每列的相同值连续段按**环向**计（左右、上下拼接边界算作相连），
  长度不得超过正整数 K；**全同的一行或一列视为无限浮长**（恒超限）。
- 修补：只能翻转未锁定格；目标为翻转数最少，并列时取按行展开字典序最小的格值序列。
- 候选比选：每次预览先按上述约束求**全局最少翻转数**（MaxSAT，只求解一次），
  再在最优翻转数下枚举按行展开格值字典序升序的**前 3 个互不相同网格**
  （不足 3 个按实际数量返回，唯一最优时只有 1 个）；所有候选翻转数相同。
  响应中的 `repaired` / `flips` / `flipCount` **始终指向首个（字典序最小）
  方案**，`candidates` 则给出每个候选的网格 `repaired`、翻转坐标 `flips`
  与格值1总数 `onesCount`。
- 格值1总数区间（可选）：设计师可为草稿指定修补稿中格值 `1` 的总格数区间
  `[下限, 上限]`；不填（两栏留空 / 请求省略或传 `null`）时沿用原有修补规则。
  填写时**两端都必填**且满足 `0 ≤ 下限 ≤ 上限 ≤ 行数×列数`；尺寸变化导致区间
  越界时只提示调整，**绝不悄然截断**。预览在环向浮长 K、锁定格与总数区间的
  共同约束下，只从总数达标的修补稿中选翻转最少者（并列仍取按行展开字典序最小）；
  总数不可达时按无可行修补处理（不发令牌）。
- 无可行修补时保留原稿并报无解；尺寸、格值、K 或区间不合法或编辑越界时拒绝操作。
- 区间随确认结果**与原稿一并持久化**（`GET /api/state` 可读出）；预览与确认的
  一致性比对包含区间，区间变更沿用既有 409 `mismatch` 冲突语义。
- 预览在生成时绑定当时的**原稿版本**；确认在单条数据库事务中原子校验
  「令牌未消费 + 提交草稿与预览输入逐字节一致 + 原稿版本未变」，
  三者同时成立才覆盖原稿（版本号恰好 +1）并消费令牌；任一不满足即整体回滚、
  原稿不变并返回 409（预览已失效）。因此并发或重复确认最多生效一次，
  甲确认后乙持有的旧令牌无法覆盖甲的结果。
- 网页收到 409 后会清除旧预览、重新拉取原稿、保留当前未提交草稿，
  并提示重新预览后再确认。

## 冲突处理（预览令牌与版本）

- `POST /api/preview`：服务端读取当前原稿版本 `v`，把规范化后的草稿输入
  （尺寸、格值、锁定、K、**格值1总数区间**）与求解结果、令牌、基础版本 `v`
  一并落库；无解（含总数区间不可达）时返回 `solvable: false`，**不发放令牌**，
  不可确认。
- `POST /api/confirm`：请求字段在原有基础上增加可选的 `onesRange`
  （`null` 或省略均表示不限制）与可选的方案序号 `choice`
  （从 0 开始；省略或 `0` 表示确认预览返回的首个方案，旧请求行为不变）。
  服务端在 `BEGIN IMMEDIATE` 写事务内完成校验与覆盖：

  | 情况                       | 结果        | 原稿/版本          |
  | -------------------------- | ----------- | ------------------ |
  | 令牌未消费、草稿一致（含区间）、版本未变、`choice` 在候选范围内 | 200，覆盖为所选网格 | 原稿与区间更新，版本 +1，令牌删除 |
  | `choice` 越界或类型非法（超出 `candidateCount-1`、负数、非整数） | 400 `invalid_choice` | 不变，**令牌不消费**，可改选后重试 |
  | 令牌不存在或已被确认（含并发重复提交） | 409 `consumed` | 不变 |
  | 草稿与预览输入不一致（含区间被改动/清除） | 409 `mismatch` | 不变 |
  | 预览绑定的原稿版本已被他人确认推进 | 409 `stale` | 不变 |
  | 无解预览、非法尺寸/格值/K/区间     | 400（无解预览本身来自 preview 接口的 `solvable:false`） | 不变 |

  非法 `choice` 的 400 与 409 不同：它**不消费令牌**，只要令牌、草稿、版本
  仍与预览一致，换成合法序号即可继续确认。选择的网格完全取自服务端预览结果
  （`candidates[choice].repaired`），请求体中携带的网格只用于草稿一致性
  比对，**不能伪造**出预览之外的网格。三种 409 响应体均为
  `{"error": "…请重新预览"}`，原稿与版本号不发生任何变化；
  冲突后重新 `POST /api/preview` 取得新令牌即可正常确认。旧版本令牌
  （生成于本轮功能之前、结果中无候选列表的预览）只允许 `choice=0`，
  其他序号按 400 拒绝；重新预览即可获得候选。

### 调用示例

```bash
# 1. 查看原稿（含版本号、格值1总数与区间）
curl -s http://localhost:8000/api/state
# {"rows":4,"cols":4,...,"version":1,"onesCount":8,"onesRange":null,"violations":[]}

# 2. 提交草稿预览，拿到令牌（无解时 solvable=false 且无 token）
curl -s -X POST http://localhost:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"rows":2,"cols":2,"grid":[[0,1],[0,1]],"locks":[],"k":2}'
# {"solvable":true,"token":"689b…","repaired":[[0,1],[1,0]],
#  "onesCount":2,"repairedOnesCount":2,"onesRange":null, ...}

# 2b. 指定格值1总数区间 [下限, 上限]（两端必填，0≤下限≤上限≤行数×列数）
curl -s -X POST http://localhost:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"rows":4,"cols":4,
       "grid":[[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]],
       "locks":[],"k":2,"onesRange":[8,8]}'
# {"solvable":true,"token":"ab12…",
#  "onesCount":0,"repairedOnesCount":8,"onesRange":[8,8],
#  "repaired":[[0,0,1,1],[0,0,1,1],[1,1,0,0],[1,1,0,0]], ...}

# 2c. 区间不可达（含锁定冲突、全同列/行等）-> 无令牌
curl -s -X POST http://localhost:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"rows":4,"cols":4,"grid":[...全0...],"locks":[],"k":2,"onesRange":[9,9]}'
# {"solvable":false,"onesRange":[9,9],"onesCount":0,...}  （无 token 字段）

# 2d. 区间越界/只填一端/下限>上限 -> 400（不会截断）
# {"error":"格值1总数区间上限 99 超出当前尺寸总格数 16（4×4），请调整区间或尺寸"}

# 3. 携带令牌与同一份草稿确认（区间必须与预览输入逐字节一致）
curl -s -X POST http://localhost:8000/api/confirm \
  -H 'Content-Type: application/json' \
  -d '{"token":"ab12…","rows":4,"cols":4,"grid":[...全0...],
       "locks":[],"k":2,"onesRange":[8,8]}'
# {"state":{...,"version":2,"onesRange":[8,8],"onesCount":8,"violations":[...]}}

# 3b. 确认时不带区间（或传 [8,9]）而预览带了 [8,8] -> 409 mismatch，原稿不变
# 4. 同一令牌再次确认（或他人已抢先确认、草稿已变动）-> 409，原稿不变
# {"error":"预览令牌不存在或已被确认，请重新预览"}
```

### 候选比选（最多 3 个同样最优方案）

`POST /api/preview` 成功时除首个方案字段外，还返回 `candidates`（1~3 个）
与 `candidateCount`；每个候选含 `repaired`（网格）、`flips`（翻转坐标）、
`onesCount`（格值1总数）。所有候选的翻转数都等于全局最少值 `flipCount`，
且按格值行展开字典序升序、网格互不相同。

```bash
# 5. 预览多候选（4x4 全0、K=2、恰好 8 个 1；最优方案不止一个）
curl -s -X POST http://localhost:8000/api/preview \
  -H 'Content-Type: application/json' \
  -d '{"rows":4,"cols":4,
       "grid":[[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]],
       "locks":[],"k":2,"onesRange":[8,8]}'
# {
#   "solvable": true, "token": "ab12…", "flipCount": 8, "candidateCount": 3,
#   "repaired": [[0,0,1,1],...],            # 恒等于 candidates[0].repaired
#   "candidates": [
#     {"repaired": [[0,0,1,1],[0,0,1,1],[1,1,0,0],[1,1,0,0]],
#      "flips": [[0,2],[0,3],...], "onesCount": 8},
#     {"repaired": [[0,0,1,1],[0,1,0,1],[1,0,1,0],[1,1,0,0]], ...},
#     {"repaired": [[0,0,1,1],[0,1,0,1],[1,1,0,0],[1,0,1,0]], ...}
#   ], ...
# }

# 6. 确认第 3 个候选（choice 从 0 开始；省略或 0 = 首个方案，旧请求兼容）
curl -s -X POST http://localhost:8000/api/confirm \
  -H 'Content-Type: application/json' \
  -d '{"token":"ab12…","choice":2,"rows":4,"cols":4,
       "grid":[[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]],
       "locks":[],"k":2,"onesRange":[8,8]}'
# {"state":{...,"grid":[[0,0,1,1],[0,1,0,1],[1,1,0,0],[1,0,1,0]],
#           "onesCount":8,"version":2,...}}

# 7. 越界选择（如该预览只有 3 个候选却传 choice=3 或 -1、"1"、true）
#    -> 400，令牌保持未消费，改用 0/1/2 仍可确认
# {"error":"所选方案不在该预览令牌的候选范围内，请从预览返回的方案中重新选择"}
```

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

启动后访问：

- 网页地址： <http://localhost:8000>
- 端口： `8000`（宿主机端口可在 `docker-compose.yml` 的 `ports` 中修改）

在网页「修补预览」面板中，当同一草稿存在多个同样最优的修补时，面板顶部会出现
**方案 1 / 方案 2 / 方案 3** 切换按钮（按格值行展开字典序排列），可逐个查看
网格、翻转位置与格值1总数，选中后点「确认应用预览」即按所选方案覆盖原稿；
只有一个最优方案时不显示切换条，操作与旧版一致。

首次启动会自动初始化 SQLite 数据库（建表 + 写入一份默认原稿），
数据库文件保存在名为 `weave-data` 的卷中，容器重建后数据仍在。

停止：

```bash
docker compose down            # 保留数据卷
docker compose down -v         # 连同数据库一起删除
```

## 配置（环境变量）

| 变量      | 默认值            | 说明                       |
| --------- | ----------------- | -------------------------- |
| `PORT`    | `8000`            | 后端监听端口（容器内）     |
| `DB_PATH` | `/data/weave.db`  | SQLite 数据库文件路径      |

也可不用 Compose，直接构建镜像：

```bash
docker build -t weave-designer .
docker run -p 8000:8000 -v weave-data:/data weave-designer
```

## 本地开发（不用 Docker）

```bash
# 后端（Python 3.11+）
cd server
pip install -r requirements.txt
python app.py                 # 监听 http://localhost:8000

# 前端（Node 20+，另开终端；开发服务器代理 /api 到 8000 端口）
cd frontend
npm ci
npm run dev                   # http://localhost:5173

# 生产形态：构建前端后由 Flask 托管
cd frontend && npm run build
cp -r dist ../server/static
cd ../server && python app.py
```

后端调试模式：`FLASK_DEBUG=1 python app.py`（Flask 内置服务器，自动重载）。

## 运行测试

```bash
cd server
python test_solver.py
```

求解器测试内容：固定边界用例（全同、锁定冲突、环向跨界、K 极大等）、
60 组随机小网格与暴力枚举对照（翻转数最少 + 字典序最小）、
格值1总数区间固定/随机用例（与暴力对照，含区间不可达、锁定冲突、精确计数）、
**候选比选用例（与暴力求出的全部最优网格前 3 个对照：数量、字典序、
翻转数相同、网格互异、区间达标；唯一最优与不足 3 个的情形）**、
16×8 最大网格在各档 K 与区间下（含枚举 3 候选）的性能。

```bash
cd server
python test_confirm_race.py
```

确认竞争测试内容：成功确认版本恰好 +1、令牌消费后重复确认 409、
草稿不匹配 409、甲确认后乙的同原稿旧令牌 409、同一令牌 5 线程并发仅一次生效、
无解预览无令牌、非法请求 400、成功响应结构保持不变；
以及格值1总数区间的非法输入 400、区间不可达无令牌、确认体缺/改区间 409、
区间随原稿持久化、无字段/`null` 请求与旧响应的兼容性；
候选比选：候选数量（1~3）与字段自洽、字典序与同翻转数、
顶层字段恒指首个方案、旧请求缺省确认首个、`choice` 选择其他候选落库、
越界/伪造/类型非法选择 400 且不消费令牌（随后可合法重试）、
伪造选择叠加草稿改动仍 409、旧版本令牌选择任意候选保持 409 stale、
同一令牌并发选择不同候选仅一次生效且结果必属本预览、
区间下各候选均达标并可确认末尾候选。

## API 简介

| 方法 | 路径           | 说明                                                         |
| ---- | -------------- | ------------------------------------------------------------ |
| GET  | `/api/state`   | 返回当前原稿（尺寸、格值、锁定格、K、**格值1总数区间**、版本号、**格值1总数 `onesCount`**）及其超限段 |
| POST | `/api/preview` | 提交草稿（可选 `onesRange:[下限,上限]`），返回超限段、全局最少翻转数与按行展开字典序前 3 个同样最优候选 `candidates`（含 `candidateCount`、双方格值1总数、区间、翻转位置、令牌）；非法输入 400，无解（含区间不可达）时 `solvable=false` 且无令牌、无候选 |
| POST | `/api/confirm` | 携带预览令牌、当前草稿（含一致的 `onesRange`）与可选 `choice`（从 0 开始，缺省为 0）；原子校验令牌未消费、草稿与预览输入一致（含区间）、原稿版本未变、`choice` 在候选范围内，成功才覆盖为**所选**网格与区间（版本 +1）并消费令牌；重复/过期/草稿或区间不匹配返回 409 且原稿不变，`choice` 越界或非法返回 400 且**不消费**令牌 |

### `onesRange` 字段约定

- 省略或为 `null`：不限制格值1总数，修补规则与旧版本完全一致。
- 否则必须是长度 2 的整数数组 `[下限, 上限]`，
  且 `0 ≤ 下限 ≤ 上限 ≤ rows×cols`，否则返回 400（不做任何截断）。
- `GET /api/state` 恒返回 `onesRange`（无区间为 `null`）与 `onesCount`；
  `POST /api/preview` 返回原稿 `onesCount`、首个候选的 `repairedOnesCount`
  与回显的 `onesRange`（无解时不返回 `repairedOnesCount`、`token` 与
  `candidates`）。
- 未携带 `onesRange` 的旧请求/响应继续可用。

### 候选与 `choice` 字段约定

- `POST /api/preview` 成功时恒返回 `candidates`（数组，长度 1~3）与
  `candidateCount`：先按环向浮长、锁定格与 `onesRange` 求全局最少翻转数，
  再返回该翻转数下按格值**行展开字典序升序**的前 3 个**互不相同**网格，
  不足 3 个按实际数量返回。每个候选为
  `{"repaired": [[...]], "flips": [[r,c], ...], "onesCount": n}`。
- `repaired`、`flips`、`flipCount` 与 `repairedOnesCount` **始终描述
  首个方案**（即 `candidates[0]`），旧客户端不读 `candidates` 也完全可用。
- `POST /api/confirm` 的可选整数 `choice` 为候选下标（从 0 开始）：
  省略或 `0` 确认首个方案（与旧请求完全一致）；`1..candidateCount-1`
  确认对应候选。越界、负数或非整数（含字符串、浮点、布尔）返回 400 且
  令牌不被消费。确认落库的网格只取自服务端保存的 `candidates[choice]`，
  客户端无法借 `choice` 或请求体网格伪造预览之外的结果。

## 目录结构

```
├── Dockerfile              # 多阶段：前端构建 + 后端运行
├── docker-compose.yml      # 一键启动
├── server/                 # Python 后端
│   ├── app.py              # Flask 路由（state / preview / confirm）
│   ├── solver.py           # SAT/MaxSAT 最优修补求解
│   ├── weave.py            # 环向超限段检测
│   ├── validate.py         # 尺寸/格值/锁定/K 校验
│   ├── db.py               # SQLite 持久化与初始化（预览版本绑定 + 原子确认事务）
│   ├── test_solver.py      # 求解器测试（含暴力对照）
│   ├── test_confirm_race.py # 确认竞争端到端测试（含并发、冲突拒绝）
│   └── requirements.txt
└── frontend/               # React 前端（Vite）
    └── src/
        ├── App.jsx         # 编辑器、预览、确认交互
        ├── weave.js        # 前端实时超限段检测
        └── api.js
```
