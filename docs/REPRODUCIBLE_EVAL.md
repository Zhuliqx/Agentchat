# 可复现评估（公开示例语料）

> 目的：让任何人 clone 仓库后，不依赖私有语料即可复现一套**真实、确定性的检索基线**。
> 示例语料（`data/kb/`，5 个文件 / 20 块）与评估集（`data/eval/ground_truth.json`，14 问）均已入库。

## 实测基线（默认配置）

| 指标 | 值 |
|---|---|
| MRR | **1.000** |
| Hit@1 / Hit@3 / Hit@5 | **1.000 / 1.000 / 1.000** |
| 检索管线 | 混合检索（向量 + BM25 + RRF）+ rerank 精排（生产同路径） |
| 案例构成 | 事实 / 表格价格 / 对比 / 列举 / 口语 / 否定 / 规格 / 政策，共 14 问 |

> 该基线在示例语料上**确定性可复现**（检索级无 LLM、无随机性）。
> 注意口径区分：文档中的完整语料基线（MRR 0.963 等）来自**私有评估语料**（`docs/README.md` 唯一基线表），
> 与这里的示例语料基线是两套数据，**不可互相换算**。

## 复现步骤

```powershell
# 1) 启动依赖（Postgres + Milvus），参照 docs/SETUP.md；安装后端依赖
cd backend
copy .env.example .env        # 按需填写 DEEPSEEK_API_KEY（检索级评估不需要 LLM key）

# 2) 初始化数据库与向量库
python scripts/init_db.py

# 3) 摄入示例语料（5 个文件）
python scripts/ingest_docs.py ..\data\kb --user default

# 4) 跑检索级评估（无需 LLM key；首次会加载 embedding/rerank 模型）
python scripts/eval_rag.py --dataset ..\data\eval\ground_truth.json --user default
```

预期输出：`MRR=1.000  Hit@1=1.000  Hit@3=1.000  Hit@5=1.000`，结果 JSON 存到 `backend/data/eval/`。

## 端到端四指标（需 LLM key，结果非确定）

```powershell
python scripts/eval_quality.py --dataset ..\data\eval\ground_truth.json --user default --max-cases 14
```

四指标（Precision / Recall / Faithfulness / Relevancy）由 LLM-judge 打分，受生成随机性影响，
同一语料不同轮次会有波动——所以文档只承诺**检索级数字可复现**，端到端给方法与工具，不给承诺值。

## 语料与评估集设计

- `data/kb/`：company（事实）/ products（套餐与价格表、对比、私有化部署要求）/ faq（试用/退款/客服/部署方式）/
  policies（数据存储/保留期/合规）/ api（版本、鉴权、限流、接口）。
- `data/eval/ground_truth.json`：14 问，覆盖 8 类考察点；`expected_sources` 按文件名子串匹配
  （与 `scripts/eval_rag.py` 的 source 模式判定一致）。
- 新增语料/用例的约定：问题必须有唯一出处；新增用例后跑一次 `eval_rag` 更新本文件数字。
