"""图片数据工程：生成含图 PDF（柱状图 + 架构图），开启图文双通道摄入 → 图向量 collection。

用途：为 P1#2（image_channel_weight / image_channel_top_k 调参）准备含图真实 GT。
产物：data/eval/img_source/*.png|*.pdf；摄入后 agent_images 有对应 source 的图向量。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

from app.config import settings  # noqa: E402
from app.rag import image_parser, vector_store  # noqa: E402
from app.rag.embedding import get_image_embedder  # noqa: E402
from app.rag.ingestion import ingest_file  # noqa: E402


def _chart(p: Path) -> None:
    img = Image.new("RGB", (800, 500), "white")
    d = ImageDraw.Draw(img)
    d.text((250, 30), "年度营收(万元)", fill="black")
    data = [("2021", 100), ("2022", 150), ("2023", 200)]
    x, base = 120, 420
    for label, val in data:
        h = int(val * 1.5)
        d.rectangle([x, base - h, x + 100, base], fill="steelblue", outline="black")
        d.text((x + 30, base - h - 24), str(val), fill="black")
        d.text((x + 30, base + 8), label, fill="black")
        x += 180
    img.save(p, "PNG")


def _arch(p: Path) -> None:
    img = Image.new("RGB", (800, 500), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([40, 60, 760, 140], fill="lightblue", outline="black")
    d.text((250, 92), "Supervisor 编排层", fill="black")
    d.rectangle([60, 200, 360, 300], fill="lightgreen", outline="black")
    d.text((120, 244), "Planner 规划", fill="black")
    d.rectangle([440, 200, 740, 300], fill="lightyellow", outline="black")
    d.text((500, 244), "Tools 工具", fill="black")
    img.save(p, "PNG")


def main() -> None:
    root = ROOT / "data" / "eval" / "img_source"
    root.mkdir(parents=True, exist_ok=True)
    chart = root / "chart.png"
    arch = root / "arch.png"
    _chart(chart)
    _arch(arch)

    pdf = root / "annual_report_2023.pdf"
    doc = fitz.open()
    for p, cap in [(chart, "年度营收趋势图"), (arch, "系统架构图")]:
        page = doc.new_page(width=612, height=792)
        page.insert_image(fitz.Rect(60, 110, 552, 480), filename=str(p))
        page.insert_text(fitz.Point(60, 75), cap, fontsize=18)
    doc.save(str(pdf))
    doc.close()

    imgs = image_parser.extract_pdf_images(pdf)
    print("extracted_pdf_images:", len(imgs))

    # 纯图文双通道（不 OCR、不 VLM，专测图向量通道）
    settings.image_dual_channel = True
    settings.image_ocr_enabled = False
    settings.image_vlm_enabled = False

    result = ingest_file(pdf, filename=pdf.name, user_id="default")
    print("ingest.source:", result.get("source"))
    print("ingest.chunks:", result.get("chunks"))

    # 校验图向量：用文本 query 走图像通道，看该 source 的图能否被召回
    source = str(pdf.resolve())
    try:
        embedder = get_image_embedder()
        qvec = embedder.encode_text("年度营收最高年份")
        hits = vector_store.search_image(qvec, top_k=6, user_id="default")
        print("image_hits_for_query:", [(h.get("source"), h.get("image_index"), h.get("score")) for h in hits])
        target = [h for h in hits if h.get("source") == source]
        print("matched_target_images:", [(h.get("image_index"), h.get("score")) for h in target])
    except Exception as exc:  # noqa: BLE001
        print("image_search_check_failed:", exc)


if __name__ == "__main__":
    main()
