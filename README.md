# cc-workspace

工作区按用途分为三类：

| 目录 | 放什么 |
|---|---|
| [`projects/`](projects/) | 可运行的代码项目，每个项目一个子目录，各自带 README |
| [`research/`](research/) | 研究笔记、资料整理、信息追踪（不含可运行代码） |
| [`archive/`](archive/) | 不再维护但想保留的内容 |

## 当前内容

| 项目 | 说明 |
|---|---|
| [`projects/t212-quant`](projects/t212-quant/) | 趋势过滤的动量 ETF 轮动策略，含回测和 Trading 212 自动下单 |

## 历史文件

整理前仓库里只剩一个空目录：之前的 `LICENSE` 和 `nature_latest.md`（Nature 研究动态追踪）已在早先的提交中删除。需要的话可以从 git 历史恢复，例如：

```bash
git show abd03a0:nature_latest.md > research/nature_latest.md
```
