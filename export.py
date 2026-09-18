"""采集结束后的独立波形导出进程，避免争用 UI 与采集进程。"""
import json
import logging
from pathlib import Path
import time


def export_session(session):
    """流式读取完整 TXT，按峰值包络限制绘图内存，原始记录不降采样。"""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import numpy as np
    from protocol import PLOT_COLUMNS
    folder = Path(session)
    log_dir = folder / "log"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(filename=log_dir / "export.log", level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    started = time.time()
    results = []
    for source in sorted(folder.glob("ch*/data.txt")):
        try:
            with source.open(encoding="utf-8") as stream:
                count = sum(1 for line in stream if line.strip())
            if not count:
                continue
            bucket = max(1, (count + 9999) // 10000)
            xs, mins, maxs, block = [], [], [], []
            with source.open(encoding="utf-8") as stream:
                for index, line in enumerate(stream):
                    row = line.split()
                    block.append([float(row[c]) for c in PLOT_COLUMNS])
                    if len(block) >= bucket or index == count - 1:
                        values = np.asarray(block)
                        xs.append(index - (len(block) - 1) / 2)
                        mins.append(values.min(axis=0))
                        maxs.append(values.max(axis=0))
                        block.clear()
            low, high = np.asarray(mins), np.asarray(maxs)
            matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            fig = Figure(figsize=(18, 8), dpi=150, constrained_layout=True)
            FigureCanvasAgg(fig)
            axes = fig.subplots(2, 2).flatten()
            titles = ["IMU 角速度 (deg/s)", "IMU 线加速度 (m/s²)", "大量程加速度 (m/s²)", "UWB 位置 (m)"]
            names = [("wx", "wy", "wz"), ("ax", "ay", "az"), ("X", "Y", "Z"), ("pos_x", "pos_y", "pos_z")]
            for group, ax in enumerate(axes):
                for axis, color in enumerate(["#dc2626", "#16a34a", "#2563eb"]):
                    column = group * 3 + axis
                    if bucket == 1:
                        ax.plot(xs, low[:, column], color=color, linewidth=0.6, label=names[group][axis])
                    else:
                        ax.fill_between(xs, low[:, column], high[:, column], color=color, alpha=0.35, label=names[group][axis])
                ax.set_title(titles[group])
                ax.set_xlabel("采样序号")
                ax.legend()
                ax.grid(alpha=0.2)
            fig.suptitle(f"{source.parent.name} · 完整记录 {count:,} 帧 · 峰值包络分组 {bucket} 帧")
            output = source.with_name("waveform.png")
            fig.savefig(output)
            results.append(dict(file=str(output), state="success", frames=count, bucket=bucket))
        except Exception as exc:
            logging.exception("导出失败 %s", source)
            results.append(dict(file=str(source), state="failed", error=str(exc)))
    report = dict(duration_seconds=round(time.time() - started, 3), results=results)
    (folder / "export_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("阶段结果 %s", json.dumps(report, ensure_ascii=False))
