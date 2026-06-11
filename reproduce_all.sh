#!/usr/bin/env bash
# 一键复现 Qwen 白盒贴片攻击的全部数据与结论。
# 单卡顺序执行 —— 跑之前确认没有别的 GPU 任务(nvidia-smi 应≈0)。全程约 30-35 分钟。
set -e
cd ~/VLM
PY=~/miniconda3/envs/vlm/bin/python
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
ts(){ date +%H:%M:%S; }

echo "[$(ts)] ===== 0/5 预处理逐位对齐 + 反传自检 (预期: max|diff|=0.000e+00 ; loss 下降 ; CHECK_OK) ~1min"
$PY train_qwen_decision_patch.py --check

echo "[$(ts)] ===== 1/5 白盒训练 in-sample (预期: 每个 epoch STOP_rate=1.000) ~5min"
$PY train_qwen_decision_patch.py --epochs 8

echo "[$(ts)] ===== 2/5 留出泛化训练 (预期: train/test 内容0重叠 ; HELDOUT_STOP 0.575 -> 1.000) ~10min"
$PY train_qwen_heldout.py --epochs 8

echo "[$(ts)] ===== 3/5 决策五行表 (预期: clean .150 / 迁移 .575 / 重训 .375 / 作者 .475 / 白盒 1.000) ~6min"
$PY eval_decision.py

echo "[$(ts)] ===== 4/5 归因四行表 (预期: 迁移真攻击~.075 ; 白盒感知1.000/真攻击~.70-.90) ~10min"
$PY eval_attr_full.py

echo "[$(ts)] ===== 全部完成。补丁: outputs_qwen_whitebox/ 与 outputs_qwen_heldout/ ====="
