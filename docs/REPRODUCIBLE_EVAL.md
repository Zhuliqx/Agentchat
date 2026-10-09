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
python scripts/eval_hallu.py --user hallueval --top-k 6 --no-rerank --out hallu_retrieval_200.json
python scripts/eval_hallu.py --user hallueval --no-retrieval --out hallu_no_retrieval_200.json
```

幻觉纠正实测（2026-10-08/09，官方 prompt 模板，抽样 200/1268，top_k 6；本机 6GB 显存与后端
共卡时 rerank 会降级，以下数字为"无精排"口径）：

| 指标 | 有检索 | 无检索基线 |
|---|---|---|
| 纠正率 / 残留虚假信息 | **0.830 / 0.085** | 0.485 / 0.160 |
| 坏词残留率（命中数据集标注的"不合理"关键词） | **0.060** | 0.240 |
| BLEU-avg / ROUGE-L（对人工修订参考） | **0.279 / 0.423** | 0.212 / 0.345 |
| 正确续写检索命中率 | 0.905 | — |

分层证据：检索命中的 181 条上，纠正率 0.862、坏词残留 0.050（无检索同题 0.514 / 0.232）；
检索未命中的 19 条掉回 0.526、残留虚假信息 0.421——收益集中在"检索到证据"的题上。
局限：judge 与生成器同为 DeepSeek（同源偏好，相对变化可信、绝对值仅供参考）；抽样 200 条；
评测为"无精排"口径（本机 6GB 显存与后端共卡，rerank 会降级；正确续写命中率不受影响）。

全量 1268 条（2026-10-09，`--no-rerank`，与抽样档同为"无精排"口径）：
无检索基线 **1268/1268 完成**（纠正率 0.477 / 残留 0.146 / 坏词 0.221 / BLEU 0.197 / ROUGE-L 0.328）；
有检索 652/1268 条有效（纠正率 0.818 / 残留 0.116 / 坏词 0.101 / 正确续写命中 0.888）。

文档池规模阶梯（1doc 检索；800 篇目标文档固定，池子由 `build_scale_pool.py` 生成并剔除与目标重复的新闻）：

| 文档池（篇 / 块） | MRR | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|---|
| 800 / 1,074（仅目标，基线） | 0.858 | 0.817 | 0.897 | 0.917 |
| 2,000 / 2,669 | 0.818 | 0.770 | 0.867 | 0.873 |
| 10,000 / 13,145 | 0.581 | 0.557 | 0.603 | 0.607 |
| 64,420 / 82,210（全池） | 0.444 | 0.380 | 0.490 | 0.547 |

```powershell
$env:RERANK_CANDIDATE_K = "12"   # 与实测口径一致（.env.example 默认候选为 6）
python scripts/build_scale_pool.py --src <上游>\80000_docs --out <池>\p1200 --limit 1200 --exclude-dir ..\data\eval_corpus\crud_1doc
python scripts/ingest_docs.py <池>\p1200 --user scale1_2k --batch
python scripts/eval_rag.py --dataset ..\data\eval_corpus\crud_1doc_gt_sample.json --user scale1_2k --top-k 6
```

> 摄入注意（2026-10-09 更新）：`batch_ingest` 已内置 PG 分批提交（`PG_INSERT_BATCH = 1 万行`），
> 全池一次 batch 摄入验证通过（63,620 篇 / 81,136 块 → PG 全 synced、Milvus 81,136 条）。
> 若中途中断留下 pending，调度器的对账任务是逐 source 补写（8 万级过慢）；批量补同步思路：
> 按 source 分组、每批约 1 万行（批量嵌入 → 一次 `add_chunks_multi` → 批量标 synced）。
> 历史：单事务 8.5 万块的旧实现曾让 PG 写入卡死 20 分钟以上。

## 外部基准：LongBench 中文子集（长文档 / 多文档检索）

[LongBench](https://github.com/THUDM/LongBench)（THUDM；数据在其 HuggingFace 仓库的 `data.zip`，
各子任务源自其上游数据集）里挑出 3 个"有可判定支撑文档"的中文子集做检索级评测；
vcsum（会议摘要）没有单一支撑段落，不适用检索口径。

```powershell
$env:RERANK_CANDIDATE_K = "12"   # 与实测口径一致（.env.example 默认候选为 6）
python scripts/import_longbench.py --src <解压后的 data 目录>
python scripts/ingest_docs.py ..\data\eval_corpus\longbench_dureader --user lb_dureader --batch
python scripts/eval_rag.py --dataset ..\data\eval_corpus\longbench_dureader_gt.json --user lb_dureader --top-k 6
# multifieldqa_zh / passage_retrieval_zh 同理（换语料目录与 --user）
```

实测（2026-10-08，top_k 6 + rerank 候选 12）：

| 子集（题型） | 语料规模 | 题量 | MRR | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|---|---|---|
| dureader（多文档问答） | 4,000 篇 / 7,761 块 | 148 | 0.563 | 0.345 | 0.723 | 0.919 |
| multifieldqa_zh（长文档问答） | 200 篇 / 2,253 块 | 200 | 0.635 | 0.480 | 0.780 | 0.845 |
| passage_retrieval_zh（段落检索） | 6,000 篇 / 6,000 块 | 200 | 0.485 | 0.255 | 0.720 | 0.860 |

口径：dureader 的支撑文章按"答案探针（去尾标点）精确匹配，否则最长公共子串 ≥12 且严格大于次优"
自动标注，200 题中 148 题可判定（其余答案过于抽象/歧义，跳过）；passage_retrieval_zh 按答案里的
"段落N"直接标注；multifieldqa_zh 的支撑文档即该题自己的长文。命中判定走 `eval_rag` 的 source 模式。
注：LongBench 部分段落的原文含 NUL（0x00）坏字符，PG 拒收——导入器已统一清洗。

## 外部基准：XFUND 中文表单（图文双通道）

[XFUND](https://github.com/doc-analysis/XFUND)（ACL 2021 多语言表单理解）中文子集：50 份真实中文扫描表单
（zh.val）+ 字段键值标注。每份表单转单页 PDF 入库，验证 `IMAGE_DUAL_CHANNEL`（Chinese-CLIP 图片向量
与文本通道融合）。图片与标注在 GitHub Releases v1.0（直连不稳时可给下载 URL 加 `https://gh-proxy.com/` 前缀）：

```powershell
# 下载 zh.val.zip / zh.val.json 并解压后：
python scripts/import_xfund.py --json <...>\zh.val.json --images <...>\images
# 摄入与评测都要开图片通道（默认关）；纯图片 PDF 批量/非批量均可（batch 已支持）
$env:IMAGE_DUAL_CHANNEL="true"
$env:RERANK_CANDIDATE_K = "12"   # 与实测口径一致（.env.example 默认候选为 6）
python scripts/ingest_docs.py ..\data\eval_corpus\xfund_zh --user docvqa_zh
python scripts/eval_rag.py --dataset ..\data\eval_corpus\xfund_zh_title_gt.json --user docvqa_zh --top-k 6
python scripts/eval_rag.py --dataset ..\data\eval_corpus\xfund_zh_gt.json --user docvqa_zh --top-k 6
```

实测（2026-10-08，top_k 6 + rerank 候选 12）：

| 查询类型 | 规模 | MRR | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|---|---|
| 表单标题/字段组合（语义查询） | 50 | 0.752 | 0.680 | 0.760 | 0.880 |
| 字段名（"姓名:" 等，跨 50 份相似表单） | 300（抽样） | 0.206 | 0.113 | 0.250 | 0.380 |
| 字段名（同一 300 条，图片通道关闭） | 300 | 0.000 | 0.000 | 0.000 | 0.000 |

结论与边界：
- 图片通道对"纯图片文档"是**唯一通路**（关闭即 0 命中）；语义查询下 MRR 0.75，通道有效。
- 细粒度"图内文字"检索偏弱（字段名跨表单天然歧义 + CLIP 不擅长细小文字）——这类场景应打开
  `IMAGE_OCR_ENABLED`（图内文字抽成文本块）或加 VLM 描述，而不是只靠图片向量。
- 工程注意：批量摄入已支持纯图片文档（图文双通道下保留空文本块计划、只写图片向量）。
  验证：batch 摄入 50 份表单 → 50 篇 / 0 块 / 0 失败 + 50 张图片向量，标题 GT 检索结果与
  非批量路径完全一致（MRR 0.752 / Hit@5 0.880）；此前 batch 会静默跳过这类文档（0 篇 / 0 块）。

## 语料与评估集设计

- `data/kb/`：company（事实）/ products（套餐与价格表、对比、私有化部署要求）/ faq（试用/退款/客服/部署方式）/
  policies（数据存储/保留期/合规）/ api（版本、鉴权、限流、接口）。
- `data/eval/ground_truth.json`：14 问，覆盖 8 类考察点；`expected_sources` 按文件名子串匹配
  （与 `scripts/eval_rag.py` 的 source 模式判定一致）。
- 新增语料/用例的约定：问题必须有唯一出处；新增用例后跑一次 `eval_rag` 更新本文件数字。

## CI 里的用法

CI 的两个评估 job（`rag-quality` / `rag-regression`）用的就是**这套**语料 + 评估集，
逐 job 对照与"语料必须与 GT 成对"的约定见 [EVAL.md §CI 里怎么跑](EVAL.md)。
