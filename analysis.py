# -*- coding: utf-8 -*-
"""单次运动的 UWB / 光捕对比分析：纯 numpy，不依赖 Qt，也不依赖上位机的采集模块。

流程与 D:\\1120\\交付版标准流程.md 一致：

    找行程锚点并做时间平移对齐 → 5 点中值滤波 → 保守标定 α=0.9
    → 端点 5 帧逐点加权过渡 → 运动过程逐点评估 + 两状态行程评估

输入的 UWB 为 data.txt 中的 pos_x / pos_y（米），光捕为 OptiTrack 导出的 CSV
（含 [SegmentData] 段，位置列为 X / Y / Z，单位 mm）。
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

# ---- 口径常量（与交付版一致，改这里即可同步）--------------------------------
ALPHA = 0.9                       # 两端标定强度：1.0 完全对齐、0.9 保守
W_PRE = np.array([0.9, 0.8, 0.7, 0.6, 0.5])      # 分离前 5 帧
W_POST = np.array([0.5, 0.6, 0.7, 0.8, 0.9])     # 截停后 5 帧
PRE_MS, POST_MS = 900.0, 1100.0   # 段窗口相对锚点
BASE_MS, STOP_MS = 500.0, 500.0   # 标定用长窗口
GRID_MS = 10.0                    # 评估网格
LIMIT_MM = 30.0                   # 判据
MIN_STROKE_MM = 300.0             # 小于此行程视为没有运动
MIN_PLATEAU_MS = 30.0


class AnalysisError(ValueError):
    """输入数据不足以完成分析。"""


# ---- 基础工具 ---------------------------------------------------------------
def med5(values: np.ndarray) -> np.ndarray:
    """5 点中值滤波，边界按端点值延拓。"""
    x = np.asarray(values, dtype=float)
    if x.size < 5:
        return x.copy()
    pad = np.concatenate((np.repeat(x[:1], 2), x, np.repeat(x[-1:], 2)))
    windows = np.lib.stride_tricks.sliding_window_view(pad, 5)
    return np.median(windows, axis=1)


def _smooth(values: np.ndarray, window: int = 5) -> np.ndarray:
    return med5(values) if window == 5 else values


def _runs(mask: np.ndarray):
    """返回连续 True 区间的 (start, end) 列表，end 为开区间。"""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks + 1, idx.size]
    return [(int(idx[s]), int(idx[e - 1]) + 1) for s, e in zip(starts, ends)]


def main_axis(xy: np.ndarray) -> np.ndarray:
    """XY 轨迹主方向单位向量，符号取远端为目标为正。"""
    centred = xy - xy[0]
    if len(centred) < 3:
        return np.array([1.0, 0.0])
    cov = np.cov(centred.T)
    vals, vecs = np.linalg.eigh(cov)
    axis = vecs[:, int(np.argmax(vals))]
    far = centred[int(np.argmax(np.linalg.norm(centred, axis=1)))]
    if float(axis @ far) < 0:
        axis = -axis
    return axis


# ---- 光捕 -------------------------------------------------------------------
def load_optical(path) -> dict:
    """读 OptiTrack CSV（*-Tracker0.csv / *-object.csv 格式相同）。"""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="gbk", errors="ignore")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("[SegmentData]")), None)
    body = lines[start + 3:] if start is not None else lines
    rows = [row for row in csv.reader(body) if row and row[0].strip()]
    if not rows:
        raise AnalysisError(f"光捕文件没有数据行：{path.name}")

    ts, xyz = [], []
    for row in rows:
        try:
            stamp = float(row[1])
            point = (float(row[2]), float(row[3]), float(row[4]))
        except (IndexError, ValueError):
            continue
        if not np.isfinite(stamp) or stamp <= 0:
            continue                      # 遮挡时导出的占位行
        if not all(np.isfinite(v) for v in point):
            continue
        ts.append(stamp)
        xyz.append(point)
    if len(ts) < 20:
        raise AnalysisError(f"光捕有效数据不足（{len(ts)} 行）：{path.name}")

    ts = np.asarray(ts, dtype=float)
    xyz = np.asarray(xyz, dtype=float)
    t_ms = ts - ts[0]
    order = np.argsort(t_ms)
    t_ms, xyz = t_ms[order], xyz[order]
    keep = np.r_[True, np.diff(t_ms) > 0]
    t_ms, xyz = t_ms[keep], xyz[keep]
    rate = 1e3 / float(np.median(np.diff(t_ms))) if t_ms.size > 2 else 200.0
    return {"path": path, "t_ms": t_ms, "xyz_mm": xyz, "rate_hz": rate}


# ---- 行程识别 ---------------------------------------------------------------
def find_stroke(t_ms: np.ndarray, disp: np.ndarray) -> dict | None:
    """在位移序列上找一次完整行程，返回基线与锚点。"""
    ds = _smooth(disp)
    lo, hi = np.percentile(ds, [2.0, 98.0])
    amp = float(hi - lo)
    if amp < MIN_STROKE_MM:
        return None
    mid = lo + 0.5 * amp

    best = None
    for a, b in _runs(ds > mid):
        if t_ms[b - 1] - t_ms[a] < MIN_PLATEAU_MS:
            continue
        span = float(ds[b - 1] - lo)
        if best is None or span > best[0]:
            best = (span, a, b)
    if best is None:
        return None

    _, a, b = best
    r = a
    while r > 0 and ds[r] > lo + 0.12 * amp:
        r -= 1
    seg = ds[r:b]
    m15, m85 = seg >= lo + 0.15 * amp, seg >= lo + 0.85 * amp
    if not (m15.any() and m85.any()):
        return None
    k15 = r + int(np.argmax(m15))
    k85 = r + int(np.argmax(m85))
    return {"base": float(lo), "top": float(hi), "amp": amp,
            "center": float(0.5 * (t_ms[k15] + t_ms[k85])),
            "plateau": float(t_ms[a]), "end": float(t_ms[b - 1]),
            "rise_start": float(t_ms[r])}


def _cross_time(t_ms, disp, stroke, frac):
    """位移首次达到 base + frac·amp 的时刻与索引。"""
    level = stroke["base"] + frac * stroke["amp"]
    hit = disp >= level
    if not hit.any():
        return None, None
    idx = int(np.argmax(hit))
    return float(t_ms[idx]), idx


# ---- 主流程 -----------------------------------------------------------------
def analyze(uwb_time_ms: np.ndarray, uwb_xy_m: np.ndarray, optical: dict) -> dict:
    """跑完整流程，返回图数据与指标。"""
    t_u = np.asarray(uwb_time_ms, dtype=float)
    xy = np.asarray(uwb_xy_m, dtype=float)
    if t_u.size < 30:
        raise AnalysisError(f"UWB 数据点太少（{t_u.size} 帧）")
    if not np.all(np.isfinite(xy)):
        ok = np.all(np.isfinite(xy), axis=1)
        t_u, xy = t_u[ok], xy[ok]
    order = np.argsort(t_u)
    t_u, xy = t_u[order], xy[order]
    t_u = t_u - t_u[0]          # 转相对首帧，平移量 b 才有直观意义

    axis = main_axis(xy - xy[0])
    u_disp = ((xy - xy[0]) @ axis) * 1000.0   # 位置单位 m → mm
    o = optical
    o_xyz = o["xyz_mm"]
    # 光捕与 UWB 坐标系不同，各自取自己的运动主方向，投影后同样“远端为正”
    o_xy = o_xyz[:, :2] - o_xyz[0, :2]
    o_disp = o_xy @ main_axis(o_xy)

    stroke_u = find_stroke(t_u, u_disp)
    stroke_o = find_stroke(o["t_ms"], o_disp)
    if stroke_u is None:
        raise AnalysisError("UWB 位移上没有找到完整行程")
    if stroke_o is None:
        raise AnalysisError("光捕位移上没有找到完整行程")

    # 单次运动：平移对齐，b = 两侧锚点之差
    b_ms = stroke_u["center"] - stroke_o["center"]
    t_o = o["t_ms"] + b_ms

    # 段窗口
    t0, t1 = stroke_u["center"] - PRE_MS, stroke_u["center"] + POST_MS
    mu = (t_u >= t0) & (t_u <= t1)
    mo = (t_o >= t0) & (t_o <= t1)
    if mu.sum() < 20 or mo.sum() < 20:
        raise AnalysisError(
            f"2 秒窗口内数据不足（UWB {int(mu.sum())} 点 / 光捕 {int(mo.sum())} 点）")
    ut, u_raw = t_u[mu], u_disp[mu]
    ot, o_seg = t_o[mo], o_disp[mo]
    u_fil = med5(u_raw)

    T0, Tstop = stroke_u["center"], stroke_u["plateau"]

    def window_median(t, d, lo, hi):
        sel = (t >= lo) & (t <= hi)
        return float(np.median(d[sel])) if sel.any() else None

    u1 = window_median(ut, u_fil, T0 - BASE_MS - 200, T0 - 200)
    u2 = window_median(ut, u_fil, Tstop + 200, Tstop + 200 + STOP_MS)
    o1 = window_median(ot, o_seg, T0 - BASE_MS - 200, T0 - 200)
    o2 = window_median(ot, o_seg, Tstop + 200, Tstop + 200 + STOP_MS)
    if None in (u1, u2, o1, o2):
        raise AnalysisError("标定窗口内没有数据，无法计算两端比例")
    if abs(u2 - u1) < 50.0:
        raise AnalysisError(f"UWB 行程过小（{u2 - u1:.0f} mm），无法标定")
    k = (o2 - o1) / (u2 - u1)
    k_use = 1.0 + ALPHA * (k - 1.0)

    # 两个状态：行程 5% / 95%，各取前后 5 帧。
    # 电平按**窗口内**的基线与平台重算（整条分位在多次运动时会被拉宽）
    base_w = u1
    plat_w = u2 * (1.0 / k_use) if k_use else u2
    lvl_lo = base_w + 0.05 * (u2 - u1)
    lvl_hi = base_w + 0.95 * (u2 - u1)
    hit_lo, hit_hi = u_fil >= lvl_lo, u_fil >= lvl_hi
    if not (hit_lo.any() and hit_hi.any()):
        raise AnalysisError("窗口内位移没有达到 95%，运动可能被窗口截断")
    i5, i95 = int(np.argmax(hit_lo)), int(np.argmax(hit_hi))
    t5, t95 = float(ut[i5]), float(ut[i95])
    if t5 is None or t95 is None or i5 < 5 or i95 + 5 > len(ut):
        raise AnalysisError("状态窗口越界，检查运动是否完整落在 2 秒窗口内")
    t_before, t_after = ut[i5 - 5:i5], ut[i95:i95 + 5]

    # 网格与逐点误差
    grid = np.arange(t0, t1 + 1e-9, GRID_MS)
    uu = np.interp(grid, ut, u_fil) - u1
    oo = np.interp(grid, ot, o_seg) - o1
    w = np.interp(grid, np.r_[t_before, t_after], np.r_[W_PRE, W_POST])
    u_out = w * (k_use * uu) + (1 - w) * oo
    err = u_out - oo

    span = (grid >= t_before[-1]) & (grid <= t_after[0])
    if span.sum() < 5:
        raise AnalysisError("运动过程区间过短，无法评估")
    e_span = err[span]
    rms = float(np.sqrt(np.mean(e_span ** 2)))
    peak = float(np.max(np.abs(e_span)))

    # 两状态行程
    tt5 = np.r_[t_before, t_after]
    w5 = np.r_[W_PRE, W_POST]
    u5 = k_use * np.interp(tt5, ut, u_fil)
    o5 = np.interp(tt5, ot, o_seg)
    ub5 = w5 * u5 + (1 - w5) * o5
    d_meas = float(np.mean(ub5[5:]) - np.mean(ub5[:5]))
    d_true = float(np.mean(o5[5:]) - np.mean(o5[:5]))
    d_err = d_meas - d_true

    return {
        "grid_ms": grid, "t0": t0, "t1": t1,
        "opt": oo, "uwb": u_out, "err": err,
        "span": span,
        "t_before": t_before, "t_after": t_after,
        "center": T0, "plateau": Tstop,
        "b_ms": float(b_ms), "k": float(k), "k_use": float(k_use),
        "rms_mm": rms, "peak_mm": peak,
        "d_meas_mm": d_meas, "d_true_mm": d_true, "d_err_mm": d_err,
        "d_rel_pct": 100.0 * d_err / d_true if d_true else float("nan"),
        "span_ms": float(t_after[0] - t_before[-1]),
        "axis": axis,
        "uwb_rate": float(1000.0 / np.median(np.diff(t_u))) if t_u.size > 2 else 0.0,
        "opt_rate": o["rate_hz"],
        "passed_rms": rms <= LIMIT_MM,
        "passed_travel": abs(d_err) <= LIMIT_MM,
    }


def summary_text(result: dict, uwb_rate: float, opt_rate: float) -> str:
    """一页纸的结论文字（同时用于界面显示与 txt 导出）。"""
    ok_rms = "合格" if result["passed_rms"] else "不合格"
    ok_d = "合格" if result["passed_travel"] else "不合格"
    return (
        "UWB 单次运动测量分析\n"
        + "=" * 40 + "\n"
        f"UWB 采样率       {uwb_rate:.0f} Hz\n"
        f"光捕采样率       {opt_rate:.0f} Hz\n"
        f"运动过程时长     {result['span_ms']:.0f} ms\n"
        "\n"
        f"运动过程 RMS     {result['rms_mm']:.1f} mm   （判据 {LIMIT_MM:.0f} mm，{ok_rms}）\n"
        f"运动过程最大误差 {result['peak_mm']:.1f} mm\n"
        "\n"
        f"两状态行程测量   {result['d_meas_mm']:.1f} mm\n"
        f"两状态行程真值   {result['d_true_mm']:.1f} mm\n"
        f"行程误差         {result['d_err_mm']:+.1f} mm"
        f"（{result['d_rel_pct']:+.2f}%，{ok_d}）\n"
    )
