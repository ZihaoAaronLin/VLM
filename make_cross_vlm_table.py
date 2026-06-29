"""Collate the per-VLM transfer runs into ONE comparable cross-model table.

Reads existing eval outputs (no GPU, no model download). Each VLM row uses the
cleanest valid ensemble patch for that target (see `patch` / `note`); the
clean / random / single-model-Qwen baselines come from a run that contains them.
Outputs cross_vlm_table.{md,csv}.
"""
import csv
import math
from pathlib import Path

# (label, ens_dir [whitebox_center = ensemble attack], base_dir [clean/random/
#  heldout_center = Qwen single-model baseline], patch_label, note)
ROWS = [
    ("InternVL2-2B", "outputs_internvl2_div5", "outputs_transfer_internvl2_noSigLIP",
     "div5", "CLEAN cross-arch (InternViT + InternLM2 both foreign)"),
    ("Qwen2-VL-2B", "outputs_transfer_ens_qwen2_2b", "outputs_transfer_ens_qwen2_2b",
     "ens4", "SAME-FAMILY as source (Qwen LM) -- not cross-family"),
    ("SmolVLM", "outputs_transfer_smolvlm_noSigLIP", "outputs_transfer_smolvlm_noSigLIP",
     "ensemble3 (no-SigLIP)", "clean cross-LM; sign/action DISSOCIATION"),
    ("LLaVA-OV", "outputs_transfer_llavaov_noSigLIP", "outputs_transfer_llavaov_noSigLIP",
     "ensemble3 (no-SigLIP)", "LM = Qwen (confounded) -> appendix"),
]


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0, c - h), min(1, c + h)


def tf(x):
    return str(x).strip().lower() in ("true", "1", "1.0")


def asr(d, variant):
    f = Path(d) / "transfer_summary_metrics.csv"
    if not f.exists():
        return None
    for r in csv.DictReader(open(f)):
        if r["category"] == "ALL_UNIQUE" and r["variant"] == variant:
            return (float(r["sign_level_asr"]), float(r["action_level_asr"]), float(r["risk_level_asr"]))
    return None


def conj(d, variant):
    f = Path(d) / "transfer_image_metrics.csv"
    if not f.exists():
        return 0, 0
    rows = [r for r in csv.DictReader(open(f)) if r["variant"] == variant]
    return sum(1 for r in rows if tf(r["sign_success"]) and tf(r["action_success"])), len(rows)


def a1(t):
    return f"{t[1]:.3f}" if t else "n/a"   # action component


md = [
    "# Cross-VLM transfer (one row per target, cleanest valid patch per target)",
    "",
    "`action` = STOP-decision ASR. `ens` cells = ensemble-attack sign/action/risk. "
    "`conj` = per-image sign^action (Wilson 95% CI). clean/random/base are floors.",
    "",
    "| VLM | patch | clean(act) | random(act) | Qwen-single(act) | **ens** sign/act/risk | **conj** [CI] | note |",
    "|---|---|--:|--:|--:|--:|--:|---|",
]
rows_csv = []
for label, ens_dir, base_dir, plabel, note in ROWS:
    cl, rd, hd = asr(base_dir, "clean"), asr(base_dir, "random_center"), asr(base_dir, "heldout_center")
    wb = asr(ens_dir, "whitebox_center")
    c, n = conj(ens_dir, "whitebox_center")
    lo, hi = wilson(c, n)
    ens = f"{wb[0]:.2f}/{wb[1]:.2f}/{wb[2]:.2f}" if wb else "n/a"
    md.append(f"| {label} | {plabel} | {a1(cl)} | {a1(rd)} | {a1(hd)} | {ens} | "
              f"{(c/n if n else 0):.3f} [{lo:.2f},{hi:.2f}] | {note} |")
    rows_csv.append({
        "vlm": label, "patch": plabel,
        "clean_action": cl[1] if cl else "", "random_action": rd[1] if rd else "",
        "qwen_single_action": hd[1] if hd else "",
        "ens_sign": wb[0] if wb else "", "ens_action": wb[1] if wb else "", "ens_risk": wb[2] if wb else "",
        "ens_conj": round(c / n, 3) if n else "", "n": n, "note": note,
    })

Path("cross_vlm_table.md").write_text("\n".join(md) + "\n", encoding="utf-8")
with open("cross_vlm_table.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows_csv[0].keys()))
    w.writeheader()
    [w.writerow(r) for r in rows_csv]

print("\n".join(md))
print("\nwrote cross_vlm_table.md / cross_vlm_table.csv")
