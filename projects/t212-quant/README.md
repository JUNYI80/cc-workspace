# t212-quant：趋势过滤的动量 ETF 轮动策略（Trading 212）

> **先读这一段**
>
> - **没有任何策略能保证赚钱。** 这个策略的目标不是"每年跑赢大盘"，而是在长期拿到接近股票的收益，同时在大熊市里明显少亏。在单边大牛市里（比如 2023–2024 年科技股暴涨），它通常会**跑输**一直持有。
> - 开发环境无法访问行情数据，所以代码只在**合成数据**上测试过。**真实回测结果需要你在自己电脑上运行 `t212q backtest` 才能看到。**
> - 在看到真实回测结果、并在 Trading 212 模拟账户（Practice / demo）跑满 2–3 个月之前，不要投真钱。上实盘后先用小额，只投亏得起的钱。

## 策略一句话

每个月底，从 10 只英镑计价的伦敦上市 ETF（标普 500、纳指 100、富时 100、欧洲、日本、新兴市场、英国地产、黄金、英国国债、通胀挂钩国债）里，选出 **3/6/12 个月动量最强**、**价格在 200 日均线之上**、并且**跑赢现金**的 3 只。按波动率倒数分配仓位；如果组合预期年化波动超过 12%，整体降仓。剩下的钱放进超短期英镑债券 ETF（ERNS，相当于现金）。

详细的规则、设计理由、什么时候会失效，见 [docs/strategy.md](docs/strategy.md)。

## 为什么适合 Trading 212

- **全部是 LSE 上的英镑 ETF**：没有 Trading 212 的 0.15% 换汇费，ISA 里可以持有，ETF 买卖也不交英国印花税。
- **每月只调仓一次**，并设 3% 的再平衡带宽，换手和成本都低。Trading 212 不收佣金，主要成本是买卖价差（回测按每笔 0.10% 估算）。
- **只用市价单**，因为 Trading 212 实盘 API 目前只支持市价单。
- **强烈建议在 Stocks ISA 里运行**：每月轮动会不断实现资本利得。普通账户（GIA）目前每年只有 £3,000 的资本利得免税额度，ISA 里则完全免税。（Trading 212 API 只支持 Invest 和 Stocks ISA 账户，不支持 CFD。）

## 目录结构

```
t212-quant/
├── config/strategy.yaml   # 标的池、策略参数、风控参数（改这里，不用改代码）
├── src/t212quant/
│   ├── strategy.py        # 信号：动量 + 趋势过滤 + 倒数波动率 + 波动率目标
│   ├── backtest.py        # 回测引擎（月末决策，次日收盘成交，含交易成本）
│   ├── metrics.py         # 年化收益、波动、夏普、最大回撤等
│   ├── data.py            # Yahoo Finance 行情下载、缓存、清洗
│   ├── broker.py          # Trading 212 API 客户端（含限速处理）
│   ├── rebalance.py       # 目标仓位 → 订单，执行与安全检查
│   ├── plot.py            # 净值和回撤图
│   └── cli.py             # 命令行 t212q
├── tests/                 # 单元测试和集成测试，含模拟的 Trading 212 API
└── docs/strategy.md       # 策略说明
```

运行后会生成 `data/`（行情缓存）、`reports/`（回测图）、`state/journal.jsonl`（下单记录），这些都在 `.gitignore` 里。

## 安装

需要 Python 3.10+。

```bash
cd projects/t212-quant
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                             # 应该全部通过
```

## 使用步骤

**第 1 步：真实数据回测**（会从 Yahoo Finance 下载约 15 年的数据）

```bash
t212q backtest --plot              # 和"持有全球股票 VWRL"以及"60/40 股债"比较，图存到 reports/backtest.png
t212q sweep                        # 81 组参数的稳健性检验（约 1–2 分钟）：看中位数和最差的那组，不要看最好的那组
```

回测时重点看：最大回撤是不是明显小于 VWRL；年化收益差多少；哪些年份跑输。如果在大部分参数组合下都不如简单持有 VWRL 或 60/40，就**不要用**。

**第 2 步：看本月信号**

```bash
t212q signal                       # 每只 ETF 的动量分数、是否在均线上方、目标仓位
```

**第 3 步：连接 Trading 212 模拟账户**

1. 在 Trading 212 App 里切换到 **Practice（模拟）** 账户，进入 Settings → API (Beta)，生成 API key 和 secret（secret 只显示一次）。权限需要包括账户信息、持仓、元数据、订单读取和下单。
2. 复制 `.env.example` 为 `.env`，填入 demo 的 key 和 secret。**`.env` 绝不能提交到 git**（已在 `.gitignore` 中）。
3. 加载环境变量并检查：

```bash
set -a; source .env; set +a
t212q resolve --env demo           # 把每只 ETF 对应到 Trading 212 代码，核对名称是否正确
t212q account --env demo           # 查看现金和持仓
t212q rebalance --env demo         # 只预览订单（dry run），不会下单
t212q rebalance --env demo --execute   # 在模拟账户真正下单
```

**第 4 步：在模拟账户跑 2–3 个月**

每月第一个交易日伦敦时间 10:00 左右运行一次 `rebalance`。对照 `state/journal.jsonl` 检查成交是否符合预期。

**第 5 步：小额实盘**

在 `config/strategy.yaml` 里设置 `capital_cap`（比如 500 或 1000 英镑），在 `.env` 里填入实盘 key 并设 `T212_ALLOW_LIVE=yes`：

```bash
t212q rebalance --env live             # 先预览
t212q rebalance --env live --execute   # 确认后会要求输入 yes
```

## 安全机制

| 机制 | 作用 |
|---|---|
| 默认 demo + 只预览 | 不加 `--execute` 永远不会下单 |
| 实盘三重确认 | 需要 `T212_ALLOW_LIVE=yes` 环境变量 + `--execute` + 输入 `yes`（或 `--yes`） |
| 交易时段检查 | 实盘只在伦敦时间工作日 08:15–16:15 下单，避免市价单排队到第二天开盘成交 |
| 单笔上限 `max_order_value` | 任何一笔超过上限，整个计划拒绝执行（默认 £2,500，可调） |
| 资金上限 `capital_cap` | 机器人最多管理多少钱 |
| 不碰其他持仓 | 只交易策略里的 11 只 ETF，你账户里的其他股票和 Pie 里的份额不会被动 |
| 先卖后买 | 卖单没成交就不买；买单按卖出后**实际**可用现金自动缩小 |
| 数据检查 | 行情超过 5 天没更新、缺价、或和 Trading 212 的持仓价格相差 >10%，拒绝执行 |
| 每月一次 | 本月已经下过单就拒绝再下（`--force` 可覆盖），防止定时任务重复下单 |
| 全程记录 | 每次下单的请求和返回都写入 `state/journal.jsonl` |

## 自动运行（可选，建议前几个月先手动）

在一台常开的机器上（时区设为英国），用 crontab：

```cron
0 10 1-7 * * cd /path/to/projects/t212-quant && set -a && . ./.env && set +a && .venv/bin/t212q rebalance --env live --execute --yes >> state/cron.log 2>&1
```

它会在每月 1–7 号每天 10:00 运行：周末被交易时段检查挡掉，当月第一次成功下单后，其余几天被"每月一次"检查挡掉。

## 注意

- **不要在同一个账户里手动持有策略用到的 11 只 ETF**（Pie 里的除外）：机器人会把它们当成自己的仓位来调整。想长期持有 VUSA 之类的，放在另一个账户（比如策略跑在 ISA，自己的长期持仓放 Invest 账户），或者从标的池里去掉它。
- 行情缓存 12 小时后自动更新；想立刻重新下载，加 `--refresh`。

## 已知限制

- Trading 212 API 仍是 Beta，接口可能变化。实盘只支持市价单。
- Yahoo Finance 免费数据偶尔有错。代码会修复常见的"便士/英镑 100 倍"错误，但不能保证修复所有问题。
- 受 ETF 上市时间限制（ERNS、VUSA 等 2012–2013 年上市），回测大约从 2014 年开始，只有十一二年，经历的熊市不多（2020、2022）。
- 回测假设按决策次日收盘价成交，成本 0.10%/笔；实际成交价会有差异。
- 分数股精度默认保留 2 位小数。如果 Trading 212 报数量精度错误，调整 `quantity_decimals`。
