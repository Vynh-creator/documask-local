"""Recall test harness — главный тест продукта.

Загружает фикстуры (изображение + эталонные боксы ПДн), прогоняет детекцию,
считает RECALL (покрытие эталонных боксов предсказанными).

Порог recall = 0.95. Упал ниже → сборка красная, продукт не готов.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

from documask.core.detectors import ocr_words, RegexDetector, NerDetector, YoloDetector
from documask.core.merge import combine, add_padding
from documask.core.pdf_io import load_any
from documask.schemas import Box, PiiKind, DetectorSource
from documask.config import settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RECALL_TARGET = 0.95
COVER_IOU = 0.3


def _make_box(gt: dict) -> Box:
    """Convert ground truth dict to Box for comparison."""
    return Box(
        x1=gt["x1"], y1=gt["y1"], x2=gt["x2"], y2=gt["y2"],
        page=gt.get("page", 0),
        kind=PiiKind(gt["kind"]),
        source=DetectorSource.MANUAL,
        score=1.0,
        text=gt.get("text", ""),
    )


def _box_cover_ratio(gt_box: Box, pred_boxes: list[Box]) -> float:
    """How much of gt_box is covered by the union of pred_boxes.

    Returns ratio of (intersection area) / (gt area) for the union of
    all pred_boxes that overlap with gt_box. A value >= COVER_IOU means
    the ground truth PII is considered 'caught'.
    """
    if not pred_boxes:
        return 0.0

    gt_area = gt_box.area()
    if gt_area == 0:
        return 1.0

    covered_x1, covered_y1 = gt_box.x2, gt_box.y2
    covered_x2, covered_y2 = gt_box.x1, gt_box.y1

    for pb in pred_boxes:
        if pb.page != gt_box.page:
            continue
        inter_x1 = max(gt_box.x1, pb.x1)
        inter_y1 = max(gt_box.y1, pb.y1)
        inter_x2 = min(gt_box.x2, pb.x2)
        inter_y2 = min(gt_box.y2, pb.y2)
        if inter_x1 < inter_x2 and inter_y1 < inter_y2:
            covered_x1 = min(covered_x1, inter_x1)
            covered_y1 = min(covered_y1, inter_y1)
            covered_x2 = max(covered_x2, inter_x2)
            covered_y2 = max(covered_y2, inter_y2)

    cover_w = max(0, covered_x2 - covered_x1)
    cover_h = max(0, covered_y2 - covered_y1)
    cover_area = min(cover_w * cover_h, gt_area)
    return cover_area / gt_area


def _run_detection(img: np.ndarray, page: int) -> list[Box]:
    """Run all detectors on one page image, merge + pad."""
    words = ocr_words(img, page)

    regex = RegexDetector()
    boxes = regex.detect(words, page)

    try:
        ner = NerDetector()
        boxes += ner.detect(words, page)
    except Exception:
        pass

    yolo = YoloDetector()
    boxes += yolo.detect(img, page)

    h, w = img.shape[:2]
    boxes = combine(boxes)
    boxes = add_padding(boxes, settings.mask_padding_px, w, h)
    return boxes


def _load_fixture(image_path: Path) -> Optional[tuple[np.ndarray, list[Box]]]:
    """Load one fixture: image + its ground truth JSON."""
    json_path = image_path.with_suffix(".json")
    if not json_path.exists():
        return None

    pages = load_any(image_path)
    if not pages:
        return None
    img = pages[0]

    gt_data = json.loads(json_path.read_text(encoding="utf-8"))
    gt_boxes = [_make_box(b) for b in gt_data.get("boxes", [])]
    return img, gt_boxes


def _compute_recall(gt_boxes: list[Box], pred_boxes: list[Box]) -> dict:
    """Compute per-kind and overall recall."""
    if not gt_boxes:
        return {"overall": 1.0, "per_kind": {}, "total_gt": 0, "covered": 0}

    covered = 0
    per_kind_gt: dict[str, int] = {}
    per_kind_covered: dict[str, int] = {}

    for gt in gt_boxes:
        kind = gt.kind.value
        per_kind_gt[kind] = per_kind_gt.get(kind, 0) + 1
        if _box_cover_ratio(gt, pred_boxes) >= COVER_IOU:
            covered += 1
            per_kind_covered[kind] = per_kind_covered.get(kind, 0) + 1

    per_kind = {}
    for kind, total in per_kind_gt.items():
        cov = per_kind_covered.get(kind, 0)
        per_kind[kind] = cov / total if total > 0 else 1.0

    return {
        "overall": covered / len(gt_boxes),
        "per_kind": per_kind,
        "total_gt": len(gt_boxes),
        "covered": covered,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _discover_fixtures() -> list[Path]:
    """Find all image fixtures with matching JSON ground truth."""
    if not FIXTURES_DIR.exists():
        return []
    fixtures: list[Path] = []
    for img_path in sorted(FIXTURES_DIR.glob("*.png")):
        if img_path.with_suffix(".json").exists():
            fixtures.append(img_path)
    for img_path in sorted(FIXTURES_DIR.glob("*.jpg")):
        if img_path.with_suffix(".json").exists():
            fixtures.append(img_path)
    for img_path in sorted(FIXTURES_DIR.glob("*.pdf")):
        if img_path.with_suffix(".json").exists():
            fixtures.append(img_path)
    return fixtures


@pytest.mark.parametrize("image_path", _discover_fixtures())
def test_page_recall(image_path: Path):
    """Recall >= RECALL_TARGET for each fixture page."""
    result = _load_fixture(image_path)
    if result is None:
        pytest.skip(f"No ground truth for {image_path.name}")
    img, gt_boxes = result

    pred_boxes = _run_detection(img, 0)
    metrics = _compute_recall(gt_boxes, pred_boxes)

    print(f"\n  {image_path.name}: {metrics['covered']}/{metrics['total_gt']} "
          f"({metrics['overall']:.1%})")
    for kind, r in metrics["per_kind"].items():
        print(f"    {kind}: {r:.1%}")

    if metrics["total_gt"] > 0:
        assert metrics["overall"] >= RECALL_TARGET, (
            f"Recall {metrics['overall']:.1%} below target {RECALL_TARGET:.1%} "
            f"({metrics['covered']}/{metrics['total_gt']})"
        )
    else:
        pytest.skip("No ground truth boxes")


def test_aggregate_recall():
    """Global recall across all fixtures >= RECALL_TARGET."""
    fixtures = _discover_fixtures()
    if not fixtures:
        pytest.skip("No fixtures found")

    total_gt = 0
    total_covered = 0

    for img_path in fixtures:
        result = _load_fixture(img_path)
        if result is None:
            continue
        img, gt_boxes = result
        pred_boxes = _run_detection(img, 0)
        metrics = _compute_recall(gt_boxes, pred_boxes)
        total_gt += metrics["total_gt"]
        total_covered += metrics["covered"]

    if total_gt == 0:
        pytest.skip("No ground truth boxes in any fixture")

    global_recall = total_covered / total_gt
    print(f"\n  GLOBAL RECALL: {total_covered}/{total_gt} ({global_recall:.1%})")
    assert global_recall >= RECALL_TARGET, (
        f"Global recall {global_recall:.1%} below target {RECALL_TARGET:.1%}"
    )