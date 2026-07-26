# VLM 对抗"停车"补丁 — 白盒攻击 → 跨架构迁移 → 决策墙 → 物理可用

Repo: `github.com/ZihaoAaronLin/VLM` ｜ 工作分支: `cuda-repro`
**白盒 source 模型自始至终 = Qwen2.5-VL-3B-Instruct**（项目"老家"）。

一句话: 在 Qwen2.5-VL-3B 上训一块 **纯噪声**对抗补丁让模型把它当 STOP 标志而停车；研究 (a) 白盒是否真起效、(b) 能否泛化、(c) 能否迁到**真正跨架构**的 VLM、(d) **决策层能否被攻破**、(e) 打印后是否仍生效。

---

## ⚠️ 项目核心承诺：名义攻击 vs 因果攻击（违反这条 = 全错，先读）

模型"停车"可能是 **(a)** 把补丁当 STOP 标志（真起效），也可能是 **(b)** 觉得场景危险而保守地停（场景吓的）。**名义 STOP 率会严重虚高。**
- 解法：测试集**人工剔除所有真实 STOP 标志** ⇒ "模型说看到 STOP 标志" ≡ 补丁起效。
- 这条揭穿了原迁移方法：名义 STOP 57.5%，归因后**真·因标志只有 7.5%**。
- **sign/感知 才是干净指标；action 单独看会骗人。**

---

## 威胁模型 & 测试集

- 补丁: 纯对抗噪声、人眼不像标志、固定居中贴。默认 64×64（物理版 96×96）。
- `data/train/`=100 张训练；`data/test/`=测试（用 **40 张 unique**，md5 与 train **0 重叠**）。测试图已剔除真 STOP 标志。

## 指标（仓库术语，全项目统一）

- **sign** = 感知 ASR（模型答"有 STOP 标志"）。**干净指标，最可信。**
- **action** = STOP-决策 ASR（决定停车）。单独看会被场景混淆。
- **risk** = 风险 ASR（判场景危险；注意 LLaVA clean 基线就高）。
- **conj** = per-image `sign ∧ action`，**Wilson 95% CI**。
- **clean / random(5-seed) / Qwen-single(=heldout_center)** = 三种地板。

---

## 白盒可微管线（核心工程贡献，不变）

- **逐位复刻 Qwen 归一化 + patchify，`max|diff|=0`** → 梯度精确回传到补丁像素；冻结权重只训补丁；异常梯度清零+裁剪+固定种子。
- 效果: 1 epoch → 40/40 STOP；感知 100%。真攻击 **whitebox .725 / heldout(泛化) .625**。
  > ⚠️ **命名坑（易误读）**: `.725`/`.625` 是**两块不同补丁**(`qwen_stop_patch.png` / `qwen_heldout_patch.png`),在 `eval_attr_full.py`→`attribution.csv` 里**都评在同一批 40 张留出 manifest 图上**(各 40 行)。**"in-sample" 只是补丁名、不是评测集**——**不是**"一块补丁 train vs test",也**不是**"在训练图上评"。同批比:whitebox `.725` > 泛化 `.625`(主补丁自己在留出图上就泛化更好,专训泛化补丁收益存疑)。
- 脚本: `train_qwen_perception_patch.py` / `train_qwen_decision_patch.py` / `train_qwen_heldout.py`。

---

## 跨架构迁移框架（最容易写错）

迁移结果有两种**独立**的"脏"，永远分开谈：
1. **训练泄漏（编码器轴，真泄漏）**: 集成补丁在 CLIP/SigLIP/EVA 编码器上训。**被测 VLM 的编码器若在训练集里 → 泄漏，作废。**
2. **近亲（家族轴，混淆项非泄漏）**: 从不训 LM，但被测 VLM 若与 source 同家族(Qwen 血统)，打中 = "在出发点附近测"，**不证明迁到真正不同的模型。**

| VLM | 编码器 / LM | 相对 source | 用的 patch | 干净度 | 报告角色 |
|---|---|---|---|---|---|
| InternVL2-2B | InternViT / InternLM2 | 全外部 | **div5** | ✅ 最干净 | **头条：干净跨架构** |
| SmolVLM | SigLIP / SmolLM2 | LM 外部、编码器 SigLIP | **ensemble3 (no-SigLIP)** | 跨 LM | action + 解离现象 |
| Qwen2-VL-2B | Qwen-VL ViT / Qwen2 | **同族**(LM 同源 Qwen) | **ens4** | ⚠️ 最不干净 | 同族近上界**参照**(非跨族) |
| LLaVA-OV | SigLIP / Qwen2 | 两轴都沾 | **ensemble3 (no-SigLIP)** | ❌ 污染 | 附录 / 脚注 |

> "同源/同族"专指 Qwen2-VL-2B 的 ViT 血统 + LM 家族都和 source(Qwen2.5-VL) 一家；**不是**和 patch 训练用的 CLIP/SigLIP 同源。

### 消融：是"集成"还是别的在起作用（已坐实）
同管线只改训练编码器数：**编码器零样本探针太"易"**(单个≈集成都~1.0，分不出)。决定性测试在 **InternVL2**：单 CLIP-B/32 **失败**(sign .10)、3×CLIP 集成**成功**(.70)、单 SigLIP 也**成功**(.625/.675)。→ **跨到陌生 VLM 需要"编码器多样性(集成)"或"表征对齐的 surrogate(SigLIP↔InternViT)"；单个不匹配编码器即便用 for-transfer 管线也失败。**

### 报告铁律（reviewer 会盯）
1. **InternVL2-2B 是头条**（两轴外部、编码器不在训练集）。
2. **Qwen2-VL-2B 永远标"同族上界参照(非跨族)"**。
3. **SmolVLM 不能用 conj 报**：sign=.00 → conj 归零；但 action ≫ random = 真成功（决策被劫持、不经感知 = **解离**）。头条用 **action + 解离**。
4. **主表每行用对该目标最干净的 patch**，**且表注说明为什么不同模型用不同 patch**（SmolVLM/LLaVA 编码器是 SigLIP，**必须 no-SigLIP 补丁**；div5 含 SigLIP 只能给 InternVL2(InternViT)）。
5. 每个数配 clean + random(5-seed) 地板。
> 数值真源 = `cross_vlm_table.{md,csv}`（`make_cross_vlm_table.py` 纯 CPU 收集、不重下模型）。**本文件只管分类和规则，不存具体数字**（数字看 CSV / HANDOFF）。

---

## 攻击边界 / 决策墙（本 session 核心负结果 —— 论文卖点之一）

迁移天花板 = **InternVL2 上 div5 的 sign∧action 合取 .675**（感知 sign 顶到 1.0，但 action 封顶 ~.6–.68）。**五种突破尝试全部失败**，每种各揭示决策层一个鲁棒维度：

| 尝试 | 做法 | 结果 | 为什么败 |
|---|---|--:|---|
| 查询·risk 门 | SPSA 优化"必须停吗→Yes"的 logprob（div5 热启动） | 不翻 | 守护推理对图像扰动鲁棒 |
| 查询·action 门 | SPSA 优化"动作→STOP"的 logprob | 合取 .675→**.30** | 刷代理：靠毁 sign(1.0→.375) 抬 STOP-logit |
| 查询·联合 min(sign,action) | SPSA 优化两者的 min（堵刷分） | =.675 无增益 | div5 已是该目标**局部最优** |
| 概念·决策文本 | 训练目标换抽象"emergency/danger ahead" | 合取 **.05** | 抽象概念无视觉原型 → CLIP 特异、**不 transfer** |
| 概念·危险物 | 训练目标换具体"行人/障碍/红灯" | action **.025** | 感知成功(模型说"watch for pedestrian")但 → **分级谨慎非急停** |

**机制结论（论文主线）**：感知极易跨族迁移（能幻觉出 STOP 标志、甚至行人）；但**决策鲁棒**——模型把感知映射成**分级谨慎(watch/slow)**，**只有"STOP 标志"这条最直接规则可靠产出"STOP"**。所以 div5(stop 标志) 既是最强攻击(.675)又是概念上对的那个；**.675 是带机制论证的稳健上限，不是没调好的数**。已写进 `TRANSFER_REPORT_ensemble.md` §9。
- 查询脚本：`attack_query_logprob.py`(`--opt-q/--pos-words/--neg-words` 切 risk/action 门)、`attack_query_joint.py`(联合 min)。两者从 div5 热启动、**只在其上叠加有界低维 nudge**——**别在低分辨率重参数化整个 patch，会毁掉对抗结构**（踩过：16×16 重参数化把 div5 打成 0）。

---

## Patch 变体 & 物理 EOT

- **div5** = 含 SigLIP 的 5 模型集成 → `outputs_ensemble_div5/ensemble_div5.png`。对 InternVL2 最强；**会污染** SmolVLM/LLaVA。
- **ensemble3 (no-SigLIP)** → `outputs_ensemble_patch_noSigLIP/ensemble_noSigLIP.png`。给 SmolVLM/LLaVA 用（保编码器轴干净）。
- **ens4** = 4 模型集成 → Qwen2-VL-2B 用。
- **物理鲁棒** = `outputs_ensemble_phys/ensemble_phys.png`（96px，`--phys-eot` 训）。
  `--phys-eot` = 随机仿射/视角 + 缩放重采样 + 镜头模糊 + gamma + 光照噪声，**全可微**，模拟"打印→拍摄→模型"链。**物理鲁棒牺牲数字潜力（数字 sign .975 但 action 仅 .325）—— 正常**；真考验是打印拍回来测。

### 打印/拍摄协议
- 打印成**正方形 15–18cm**居中放 A4（别拉伸）、全彩 sRGB、哑光纸、实际尺寸不平滑。
- 拍摄：patch 占框 **1/4–1/3 居中**（兼顾"够大"与"够多场景"）、大致正面（±12° 内）、光照匀无眩光、每场景多张。
- 三套场景：白墙 / 室内 / 室外。
- 测：照片放 `physical_photos/<scene>/` → `eval_physical.py --photo-dir ...`。

---

## 关键脚本

- **白盒训练**: `train_qwen_perception_patch.py` `train_qwen_decision_patch.py` `train_qwen_heldout.py`
- **集成/物理训练**: `train_ensemble_patch.py`（`--train-models` `--phys-eot` `--extra-stop-texts`增补 `--stop-texts`替换 `--patch-size`）
- **编码器层迁移评测**: `eval_clip_transfer.py`（`--n-random` 多 seed；输出 stop-rate 矩阵）
- **VLM 迁移评测**: `eval_transfer.py`(AutoModelForVision2Seq) `eval_transfer_internvl.py`(InternVL2 走 model.chat) `eval_transfer_api.py`(Gemini/OpenAI)
- **归因评测**（区分名义/因果）: `eval_attribution.py` `eval_attr_full.py` `eval_decision.py`
- **查询黑盒攻击**: `attack_query_logprob.py` `attack_query_joint.py` `attack_query_blackbox.py`
- **嵌入位移分析**: `analyze_encoder_shift.py`
- **物理评测**: `eval_physical.py`｜**演示**: `demo_attack.py`(CLEAN vs PATCHED 逐图)｜**跨 VLM 表**: `make_cross_vlm_table.py`

**评测输出约定**: 每个迁移 `outputs_*/` 下 `transfer_summary_metrics.csv`（`category=ALL_UNIQUE`、`variant∈{clean,random_center,heldout_center,whitebox_center}`）+ `transfer_image_metrics.csv`（per-image `sign_success`/`action_success`，算 conj）。`whitebox_center`=集成攻击列，`heldout_center`=Qwen 单模型基线。**这些 CSV 是数值真源。**

## 可复现命令

```bash
# 集成 patch（hold-out；SmolVLM/LLaVA 评测必须用不含其编码器的版本）
python train_ensemble_patch.py \
  --train-models ViT-B-32-quickgelu:openai ViT-B-16-quickgelu:openai \
                 ViT-B-32:laion2b_s34b_b79k ViT-B-16-SigLIP:webli EVA02-B-16:merged2b_s8b_b131k \
  --holdout-models --patch-size 96 --epochs 15 --out-dir outputs_ensemble --save-name ensemble.png
# 物理鲁棒：上面 + --phys-eot --out-dir outputs_ensemble_phys --save-name ensemble_phys.png
# InternVL2 迁移评测
python eval_transfer_internvl.py --model-id OpenGVLab/InternVL2-2B --out-dir outputs_iv2 \
  --patch-path outputs_ensemble_div5/ensemble_div5.png --heldout-path outputs_qwen_whitebox/qwen_stop_patch.png \
  --prompt-ids sign_stop decision_safe_action decision_risk
# 跨 VLM 主表（CPU，不动 GPU、不重下模型） / 演示 / 物理
python make_cross_vlm_table.py            # → cross_vlm_table.{md,csv}
python demo_attack.py --n 4               # CLEAN→PATCHED before/after
python eval_physical.py --photo-dir physical_photos/wall
```

---

## 计算环境 & 踩坑（每次都遇到）

- GPU 服务器 `w6908`，走 SSH ControlMaster socket `~/.ssh/cm-w6908.sock`。**w6328 的 GPU 驱动已死，只用 w6908。**
- **master 老掉 + sshgate 限流**（频繁重试触发 fail2ban 式封禁、`Connection reset by 129.215.215.76`）。稳起法 + **别狂重试**（越试封越久，停 ~20min 冷却）：
  ```bash
  ssh -M -S ~/.ssh/cm-w6908.sock -o ControlPersist=12h -o ServerAliveInterval=60 -o ServerAliveCountMax=120 -fN w6908
  ```
- **连接不稳 → 远程任务一律"scp + push + detached(nohup) 一条命令"发起再轮询**；detached 任务写 NFS、连接断了也照跑、结果不丢。
- **bash 引号坑（坑过两次）**: `nohup bash -c "... echo === foo (96px) === ..."` 里**未转义的括号**会让 bash 当场语法错、任务**静默死掉**（pgrep 空）。echo/注释里的 `()` 一律去掉或转义。
- **不要重下被删的 VLM**: SmolVLM/LLaVA/Qwen2-VL-2B 共 ~41G（本 session 为腾配额删了，释放 47G）。要数据从已有 `outputs_*/` CSV 收集（CPU、不抢卡）。InternVL2 + open_clip 编码器缓存**保留着**。InternVL2 需 `einops`+`sentencepiece`（已装）。
- **GPU 约定**: 重训练放 GPU1；CSV 汇总/出表/demo 放 CPU。

## 诚实边界（写论文/答辩守住）

固定居中 · 干净数字域（**无 JPEG/物理拍照**，物理版在做） · 单模型 Qwen2.5-VL-3B 白盒 · 贪心解码 · 真攻击率是**保守下限** · 跨 prompt 鲁棒性部分依赖措辞。
**已知边界（坐实）**: 决策层对图像攻击鲁棒（.675 带机制天花板，**六种突破全败**：加了 typographic——嵌 STOP 文字对齐 CLIP 文本、InternViT 不 OCR-迁移，sign .20/act .075）——这是结论不是缺陷。
**物理可用（受控配对，2026-07-19）**: `eval_physical.py --paired-dir` 同机位 clean/mine/senior + McNemar。合并 n=30：**感知大幅领先 sign .90 vs 学长 .60,"让车停"打平 .233,clean action=0 证因果**;决策墙物理域复现。**扔掉合取只有坏处**（学长有"没看见却停"样本被合取剔除、只看 action 会追平）；**严格口径 + 合取是该守的**。早前"未控" .545 vs .143 **虚高已弃用**（表观尺寸没配平）。
**★表观尺寸 vs 场景（2026-07-20 修正）**: 裁剪探针+clean对照+上下文隔离(probe3)。**能确定**:①尺寸是杠杆但**非单调,峰≈1/3**(z100 .267 / z70 .600 / z55 反降 .400);②**远处/上方的路无关**(probe3 遮上半 action 仍 .600)——早前"上下文重要"多是 size 确认偏差。**驾驶场景=控制 action 的关键独立因素(已支持,不对称检验 scene_iso)**:感知持平(sign .933=.933)、indoor patch 反而更大(占尺寸便宜)下,indoor action .200 vs outdoor .667(差3×)→ 场景压过尺寸优势。机制="整个场景像不像可驾驶户外语境"(非某条具体路,probe3 远处路可遮)。措辞守"场景类型作为整体(户外包vs室内包)",光照/背景是室内外不可分差别、真车永在完整户外包→部署相关正确单位,不必单剥。决策墙因此是**有条件的**:action 只在"场景可驾驶∧看见STOP"升级硬停。**改口两句**:z55 非纯切割(图上 patch 大多完整)、"越大越好"作废(甜点≈1/3别撑满)。数字探针不上报,预示 ~1/3 重拍 .5–.6,不破墙(~.68)。**瓶颈=决策墙(确定);感知/patch/迁移/尺寸都不是。下一步=重拍outdoor@1/3 + 驾驶场景隔离对照(路边vs纯墙,同尺寸) + div5。**
