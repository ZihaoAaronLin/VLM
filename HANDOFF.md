# HANDOFF — VLM 项目（截至本 session 末）

## 一句话状态
白盒 100% → 集成跨架构迁移（InternVL2 .675 = 干净头条）→ **决策墙**（.675 带机制上限，**六种突破全败**，加了 typographic）→ **受控配对物理实测完成**。同机位 clean/mine/senior + McNemar：**感知大幅领先**（合并 n=30 sign **.90 vs 学长 .60**），**"让车停"打平**（.233），**clean=0 证因果**；决策墙物理域复现。**★新发现**：裁剪探针 + clean 对照证明 **patch 占框 ~1/3 且留住驾驶场景 → 停车 .267→.60（纯 patch 驱动）**——表观尺寸×场景是可回收物理杠杆，预示重拍能到 .5–.6。可分享总览 artifact:https://claude.ai/code/artifact/6ce202c7-7b49-4178-9d74-3d78b12ad2de

> 注:早前"未控"那批(ours conj .545 vs 学长 .143)**虚高、已弃用**（表观尺寸没配平）；以下**受控配对**为准。

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

### 受控配对物理（`eval_physical.py --paired-dir ...`，InternVL2，同机位 clean/mine/senior + McNemar）

`eval_physical.py` 新增 **`--paired-dir` 模式**：逐机位决策表 + **strict/liberal 双 action 口径** + conj + McNemar 精确检验。mine = `ensemble_phys`。真源 `phys_paired_{outdoor,indoor}.log`。

| 组 | outdoor sign/act严/conj严 | indoor sign/act严/conj严 |
|---|---|---|
| clean | .200 / **.000** / .000 | .000 / **.000** / .000 |
| **mine** | **.867 / .267 / .267** | **.933 / .200 / .200** |
| senior | .600 / .267 / .200 | .600 / .200 / .200 |

- **合并 n=30**：mine **sign .90** vs senior .60；**action .233 打平**；conj .233 vs .20；**clean action=0 → 因果成立**。
- **outdoor 翻转**：clean→mine +4/−0 p=.0625；**mine vs senior action 打平 +3/−3 p=1.0，只在 sign 赢**。
- **扔掉合取只有坏处**：学长 `outdoor_7`=没看见标志却停,合取剔除、只看 action 给他算上→追平;宽松 senior .400 反超。**严格 + 合取 是我们该守的。**
- **indoor 是感知的好测试、动作的坏测试**：放大 patch → sign .867→**.933**,但 action **反降 .200**——indoor 非驾驶场景（clean 宽松 .333 "do not drive"）。**动作看 outdoor,感知看 indoor。**

### ★表观尺寸 × 场景（裁剪探针 + clean 对照，本轮关键发现）
把 outdoor mine 图往 patch 裁紧（放大占比、留住路），clean 同样裁作对照：

| 放大档 | clean act | mine act | mine conj |
|---|--:|--:|--:|
| z100(patch ~1/5) | .000 | .267 | .267 |
| **z70(~1/3)** | **.067** | **.600** | **.533** |
| z55(更大/更紧) | .000 | .400 | .400 |
| z70+遮上半路(probe3) | .000 | .600 | .600 |

- **能确定:尺寸是杠杆,但非单调,峰≈1/3**。clean 各档 ~0、mine z100→z70 冲 .600（+.533，McNemar p≈.01）；但 **z55 更大反降 .400 → "越大越好"错,甜点≈1/3别撑满**。
- **能确定:远处/上方的路无关**。probe3(固定 z70、遮上半路)action 仍 .600 = 未遮 → 早前"上下文重要"多是 **size 确认偏差**。
- **已支持:驾驶场景是控制 action 的关键、独立因素(不对称检验 scene_iso,2026-07-20)**。用户手裁 outdoor/indoor mine 图喂模型:两组 **sign 都 .933(感知持平)**、**indoor patch 反而更大(占尺寸便宜)**,但 **indoor action .200 vs outdoor .667(差 3×)**。→ 感知持平+indoor 尺寸占优,换非驾驶场景仍打到 1/3 → **场景压过尺寸优势 = 保守而有力的证据**。结合 probe3(远处路可遮),机制 = "整个场景像不像可驾驶户外语境"(非某条具体路)。
- **措辞层级(挡"你怎么知道不是光照"追问)**:主张"**场景类型作为整体**(户外包 vs 室内包)控制 action";光照/背景是室内外**根本不可分**的差别,真车永远在完整户外包里 → 这是**部署相关的正确比较单位**,不需/无法单剥路语义。
- **改口**:z55 非纯切割(图上 patch 大多完整)。数字探针不上报,预示 ~1/3 重拍 .5–.6,不破墙(~.68)。
- **瓶颈(确定)= 决策墙,且是有条件的**:action 只在"场景可驾驶 ∧ 看见 STOP"时升级到硬停。感知/patch/迁移/尺寸都已确定不是瓶颈。物理彻底钉死(可选)= 同 patch 同尺寸路边 vs 纯墙,但不对称检验已很有说服力。

**攻击边界（.675 天花板，六种突破全败）**：

| 尝试 | 结果 | 机制 |
|---|--:|---|
| 查询 risk 门 | 不翻 | 守护推理硬 |
| 查询 action 门 | 合取 .30 | 刷代理、毁 sign |
| 查询 joint min | .675 | div5 局部最优 |
| 决策文本(抽象) | .05 | 概念不 transfer |
| 危险物(行人) | action .025 | 感知成功但→谨慎非急停 |
| **typographic(嵌STOP字)** | **sign .20/act .075** | **CLIP 文本≠InternViT,不 OCR-迁移** |

**优化尝试(都没打赢原始)**：v2(NPS+TI+增强EOT,`train_ensemble_patch.py --nps-weight --ti`)过度正则化 sign→.175；typographic 塌;**白盒 InternVL2 用户否决=off-thesis(弃泛用性)**。原始 `ensemble_phys` 数字 sign .975/act .325 仍冠军。

## 下一步（payoff）
1. **重拍 outdoor**：patch 占框 **~1/3**（非 1/5、非 1/2）+ **画面留住路面/车** + 正面 + 同机位 clean/mine/senior。预测物理 action **~.5–.6**、conj ~.5 —— 能上报、不改 claim 的高数。
2. **div5 对照**：数字底子 .68(vs phys .325),同样拍大;打印件已备 `~/Downloads/patches_to_print/{div5,phys}_PRINT_1920px.png`（最近邻放大保硬边）。
3. 照片流程(已跑通)：本地 HEIC/jpg → `sips`/`ImageOps.exif_transpose` 烘正方向 + 降采样 1600 → **打 tar 传一次**再远端解 → `eval_physical.py --paired-dir`（先 `nvidia-smi` 找空卡,4 张卡别撞别人的）。
4.（可选）论文初稿:白盒→迁移→决策墙(六印证)→物理可用 + 表观尺寸×场景。

## 锁定决策（别重吵，细节见 CLAUDE.md 报告铁律）
- 头条 = **InternVL2 .675**（干净跨架构）；**Qwen2-VL-2B .800 = 同族上界参照，不当跨族成功报**。
- **SmolVLM 用 action(.80)+解离报，不用合取**（sign=.00 会把合取归零、误导）。
- LLaVA-OV 放附录、注明两轴污染。
- 主表每行用对该目标最干净的 patch，**表注解释**为什么不同模型用不同 patch。
- **.675 是带机制天花板**；查询/概念攻击五种全败 → 攻击线收口（这是结论，不是待办）。
- 物理鲁棒 patch 数字 action .325 偏低**是正常**（拿潜力换鲁棒）；**考卷已交：物理 conj .545 > 数字 .325,phys-EOT 成立**。
- **物理对照报法**：我们的 vs 学长的 = 同任务·同评测的**方法对比**（不是地板对照）；sign 优势已边缘显著,conj 优势待补 n。别把 .545 当没有不确定性——n=11、CI [.28,.79]。

## 环境/连接（每次都遇到，详见 CLAUDE.md「踩坑」）
- `ssh -M -S ~/.ssh/cm-w6908.sock -o ControlPersist=12h -o ServerAliveInterval=60 -o ServerAliveCountMax=120 -fN w6908`
- master 老掉 + sshgate 限流 → **别狂重试**（停 ~20min 冷却）；远程任务"scp+push+detached 一条命令"发起再轮询；nohup 内 echo 的括号必须去掉/转义；别重下被删的 ~41G VLM。
- 进程（本 session）：phys 训练 PID 4288 → `qphys.log`（已完成；首次 PID 4034 因括号语法错静默挂掉）。
