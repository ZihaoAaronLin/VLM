# HANDOFF — VLM 项目（截至本 session 末）

## 一句话状态
攻击研究线**已穷尽并收口**：白盒 100% → 集成跨架构迁移（InternVL2 .675 = 干净头条）→ **决策墙**（.675 带机制上限，五种突破全败）→ **物理鲁棒 patch 已训好并 sanity 过**。下一步**主要在用户手上**：打印 `ensemble_phys.png`、拍白墙/室内/室外三套、跑 `eval_physical.py` 出物理 ASR。全部已 push GitHub `cuda-repro`（最新 commit `d923b50`）。

## 本 session 已完成
1. **跨架构迁移框架 + 污染审计**（编码器轴=真泄漏 / 家族轴=近亲混淆，分开谈）；统一表 `cross_vlm_table.{md,csv}`（`make_cross_vlm_table.py` 从已有 outputs 收集，CPU、不重下模型）。
2. **消融坐实**：跨到陌生 VLM 靠"编码器多样性(集成)"或"表征对齐 surrogate(SigLIP↔InternViT)"；单个不匹配编码器(CLIP-B/32)即便用 for-transfer 管线也失败。
3. **决策墙 + 五种突破尝试全败**（risk门/action门/joint/决策文本/危险物）—— 见 CLAUDE.md「攻击边界」+ `TRANSFER_REPORT_ensemble.md` §9。
4. **物理鲁棒 patch**：`train_ensemble_patch.py --phys-eot --patch-size 96 --epochs 15` → `outputs_ensemble_phys/ensemble_phys.png`。训练时全程透视/缩放/模糊/gamma/光照下 train_asr ~0.94–1.0。数字 sanity(InternVL2)：**sign .975 / action .325**（感知保住、动作用潜力换鲁棒，**符合预期，别当失败**）。
5. **新脚本并 push**：`eval_physical.py`（手机照片→物理 ASR）、`demo_attack.py`（CLEAN→PATCHED，sign 4/4 No→Yes）、`make_cross_vlm_table.py`、`attack_query_logprob.py`、`attack_query_joint.py`、`eval_transfer_internvl.py`、`eval_clip_transfer.py`、`analyze_encoder_shift.py`、`train_ensemble_patch.py(--phys-eot)`。
6. **存储**：删了 4 个测完的 HF 模型缓存(SmolVLM/LLaVA/Qwen2.5-VL/Qwen2-VL-2B，**释放 47G**)；备份先 push 到 GitHub 再删，零损失。**别重下**（见 CLAUDE.md）。InternVL2 + open_clip 编码器缓存保留。

## 当前结果快照（数值真源 = `cross_vlm_table.csv`）

| VLM | patch | clean(act) | random(act) | Qwen单模型(act) | 集成 sign/act/risk | 合取(act) [CI] | 定位 |
|---|---|---|---|---|---|---|---|
| InternVL2-2B | div5 | .05 | .025 | .05 | 1.00 / .68 / .00 | **.675 [.52,.80]** | ✅ 干净跨架构（头条） |
| Qwen2-VL-2B | ens4 | .15 | .15 | .15 | .97 / .82 / .45 | .800 [.65,.90] | ⚠️ 同族上界（非跨族） |
| SmolVLM | ensemble3 (no-SigLIP) | .05 | .275 | .15 | .00 / .80 / .75 | .000 ⚠️ | 跨 LM；解离（看 action 不看 conj） |
| LLaVA-OV | ensemble3 (no-SigLIP) | .05 | .175 | .15 | .45 / .42 / .95 | .250 [.14,.40] | 污染 → 附录 |

**物理 patch**（`ensemble_phys.png`，96px，数字域）：InternVL2 **sign .975 / action .325**（合取 ~.325）。物理 ASR 待用户拍摄后测。

**攻击边界（.675 天花板，五种突破全败）**：

| 尝试 | 结果 | 机制 |
|---|--:|---|
| 查询 risk 门 | 不翻 | 守护推理硬 |
| 查询 action 门 | 合取 .30 | 刷代理、毁 sign |
| 查询 joint min | .675 | div5 局部最优 |
| 决策文本(抽象) | .05 | 概念不 transfer |
| 危险物(行人) | action .025 | 感知成功但→谨慎非急停 |

## 下一步（按顺序，主要用户动手）
1. `git pull` 取 `ensemble_phys.png` → 打印（正方形 15–18cm、全彩 sRGB、哑光、实际尺寸不平滑）。
2. 拍三套（白墙/室内/室外）：patch 占框 1/4–1/3 居中、正面±12°、光照匀，每套多张。
3. 照片传 `~/VLM/physical_photos/<scene>/` → `CUDA_VISIBLE_DEVICES=1 python eval_physical.py --photo-dir physical_photos/wall`（室内/室外同理）。
4. 物理 ASR 贴回来分析；物理掉太多 → 迭代 phys-EOT（加大 patch / 更激进透视·光照重训）。
5.（可选）写论文初稿：白盒→迁移→决策墙→物理；或把复现指南落成 `REPRODUCE.md`。

## 锁定决策（别重吵，细节见 CLAUDE.md 报告铁律）
- 头条 = **InternVL2 .675**（干净跨架构）；**Qwen2-VL-2B .800 = 同族上界参照，不当跨族成功报**。
- **SmolVLM 用 action(.80)+解离报，不用合取**（sign=.00 会把合取归零、误导）。
- LLaVA-OV 放附录、注明两轴污染。
- 主表每行用对该目标最干净的 patch，**表注解释**为什么不同模型用不同 patch。
- **.675 是带机制天花板**；查询/概念攻击五种全败 → 攻击线收口（这是结论，不是待办）。
- 物理鲁棒 patch 数字 action .325 偏低**是正常**（拿潜力换鲁棒）；考卷是打印拍回来。

## 环境/连接（每次都遇到，详见 CLAUDE.md「踩坑」）
- `ssh -M -S ~/.ssh/cm-w6908.sock -o ControlPersist=12h -o ServerAliveInterval=60 -o ServerAliveCountMax=120 -fN w6908`
- master 老掉 + sshgate 限流 → **别狂重试**（停 ~20min 冷却）；远程任务"scp+push+detached 一条命令"发起再轮询；nohup 内 echo 的括号必须去掉/转义；别重下被删的 ~41G VLM。
- 进程（本 session）：phys 训练 PID 4288 → `qphys.log`（已完成；首次 PID 4034 因括号语法错静默挂掉）。
