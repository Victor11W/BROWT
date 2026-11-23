# Detection · YOLO11

Input: a game image. Output: classes, scores and pixel bounding boxes.
The historical YOLO11n baseline is preserved. No real accuracy result has been
established in the current repository.

## Setup

Python 3.11 or 3.12:

```text
python -m pip install -e ".[vision,research,dev]"
```

Place the original YOLO export in `data/dataset/`, with `data.yaml` and
independently defined `train/`, `valid/`, and final `test/` partitions.
Partition by session/match before training. Adjacent video frames must not be
randomly split across training and test.

## Audit, train, evaluate

```text
python -m BROWT audit --dataset data/dataset
python -m BROWT.detection.train --smoke
python -m BROWT.detection.train
python -m BROWT.detection.report --weights path/to/weights/best.pt --split val
```

Training does not move source images or replace the source YAML.
Each run has a unique directory, manifest, resolved dataset YAML and checkpoint.
A smoke run uses one epoch and a training subset. It cannot publish weights or
evaluate the held-out test.

Normal training uses training/validation only. After the protocol is frozen,
explicitly use `--evaluate-test` or evaluate a checkpoint with `--split test`.

To copy a completed run's weights to the configured model directory, training
accepts `--publish`. Existing published weights are never replaced. Inference
can also load the best checkpoint directly from its run directory.

## Inference

```python
from BROWT.detection import Detector

detector = Detector("path/to/weights/best.pt")
for detection in detector.detect("path/to/frame.png"):
    print(detection.cls_name, detection.confidence, detection.center)
```

The label-index mapping comes from the actual dataset export, not a hardcoded
alphabetical class order.

## Results

Evaluation writes a new run with JSON metrics, Markdown and provenance.
Updating this README requires the explicit `--update-readme` option.

<!-- RESULTS:START -->
No real dataset/checkpoint evaluation has been archived yet.
<!-- RESULTS:END -->

Class absence is reported as not evaluated, never as zero performance.
The latency reported by Ultralytics covers inference, not the complete
capture–decision–action loop.
