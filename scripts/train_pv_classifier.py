#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PlantVillage 分类器训练脚本（在 Colab / 有 GPU 的机器上跑，不在本机跑）。

产出：
    pv_classifier.onnx   → 放到项目 models/ 下，后端自动启用（settings.classifier_enabled）
    训练日志（每类准确率）→ 用于判断哪些类不值得信任

数据集：Kaggle "New Plant Diseases Dataset"（PlantVillage 增强版，38 类，
train/ 与 valid/ 两个目录按类分文件夹）。下载地址见实施问题清单 D32。

用法（Colab）：
    !pip install torch torchvision onnx onnxruntime
    !python train_pv_classifier.py --data /content/new_plant_diseases_dataset \
        --epochs 8 --out /content/out
训练完成后把 out/pv_classifier.onnx 与项目里 models/pv_labels.json 配对使用
（labels 顺序 = sorted(labels.keys())，与 _load_label_map() 一致 —— 两边改一处必须同步改另一处）。

设计要点：
  - ResNet18 迁移学习：38 类 5 万张图，CPU 之外随便什么 GPU 都够；
  - 预处理与推理端严格一致：resize 224×224 + ImageNet 归一化（见
    app/services/classifier.py 的 _preprocess —— 两边改一处必须同步）；
  - valid 集逐类准确率写进 out/per_class_report.txt：**田间实拍与温室图
    存在 domain gap，逐类准确率是判断「哪些类可信」的唯一依据**，
    上线前应按它调整 pv_labels.json 的映射（低分类从映射里摘掉）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True,
                        help="数据集根目录（内含 train/ valid/，按 38 个类名分文件夹）")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch", type=int, default=128)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--out", default="out")
    args = parser.parse_args()

    import numpy as np
    import torch
    import torch.nn as nn
    from torchvision import datasets, models, transforms
    from torch.utils.data import DataLoader

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    tf_train = transforms.Compose([
        transforms.Resize((224, 224)),          # ★ 与推理端一致，别加随机裁剪之外的戏
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(brightness=0.2, contrast=0.2),   # 缓解温室图 domain gap
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    tf_eval = transforms.Compose([
        transforms.Resize((224, 224)),          # ★ 与推理端一致
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])

    train_ds = datasets.ImageFolder(Path(args.data) / "train", tf_train)
    valid_ds = datasets.ImageFolder(Path(args.data) / "valid", tf_eval)
    train_dl = DataLoader(train_ds, batch_size=args.batch, shuffle=True,
                          num_workers=4, pin_memory=True)
    valid_dl = DataLoader(valid_ds, batch_size=args.batch, shuffle=False,
                          num_workers=4, pin_memory=True)
    classes = train_ds.classes
    print(f"类别数 {len(classes)}，训练 {len(train_ds)} 张，验证 {len(valid_ds)} 张")

    model = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1)
    model.fc = nn.Linear(model.fc.in_features, len(classes))
    model.to(device)

    crit = nn.CrossEntropyLoss()
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    # 4GB 显存（RTX 3050 Laptop）实测：FP32 batch 48 贴边，AMP 既是显存保险也是速度
    scaler = torch.amp.GradScaler(device.type)

    best_acc = 0.0
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = correct = 0
        for imgs, ys in train_dl:
            imgs, ys = imgs.to(device), ys.to(device)
            opt.zero_grad()
            with torch.autocast(device_type=device.type,
                                enabled=device.type == "cuda"):
                loss = crit(model(imgs), ys)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            with torch.no_grad():
                correct += (model(imgs).argmax(1) == ys).sum().item()
            total += ys.numel()
        sched.step()
        train_acc = correct / max(total, 1)

        model.eval()
        vtotal = vcorrect = 0
        with torch.no_grad():
            for imgs, ys in valid_dl:
                imgs, ys = imgs.to(device), ys.to(device)
                vcorrect += (model(imgs).argmax(1) == ys).sum().item()
                vtotal += ys.numel()
        val_acc = vcorrect / max(vtotal, 1)
        print(f"epoch {epoch}: train {train_acc:.4f}  valid {val_acc:.4f}")
        if val_acc > best_acc:
            best_acc = val_acc
            torch.save(model.state_dict(), out_dir / "best.pt")

    # ---- 导出 ONNX（CPU 推理用）----
    model.load_state_dict(torch.load(out_dir / "best.pt", weights_only=True))
    model.eval().to("cpu")
    dummy = torch.randn(1, 3, 224, 224)
    torch.onnx.export(model, dummy, str(out_dir / "pv_classifier.onnx"),
                      input_names=["input"], output_names=["logits"],
                      dynamic_axes={"input": {0: "batch"}})

    # ---- 逐类准确率报告：决定 pv_labels.json 里哪些映射可保留 ----
    per_class = {c: [0, 0] for c in classes}
    model.to(device)
    with torch.no_grad():
        for imgs, ys in valid_dl:
            preds = model(imgs.to(device)).argmax(1).cpu().numpy()
            for p, y in zip(preds, ys.numpy()):
                per_class[classes[int(y)]][1] += 1
                per_class[classes[int(p)]][0] += int(p == y)
    lines = []
    for c, (hit, tot) in sorted(per_class.items()):
        acc = hit / max(tot, 1)
        lines.append(f"{c}\t{acc:.4f}\t{hit}/{tot}")
    (out_dir / "per_class_report.txt").write_text("\n".join(lines), encoding="utf-8")

    # labels 顺序必须与后端 _load_label_map() 的 sorted(labels.keys()) 一致
    (out_dir / "labels.json").write_text(
        json.dumps(sorted(classes), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：best valid acc {best_acc:.4f}，产物在 {out_dir}/")


if __name__ == "__main__":
    main()
