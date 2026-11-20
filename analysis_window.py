# -*- coding: utf-8 -*-
"""数据分析与处理：单次运动 UWB / 光捕对比，一键出图与结论。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

import analysis
from session_data import SessionDataError, discover_channels, load_channel

pg.setConfigOptions(antialias=True, background="w", foreground="#334155")

COLOR_OPT = "#16a34a"
COLOR_UWB = "#2563eb"
COLOR_ERR = "#dc2626"
COLOR_BAND = (22, 163, 74, 30)


class AnalysisWindow(QtWidgets.QMainWindow):
    """绑定一个已记录会话 + 一个光捕文件，处理一次运动并给出结论。"""

    def __init__(self, base_dir: Path, session: Path | None = None):
        super().__init__()
        self.base_dir = Path(base_dir)
        self.settings = QtCore.QSettings("RTC", "SerialDataCollector")
        self.result = None
        self.session = Path(session) if session else self._remembered("analysis/session")
        self.optical_path = self._remembered("analysis/optical")
        self._uwb_channel = None

        self.setWindowTitle("数据分析与处理 V7.1")
        self.resize(1280, 900)
        self.setMinimumSize(1040, 720)
        self.build_ui()
        if self.session and self.session.is_dir():
            self.inspect_session()
            if self.optical_path:
                self.optical_edit.setText(str(self.optical_path))
        else:
            self.session = None
        self.refresh_ready()

    # ---- 设置读写 ---------------------------------------------------------
    def _remembered(self, key):
        value = self.settings.value(key, "")
        return Path(value) if value else None

    def _remember(self, key, path):
        self.settings.setValue(key, str(path))

    # ---- 界面 -------------------------------------------------------------
    def build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("数据分析与处理  V7.1")
        title.setObjectName("title")
        layout.addWidget(title)
        hint = QtWidgets.QLabel("选择已结束的采集会话，导入同一次试验的光捕 CSV，点击处理后直接给出结论。")
        hint.setStyleSheet("color:#64748b;")
        layout.addWidget(hint)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        self.session_edit = QtWidgets.QLineEdit()
        self.session_edit.setReadOnly(True)
        self.session_edit.setPlaceholderText("尚未选择采集会话")
        self.session_button = QtWidgets.QPushButton("选择会话…")
        self.session_button.clicked.connect(self.choose_session)
        self.optical_edit = QtWidgets.QLineEdit()
        self.optical_edit.setReadOnly(True)
        self.optical_edit.setPlaceholderText("尚未导入光捕数据")
        self.optical_button = QtWidgets.QPushButton("导入光捕…")
        self.optical_button.clicked.connect(self.choose_optical)
        grid.addWidget(QtWidgets.QLabel("采集会话"), 0, 0)
        grid.addWidget(self.session_edit, 0, 1, 1, 3)
        grid.addWidget(self.session_button, 0, 4)
        grid.addWidget(QtWidgets.QLabel("光捕数据"), 1, 0)
        grid.addWidget(self.optical_edit, 1, 1, 1, 3)
        grid.addWidget(self.optical_button, 1, 4)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        row = QtWidgets.QHBoxLayout()
        self.channel_label = QtWidgets.QLabel("—")
        self.channel_label.setStyleSheet("color:#475569;")
        row.addWidget(self.channel_label, 1)
        self.run_button = QtWidgets.QPushButton("开始处理")
        self.run_button.setObjectName("primary")
        self.run_button.setMinimumHeight(38)
        self.run_button.setMinimumWidth(140)
        self.run_button.clicked.connect(self.run_analysis)
        row.addWidget(self.run_button)
        layout.addLayout(row)

        self.verdict = QtWidgets.QLabel("等待处理")
        self.verdict.setWordWrap(True)
        self.verdict.setStyleSheet(
            "background:#e2e8f0;color:#334155;border-radius:6px;padding:10px 14px;font-size:15px;")
        layout.addWidget(self.verdict)

        cards = QtWidgets.QGridLayout()
        cards.setHorizontalSpacing(10)
        self.card_values = {}
        for index, (key, name) in enumerate([
                ("rms", "运动过程 RMS"), ("peak", "运动过程最大误差"),
                ("travel", "两状态行程误差"), ("rel", "行程相对误差")]):
            box = QtWidgets.QFrame()
            box.setStyleSheet("QFrame{background:white;border:1px solid #e2e8f0;border-radius:8px;}")
            inner = QtWidgets.QVBoxLayout(box)
            inner.setContentsMargins(12, 8, 12, 8)
            caption = QtWidgets.QLabel(name)
            caption.setStyleSheet("color:#64748b;font-size:12px;border:none;")
            value = QtWidgets.QLabel("—")
            value.setStyleSheet("font-size:21px;font-weight:700;color:#0f172a;border:none;")
            inner.addWidget(caption)
            inner.addWidget(value)
            self.card_values[key] = value
            cards.addWidget(box, 0, index)
        layout.addLayout(cards)

        self.plot_host = QtWidgets.QWidget()
        plot_layout = QtWidgets.QVBoxLayout(self.plot_host)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(6)
        self.plot_disp = pg.PlotWidget()
        self.plot_disp.setMinimumHeight(240)
        self.plot_disp.showGrid(x=True, y=True, alpha=0.25)
        self.plot_disp.setLabel("left", "运动方向位移", units="mm",
                                **{"font-family": "Microsoft YaHei", "font-size": "10pt"})
        self.plot_disp.addLegend(offset=(-12, 12), labelTextSize="10pt")
        self.plot_err = pg.PlotWidget()
        self.plot_err.setMinimumHeight(190)
        self.plot_err.showGrid(x=True, y=True, alpha=0.25)
        self.plot_err.setLabel("left", "位置误差", units="mm",
                               **{"font-family": "Microsoft YaHei", "font-size": "10pt"})
        self.plot_err.setLabel("bottom", "相对运动中心的时间", units="s",
                               **{"font-family": "Microsoft YaHei", "font-size": "10pt"})
        self.plot_err.addLegend(offset=(-12, 12), labelTextSize="10pt")
        self.plot_err.setXLink(self.plot_disp)
        plot_layout.addWidget(self.plot_disp)
        plot_layout.addWidget(self.plot_err)
        layout.addWidget(self.plot_host, 1)

        footer = QtWidgets.QHBoxLayout()
        self.export_png = QtWidgets.QPushButton("导出图表 PNG")
        self.export_png.clicked.connect(self.save_png)
        self.export_csv = QtWidgets.QPushButton("导出处理后 CSV")
        self.export_csv.clicked.connect(self.save_csv)
        self.open_dir = QtWidgets.QPushButton("打开输出目录")
        self.open_dir.clicked.connect(self.open_output)
        footer.addWidget(self.export_png)
        footer.addWidget(self.export_csv)
        footer.addWidget(self.open_dir)
        footer.addStretch()
        self.output_label = QtWidgets.QLabel("")
        self.output_label.setStyleSheet("color:#64748b;")
        footer.addWidget(self.output_label)
        layout.addLayout(footer)

    # ---- 选择文件 ---------------------------------------------------------
    def choose_session(self):
        start = self.session or self.base_dir / "data"
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择采集会话文件夹（包含 chN/data.txt）", str(start))
        if not selected:
            return
        folder = Path(selected)
        if folder.name.lower().startswith("ch") and (folder / "data.txt").is_file():
            folder = folder.parent
        self.session = folder
        self._remember("analysis/session", folder)
        self.inspect_session()
        self.refresh_ready()

    def choose_optical(self):
        start = Path(self.optical_path).parent if self.optical_path else self.base_dir
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "导入光捕数据（OptiTrack 导出 CSV）", str(start), "CSV 文件 (*.csv);;所有文件 (*)")
        if not path:
            return
        self.optical_path = Path(path)
        self._remember("analysis/optical", self.optical_path)
        self.optical_edit.setText(str(self.optical_path))
        self.refresh_ready()

    def inspect_session(self, quiet=False):
        """识别会话里的通道，标出哪个是 UWB。"""
        self.session_edit.setText(str(self.session))
        self._uwb_channel = None
        try:
            sources = discover_channels(self.session)
        except SessionDataError as exc:
            self.channel_label.setText(f"无法识别通道：{exc}")
            return
        parts = []
        for source in sources:
            header = (source.summary or {}).get("frame_header")
            rate = (source.summary or {}).get("rate_hz")
            kind = {"EB 92": "UWB", "EB 91": "IMU"}.get(header, "未知")
            parts.append(f"{source.name.upper()} = {kind}（{header or '?'}，{rate or '?'} Hz）")
            if header == "EB 92" and self._uwb_channel is None:
                self._uwb_channel = source.name
        self.channel_label.setText("　".join(parts) +
                                   ("" if self._uwb_channel else "　未找到 UWB 通道（EB 92）"))

    def refresh_ready(self):
        ready = bool(self.session and getattr(self, "_uwb_channel", None) and self.optical_path)
        self.run_button.setEnabled(ready)

    # ---- 处理 -------------------------------------------------------------
    def run_analysis(self):
        if not (self.session and self.optical_path):
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            channel = load_channel(self.session, self._uwb_channel)
            uwb_t = channel.device_time * 1000.0
            uwb_xy = channel.values[:, 9:11]          # pos_x / pos_y，单位 m
            optical = analysis.load_optical(self.optical_path)
            result = analysis.analyze(uwb_t, uwb_xy, optical)
        except (analysis.AnalysisError, SessionDataError) as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "无法完成分析", str(exc))
            return
        except Exception as exc:                       # noqa: BLE001
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "分析失败", f"{type(exc).__name__}: {exc}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()

        self.result = result
        self.session_name = self.session.name
        self.render(result)
        self.write_outputs(result)

    def render(self, r):
        self.card_values["rms"].setText(f"{r['rms_mm']:.1f} mm")
        self.card_values["peak"].setText(f"{r['peak_mm']:.0f} mm")
        self.card_values["travel"].setText(f"{r['d_err_mm']:+.1f} mm")
        self.card_values["rel"].setText(f"{r['d_rel_pct']:+.2f} %")

        ok_rms, ok_travel = r["passed_rms"], r["passed_travel"]
        if ok_rms and ok_travel:
            text, color = "结论：合格　运动过程 RMS 与两状态行程误差均在 30 mm 以内", ("#dcfce7", "#166534")
        elif ok_rms or ok_travel:
            which = "运动过程 RMS" if ok_rms else "两状态行程误差"
            text = f"结论：部分合格　{which}在 30 mm 以内，另一项超出"
            color = ("#fef3c7", "#92400e")
        else:
            text, color = "结论：不合格　运动过程 RMS 与两状态行程误差均超出 30 mm", ("#fee2e2", "#991b1b")
        self.verdict.setText(
            f"{text}　|　行程测量 {r['d_meas_mm']:.1f} mm ／ 真值 {r['d_true_mm']:.1f} mm")
        self.verdict.setStyleSheet(
            f"background:{color[0]};color:{color[1]};border-radius:6px;padding:10px 14px;font-size:15px;")

        x = (r["grid_ms"] - r["center"]) / 1000.0
        self.plot_disp.clear()
        self.plot_err.clear()

        band = pg.PlotDataItem(x, np.full_like(x, analysis.LIMIT_MM), pen=None)
        lower = pg.PlotDataItem(x, np.full_like(x, -analysis.LIMIT_MM), pen=None)
        self.plot_err.addItem(pg.FillBetweenItem(band, lower, brush=COLOR_BAND))

        base = float(min(r["opt"].min(), r["uwb"].min()))
        self.plot_disp.plot(x, r["opt"] - base, pen=pg.mkPen(COLOR_OPT, width=2.4), name="光学动捕真值")
        self.plot_disp.plot(x, r["uwb"] - base, pen=pg.mkPen(COLOR_UWB, width=1.8), name="UWB 测量位移")
        for t in (r["t_before"][0], r["t_before"][-1], r["t_after"][0], r["t_after"][-1]):
            self.plot_disp.addItem(pg.InfiniteLine(
                pos=(t - r["center"]) / 1000.0, angle=90,
                pen=pg.mkPen("#94a3b8", width=1, style=QtCore.Qt.DashLine)))

        self.plot_err.plot(x, r["err"], pen=pg.mkPen(COLOR_ERR, width=1.8),
                           name=f"误差　RMS {r['rms_mm']:.1f} mm")
        self.plot_err.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen("#334155", width=1)))
        for t in (r["t_before"][-1], r["t_after"][0]):
            self.plot_err.addItem(pg.InfiniteLine(
                pos=(t - r["center"]) / 1000.0, angle=90,
                pen=pg.mkPen("#94a3b8", width=1, style=QtCore.Qt.DashLine)))

    # ---- 输出 -------------------------------------------------------------
    def output_dir(self) -> Path:
        return self.session / "analysis"

    def write_outputs(self, r):
        folder = self.output_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            stamp = "分析结果.txt"
            text = analysis.summary_text(
                r, r["uwb_rate"], r["opt_rate"]) + f"\n来源会话  {self.session}\n光捕文件  {self.optical_path}\n"
            (folder / stamp).write_text(text, encoding="utf-8")
            self.output_label.setText(f"输出目录：{folder}")
        except OSError as exc:
            self.output_label.setText(f"写入输出目录失败：{exc}")

    def save_png(self):
        if self.result is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "保存分析图", str(self.output_dir() / "分析图.png"), "PNG 图片 (*.png)")
        if not path:
            return
        self.plot_host.grab().save(path)
        self.output_label.setText(f"已保存：{path}")

    def save_csv(self):
        if self.result is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "保存处理后数据", str(self.output_dir() / "分析数据.csv"), "CSV 文件 (*.csv)")
        if not path:
            return
        r = self.result
        data = np.column_stack([r["grid_ms"] - r["center"], r["opt"], r["uwb"], r["err"]])
        np.savetxt(path, data, delimiter=",", fmt="%.4f",
                   header="time_ms,optical_mm,uwb_mm,error_mm", comments="")
        self.output_label.setText(f"已保存：{path}")

    def open_output(self):
        folder = self.output_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))
