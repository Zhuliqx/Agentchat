# 可复现评估（公开示例语料）

> 评测资产总索引（GT 清单 / 脚本 / 产出目录 / CI 用法）见 [EVAL.md](EVAL.md)。

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

预期输出：`MRR=1.000  Hit@1=1.000  Hit@3=1.000  Hit@5=1.000`，结果 JSON 存到 `backend/data/eval_runs/`。

## 端到端四指标（需 LLM key，结果非确定）

```powershell
python scripts/eval_quality.py --dataset ..\data\eval\ground_truth.json --max-cases 14
```

四指标（Precision / Recall / Faithfulness / Relevancy）由 LLM-judge 打分，受生成随机性影响，
同一语料不同轮次会有波动——所以文档只承诺**检索级数字可复现**，端到端给方法与工具，不给承诺值。

## 外部基准：CRUD-RAG（幻觉抵抗 + 文档池规模）

[CRUD-RAG](https://github.com/IAAR-Shanghai/CRUD_RAG)（Apache-2.0）提供带人工修订参考的幻觉纠正任务和 8 万篇新闻池。
语料在本地生成（`data/eval_corpus/`，已 gitignore；先按上游 README 把数据下载到 `<上游目录>`）：

```powershell
python scripts/import_crud_rag.py  --src <上游目录> --task 1docs     # 2docs / 3docs 同理
python scripts/import_crud_hallu.py --src <上游目录>
python scripts/ingest_docs.py ..\data\eval_corpus\crud_hallu_db --user hallueval --batch
python scripts/eval_hallu.py --user hallueval --top-k 6 --out hallu_retrieval_200.json
python scripts/eval_hallu.py --user hallueval --no-retrieval --out hallu_no_retrieval_200.json
```

幻觉纠正实测（2026-10-08，官方 prompt 模板，抽样 200/1268，top_k 6 + rerank 候选 12）：

| 指标 | 有检索 | 无检索基线 |
|---|---|---|
| 纠正率 / 残留虚假信息 | **0.830 / 0.085** | 0.485 / 0.160 |
| 坏词残留率（命中数据集标注的"不合理"关键词） | **0.060** | 0.240 |
| BLEU-avg / ROUGE-L（对人工修订参考） | **0.279 / 0.423** | 0.212 / 0.345 |
| 正确续写检索命中率 | 0.905 | — |

分层证据：检索命中的 181 条上，纠正率 0.862、坏词残留 0.050（无检索同题 0.514 / 0.232）；
检索未命中的 19 条掉回 0.526、残留虚假信息 0.421——收益集中在"检索到证据"的题上。
局限：judge 与生成器同为 DeepSeek（同源偏好，相对变化可信、绝对值仅供参考）；抽样 200 条；
本机 6GB 显存与后端共卡时 rerank 可能降级为未精排（正确续写命中率不受影响）。

文档池规模阶梯（1doc 检索；800 篇目标文档固定，池子由 `build_scale_pool.py` 生成并剔除与目标重复的新闻）：

| 文档池（篇 / 块） | MRR | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|---|
| 800 / 1,074（仅目标，基线） | 0.858 | 0.817 | 0.897 | 0.917 |
| 2,000 / 2,669 | 0.818 | 0.770 | 0.867 | 0.873 |
| 10,000 / 13,145 | 0.581 | 0.557 | 0.603 | 0.607 |
| 64,420 / 82,210（全池） | 0.444 | 0.380 | 0.490 | 0.547 |

```powershell
python scripts/build_scale_pool.py --src <上游>\80000_docs --out <池>\p1200 --limit 1200 --exclude-dir ..\data\eval_corpus\crud_1doc
python scripts/ingest_docs.py <池>\p1200 --user scale1_2k --batch
python scripts/eval_rag.py --dataset ..\data\eval_corpus\crud_1doc_gt_sample.json --user scale1_2k --top-k 6
```

> 摄入注意：全池一次批量摄入（单事务约 8.5 万块）在本机出现 PG 写入卡死；拆成每批约 1 万块
> （约 70s/批、零失败）稳定完成。建议后续给 `batch_ingest` 增加分批提交（每批 1-2 万行）。

## 语料与评估集设计

- `data/kb/`：company（事实）/ products（套餐与价格表、对比、私有化部署要求）/ faq（试用/退款/客服/部署方式）/
  policies（数据存储/保留期/合规）/ api（版本、鉴权、限流、接口）。
- `data/eval/ground_truth.json`：14 问，覆盖 8 类考察点；`expected_sources` 按文件名子串匹配
  （与 `scripts/eval_rag.py` 的 source 模式判定一致）。
- 新增语料/用例的约定：问题必须有唯一出处；新增用例后跑一次 `eval_rag` 更新本文件数字。

## CI 里的用法

CI 的两个评估 job（`rag-quality` / `rag-regression`）用的就是**这套**语料 + 评估集，
逐 job 对照与"语料必须与 GT 成对"的约定见 [EVAL.md §CI 里怎么跑](EVAL.md)。
