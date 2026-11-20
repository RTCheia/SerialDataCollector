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
    from plot_metadata import PLOT_COLORS, PLOT_COLUMNS, PLOT_GROUPS
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
            # 采样率取自采集摘要（IMU 板 1000 Hz / UWB 板 50 Hz 自动识别），老记录没有该字段时按 1000 Hz。
            rate = 1000
            summary_path = source.parent / "summary.json"
            if summary_path.exists():
                try:
                    rate = json.loads(summary_path.read_text(encoding="utf-8")).get("rate_hz") or 1000
                except Exception:
                    logging.warning("采样率读取失败，按 1000 Hz 处理 %s", summary_path)
            bucket = max(1, (count + 9999) // 10000)
            xs, mins, maxs, block = [], [], [], []
            with source.open(encoding="utf-8") as stream:
                for index, line in enumerate(stream):
                    row = line.split()
                    block.append([float(row[c]) for c in PLOT_COLUMNS])
                    if len(block) >= bucket or index == count - 1:
                        values = np.asarray(block)
                        xs.append((index - (len(block) - 1) / 2) / rate)
                        mins.append(values.min(axis=0))
                        maxs.append(values.max(axis=0))
                        block.clear()
            low, high = np.asarray(mins), np.asarray(maxs)
            matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            fig = Figure(figsize=(18, 8), dpi=150, constrained_layout=True)
            FigureCanvasAgg(fig)
            axes = fig.subplots(2, 2).flatten()
            for group, (spec, ax) in enumerate(zip(PLOT_GROUPS, axes)):
                for axis, color in enumerate(PLOT_COLORS):
                    column = group * 3 + axis
                    if bucket == 1:
                        ax.plot(xs, low[:, column], color=color, linewidth=0.6, label=spec["names"][axis])
                    else:
                        ax.fill_between(xs, low[:, column], high[:, column], color=color, alpha=0.35, label=spec["names"][axis])
                ax.set_title(f"{spec['title']} ({spec['unit']})")
                ax.set_xlabel("时间 (s)")
                ax.legend()
                ax.grid(alpha=0.2)
            fig.suptitle(f"{source.parent.name} · 完整记录 {count:,} 帧 · {rate} Hz · 峰值包络分组 {bucket} 帧")
            output = source.with_name("waveform.png")
            fig.savefig(output)
            results.append(dict(file=str(output), state="success", frames=count, bucket=bucket))
        except Exception as exc:
            logging.exception("导出失败 %s", source)
            results.append(dict(file=str(source), state="failed", error=str(exc)))
    report = dict(duration_seconds=round(time.time() - started, 3), results=results)
    (folder / "export_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("阶段结果 %s", json.dumps(report, ensure_ascii=False))
