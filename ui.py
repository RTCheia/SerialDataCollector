"""Qt 预览与操作界面；所有状态读取均由 UI 定时拉取。"""
import json
import logging
import multiprocessing as mp
from pathlib import Path
import sys
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg
import serial
import serial.tools.list_ports

from acquisition import (acquisition_main, make_shared, STATE, RECEIVED, FRAMES, ANOMALIES,
                            BACKLOG, DISCARDED, TAIL, FLUSHED, RATE, RUNNING, COMPLETE, FAILED, QUEUE_CHUNKS)
from plot_metadata import PLOT_COLORS, PLOT_GROUPS
from protocol import load_rules


class MainWindow(QtWidgets.QMainWindow):
    """三个通道独立记录，只绘制当前选中的通道。"""
    def __init__(self, base):
        super().__init__()
        self.base = Path(base)
        self.ctx = mp.get_context("spawn")
        self.jobs = []
        self.channels = []
        self.save_dir = self.base / "data"
        self.session = None
        self.stopping = False
        self.close_pending = False
        self.last_preview = None
        self.next_scale = 0
        self.export_process = None
        self.dashboards = []
        self.analyses = []
        self.running_announced = False
        self.setWindowTitle("多串口数据采集 V7.1 · 921600 · 双协议自适应")
        self.resize(1460, 920)
        self.setMinimumSize(1080, 740)
        self.build_ui()
        self.refresh_ports()
        self.status_timer = QtCore.QTimer(self)
        self.status_timer.timeout.connect(self.poll_status)
        self.status_timer.start(250)
        self.plot_timer = QtCore.QTimer(self)
        self.plot_timer.timeout.connect(self.update_plot)
        self.set_fps()
        QtGui.QShortcut(QtGui.QKeySequence("F11"), self, activated=self.toggle_fullscreen)
        QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, activated=self.showNormal)

    def build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 16)
        title = QtWidgets.QLabel("多串口数据采集  V7.1")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QtWidgets.QLabel("三路独立采集 · 完整 TXT 记录 · 预览不等待、不积压"))
        grid = QtWidgets.QGridLayout()
        for column, label in enumerate(["启用", "输入串口", "波特率（8N1）", "记录状态"]):
            grid.addWidget(QtWidgets.QLabel(label), 0, column)
        for index in range(3):
            enabled = QtWidgets.QCheckBox(f"CH{index + 1}")
            enabled.setChecked(True)
            port = QtWidgets.QComboBox()
            port.setMinimumWidth(220)
            baud = QtWidgets.QSpinBox()
            baud.setRange(1200, 4000000)
            baud.setValue(921600)
            status = QtWidgets.QLabel("待命")
            status.setMinimumWidth(450)
            self.channels.append(dict(enabled=enabled, port=port, baud=baud, status=status))
            for column, widget in enumerate([enabled, port, baud, status]):
                grid.addWidget(widget, index + 1, column)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)
        controls = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("刷新串口")
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.folder_button = QtWidgets.QPushButton("保存目录")
        self.folder_button.clicked.connect(self.choose_folder)
        self.start_button = QtWidgets.QPushButton("开始记录")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_collection)
        self.stop_button = QtWidgets.QPushButton("停止并保存")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_collection)
        self.export_button = QtWidgets.QPushButton("导出本次完整波形 PNG")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_plots)
        self.dashboard_button = QtWidgets.QPushButton("进入数据看板")
        self.dashboard_button.setToolTip("随时进入；打开前选择包含 chN/data.txt 的已记录会话文件夹")
        self.dashboard_button.clicked.connect(lambda: self.open_dashboard())
        self.analysis_button = QtWidgets.QPushButton("数据分析与处理")
        self.analysis_button.setToolTip("选择会话并导入光捕 CSV，直接给出 UWB 精度结论")
        self.analysis_button.clicked.connect(lambda: self.open_analysis())
        for button in [self.refresh_button, self.folder_button, self.start_button, self.stop_button,
                       self.export_button, self.dashboard_button, self.analysis_button]:
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        key_bar = QtWidgets.QHBoxLayout()
        key_bar.addWidget(QtWidgets.QLabel("临时串口按键："))
        self.key_channel = QtWidgets.QComboBox()
        self.key_channel.addItems(["CH1", "CH2", "CH3"])
        self.key_channel.setToolTip("选择发送按键指令的目标通道串口（需与连接板卡的 CH 一致）")
        key_bar.addWidget(self.key_channel)
        self.btn_cmd_clear = QtWidgets.QPushButton("1 · 清除")
        self.btn_cmd_clear.setToolTip("向选中串口发送字符 '1'（模拟按下清除键）")
        self.btn_cmd_clear.clicked.connect(lambda: self.send_uart_key(b"1", "1 · 清除"))
        self.btn_cmd_record = QtWidgets.QPushButton("2 · 记录")
        self.btn_cmd_record.setToolTip("向选中串口发送字符 '2'（模拟按下记录键）")
        self.btn_cmd_record.clicked.connect(lambda: self.send_uart_key(b"2", "2 · 记录"))
        self.btn_cmd_read = QtWidgets.QPushButton("3 · 停止/输出")
        self.btn_cmd_read.setToolTip("向选中串口发送字符 '3'（模拟按下停止/输出键；回放期间板卡忽略）")
        self.btn_cmd_read.clicked.connect(lambda: self.send_uart_key(b"3", "3 · 停止/输出"))
        for button in [self.btn_cmd_clear, self.btn_cmd_record, self.btn_cmd_read]:
            key_bar.addWidget(button)
        key_bar.addStretch()
        layout.addLayout(key_bar)
        self.path_label = QtWidgets.QLabel(str(self.save_dir))
        self.path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        self.notice = QtWidgets.QLabel("就绪。默认 230440；请与设备设置一致。计数连续性不等同于校验和验证。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        plot_controls = QtWidgets.QHBoxLayout()
        self.preview_channel = QtWidgets.QComboBox()
        self.preview_channel.addItems(["预览 CH1", "预览 CH2", "预览 CH3"])
        self.preview_channel.currentIndexChanged.connect(self.invalidate_preview)
        self.fps = QtWidgets.QComboBox()
        self.fps.addItems(["关闭预览", "1 FPS", "5 FPS", "10 FPS"])
        self.fps.setCurrentIndex(2)
        self.fps.currentIndexChanged.connect(self.set_fps)
        self.window = QtWidgets.QSpinBox()
        self.window.setRange(50, 2000)
        self.window.setSingleStep(50)
        self.window.setValue(500)
        self.window.setSuffix(" 点")
        self.window.valueChanged.connect(self.invalidate_preview)
        self.x_mode = QtWidgets.QComboBox()
        self.x_mode.addItems(["显示帧号", "显示时间 (s)"])
        self.x_mode.setCurrentIndex(1)   # 默认横轴为时间（秒），帧率由协议自动识别
        self.x_mode.currentIndexChanged.connect(self.on_x_mode_changed)
        self.sample_rate = QtWidgets.QSpinBox()
        self.sample_rate.setRange(1, 5000)
        self.sample_rate.setValue(1000)
        self.sample_rate.setSuffix(" Hz")
        self.sample_rate.setToolTip("老格式(EB 90)帧的手动采样率；EB 91 自动 1000 Hz、EB 92 自动 50 Hz，无需手调")
        self.sample_rate.valueChanged.connect(self.invalidate_preview)
        self.auto_y = QtWidgets.QCheckBox("自动 Y 范围（每秒一次）")
        self.auto_y.setChecked(True)
        self.auto_y.toggled.connect(self.invalidate_preview)
        reset = QtWidgets.QPushButton("适配范围")
        reset.clicked.connect(self.fit_ranges)
        for widget in [self.preview_channel, self.fps, QtWidgets.QLabel("显示窗口"), self.window,
                       QtWidgets.QLabel("X轴:"), self.x_mode, QtWidgets.QLabel("帧率:"), self.sample_rate,
                       self.auto_y, reset]:
            plot_controls.addWidget(widget)
        plot_controls.addStretch()
        self.render_label = QtWidgets.QLabel("预览尚无数据")
        plot_controls.addWidget(self.render_label)
        layout.addLayout(plot_controls)
        # 默认高效二维路径：细线、关闭抗锯齿，不依赖显卡驱动稳定性。
        pg.setConfigOptions(antialias=False, useOpenGL=False, background="w", foreground="#334155")
        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics, 1)
        self.plots, self.curves = [], []
        for spec in PLOT_GROUPS:
            plot = self.graphics.addPlot(row=spec["row"], col=spec["column"], rowspan=spec["rowspan"], title=spec["title"])
            plot.setLabel("left", spec["unit"])
            plot.setLabel("bottom", "时间 (s)")
            plot.showGrid(x=True, y=True, alpha=0.15)
            plot.addLegend(offset=(8, 8))
            plot.disableAutoRange()
            plot.setYRange(-1, 1)
            self.plots.append(plot)
            for name, color in zip(spec["names"], PLOT_COLORS):
                curve = plot.plot(pen=pg.mkPen(color, width=1), name=name)
                curve.setClipToView(True)
                curve.setDownsampling(auto=True, method="peak")
                self.curves.append(curve)

    def refresh_ports(self):
        ports = list(serial.tools.list_ports.comports())
        for index, channel in enumerate(self.channels):
            combo = channel["port"]
            old = combo.currentData()
            combo.clear()
            combo.addItem("请选择串口", "")
            for port in ports:
                combo.addItem(f"{port.device} · {port.description}", port.device)
            combo.addItem("仿真数据（200 Hz 正弦波）", "SIM")
            combo.addItem("仿真回放 d.txt（200 Hz 真实板卡回放）", "SIM_D")
            found = combo.findData(old) if old else -1
            combo.setCurrentIndex(found if found >= 0 else (index + 1 if index < len(ports) else 0))

    def choose_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "选择记录目录", str(self.save_dir))
        if folder:
            self.save_dir = Path(folder)
            self.path_label.setText(folder)

    def set_fps(self, *_):
        if not hasattr(self, "plot_timer"):
            return
        fps = [0, 1, 5, 10][self.fps.currentIndex()]
        self.plot_timer.stop()
        if fps:
            self.plot_timer.start(round(1000 / fps))
        else:
            self.render_label.setText("预览已关闭，记录继续")
        self.invalidate_preview()

    def invalidate_preview(self, *_):
        self.last_preview = None
        self.next_scale = 0
        # 切换到没有采集的通道时清空旧曲线，避免误认其他通道的数据。
        if hasattr(self, "curves") and not any(j["index"] == self.preview_channel.currentIndex() for j in self.jobs):
            for curve in self.curves:
                curve.setData([], [])

    def on_x_mode_changed(self, *_):
        is_time = (self.x_mode.currentIndex() == 1)
        label = "时间 (s)" if is_time else "采样序号"
        if hasattr(self, "plots"):
            for plot in self.plots:
                plot.setLabel("bottom", label)
        self.invalidate_preview()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def start_collection(self):
        if any(job["process"].is_alive() for job in self.jobs):
            return
        selected = [(index, c) for index, c in enumerate(self.channels) if c["enabled"].isChecked()]
        ports = [c["port"].currentData() for _, c in selected]
        physical = [p for p in ports if p not in ("SIM", "SIM_D")]
        if not selected or any(not p for p in ports) or len(set(physical)) != len(physical):
            QtWidgets.QMessageBox.warning(self, "串口设置", "请选择至少一路输入；启用通道必须选择串口，物理串口不能重复。")
            return
        try:
            rules = load_rules(self.base / "fr.txt")
            self.session = self.save_dir / (time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1000000000:09d}")
            self.session.mkdir(parents=True, exist_ok=False)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "无法开始", str(exc))
            return
        self.jobs = []
        self.stopping = False
        self.running_announced = False
        self.invalidate_preview()
        for curve in self.curves:
            curve.setData([], [])
        for channel in self.channels:
            channel["status"].setText("未启用")
        try:
            for index, channel in selected:
                status, preview, event = make_shared(self.ctx)
                cmd_queue = self.ctx.Queue()
                folder = self.session / f"ch{index + 1}"
                config = dict(port=channel["port"].currentData(), baud=channel["baud"].value(), rules=rules, folder=str(folder))
                process = self.ctx.Process(target=acquisition_main, args=(config, status, preview, event, None, cmd_queue), name=f"capture-ch{index + 1}")
                process.start()
                self.jobs.append(dict(index=index, process=process, status=status, preview=preview, stop=event, cmd_queue=cmd_queue, folder=folder))
        except Exception as exc:
            for job in self.jobs:
                job["stop"].set()
            self.stopping = True
            logging.exception("启动失败")
            self.notice.setText(f"启动失败，正在收尾已启动通道：{exc}")
            if not self.jobs:
                self.set_active(False)
                self.notice.setText(f"未能启动采集进程：{exc}。请检查 log/app_v7_0.log。")
                return
        self.preview_channel.setCurrentIndex(selected[0][0])
        self.set_active(True)
        self.path_label.setText(str(self.session))
        if not self.stopping:
            self.notice.setText("正在启动采集进程；计数异常、写盘失败会明确显示。关闭预览不会停止记录。")
        logging.info("采集开始 session=%s", self.session)

    def set_active(self, active):
        for channel in self.channels:
            for key in ["enabled", "port", "baud"]:
                channel[key].setEnabled(not active)
        self.refresh_button.setEnabled(not active)
        self.folder_button.setEnabled(not active)
        self.start_button.setEnabled(not active)
        self.stop_button.setEnabled(active and not self.stopping)
        self.export_button.setEnabled(not active and bool(self.jobs) and self.export_process is None)
        self.dashboard_button.setEnabled(True)

    def stop_collection(self):
        self.stopping = True
        for job in self.jobs:
            job["stop"].set()
        self.stop_button.setEnabled(False)
        self.notice.setText("正在停止接收并排空记录缓冲，文件同步完成后才会确认保存。")

    def send_uart_key(self, payload, label):
        ch_index = self.key_channel.currentIndex()
        channel = self.channels[ch_index]
        port = channel["port"].currentData()
        baud = channel["baud"].value()
        if not port or port in ("SIM", "SIM_D"):
            QtWidgets.QMessageBox.warning(self, "串口按键", f"CH{ch_index + 1} 当前未选择物理串口。")
            return
        job = next((j for j in self.jobs if j["index"] == ch_index and j["process"].is_alive()), None)
        if job is not None:
            job["cmd_queue"].put(payload)
            self.notice.setText(f"已通过运行中通道 CH{ch_index + 1} ({port}) 发送 {label} ({payload.decode('ascii')})")
            return
        try:
            with serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.1, write_timeout=0.5) as ser:
                ser.write(payload)
                ser.flush()
            self.notice.setText(f"已向 CH{ch_index + 1} ({port}, {baud}) 发送 {label} ({payload.decode('ascii')})")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "发送失败", f"向 {port} 发送 {label} 失败：\n{exc}")

    def poll_status(self):
        any_alive, failed, warnings = False, False, False
        for job in self.jobs:
            process, status = job["process"], job["status"]
            alive = process.is_alive()
            any_alive |= alive
            state = int(status[STATE])
            abnormal = state == FAILED or (not alive and (process.exitcode != 0 or state != COMPLETE))
            failed |= abnormal
            warning = bool(status[ANOMALIES] or status[DISCARDED])
            warnings |= warning
            name = "失败，查看 summary/log" if abnormal else ("已保存" if not alive else ("正在收尾" if self.stopping else ("记录中" if state == RUNNING else "正在启动")))
            label = self.channels[job["index"]]["status"]
            rate_now = int(status[RATE])
            proto = f"{rate_now} Hz" if rate_now else "手动帧率"
            label.setText(f"{name} | {int(status[FRAMES]):,} 帧 | {proto} | 计数异常 {int(status[ANOMALIES])} | 缓冲 {int(status[BACKLOG])}/{QUEUE_CHUNKS}")
            label.setStyleSheet("color: #b91c1c" if abnormal else ("color: #b45309" if warning else "color: #15803d"))
            label.setToolTip(f"接收 {int(status[RECEIVED])} 字节；已 flush {int(status[FLUSHED])} 帧；同步丢弃 {int(status[DISCARDED])} 字节；末尾不足帧 {int(status[TAIL])} 字节")
        if failed and any_alive and not self.stopping:
            # 一路失败即请求全部停止，避免将不完整的三路实验误当作正常采集。
            self.stop_collection()
            self.notice.setText("检测到通道失败，正在停止全部通道并保存已有数据；请查看各通道摘要与日志。")
        if self.jobs and not self.running_announced and not self.stopping and all(int(j["status"][STATE]) == RUNNING for j in self.jobs):
            self.running_announced = True
            self.notice.setText("采集已启动，正在记录完整 TXT。可随时降低或关闭预览；完成后点击“停止并保存”。")
        if self.jobs and not any_alive and not self.start_button.isEnabled():
            for job in self.jobs:
                job["process"].join(timeout=0)
            self.set_active(False)
            if failed:
                self.notice.setText("采集失败/中断，已保存的数据仍保留。请查看各通道 summary.json 和 log/acquisition.log。")
            elif warnings:
                self.notice.setText("文件已关闭并保存，但检测到计数或帧同步异常，请检查 summary.json。")
            else:
                tails = sum(int(job["status"][TAIL]) for job in self.jobs)
                self.notice.setText(f"TXT 已关闭并保存；本次未检测到计数跳变。停止边界不足一帧的字节共 {tails} 字节，已计入摘要。")
            logging.info("采集结束 failed=%s warnings=%s session=%s", failed, warnings, self.session)
        if self.export_process and not self.export_process.is_alive():
            self.export_process.join(timeout=0)
            self.export_process = None
            self.export_button.setEnabled(not any_alive and bool(self.jobs))
            if not any_alive:
                self.notice.setText("波形导出结束，结果见采集目录 export_result.json。")
        if self.close_pending and not any_alive and self.export_process is None:
            self.close_pending = False
            self.close()

    def update_plot(self):
        if self.isMinimized():
            return
        job = next((j for j in self.jobs if j["index"] == self.preview_channel.currentIndex()), None)
        if not job:
            return
        preview = job["preview"]
        lock = preview.get_lock()
        if not lock.acquire(False):
            return
        start = time.perf_counter()
        try:
            version, count = int(preview[0]), int(preview[1])
            is_time = (self.x_mode.currentIndex() == 1)
            # 协议识别出的采样率优先（IMU 板 1000 Hz / UWB 板 50 Hz），老格式帧用手动值兜底
            rate = int(job["status"][RATE]) or max(1, self.sample_rate.value())
            key = (job["index"], version, self.window.value(), is_time, rate)
            if not count or key == self.last_preview:
                return
            points = min(count, self.window.value())
            offset = 2 + (count - points) * 13
            data = np.array(preview[offset:2 + count * 13]).reshape(points, 13)
        finally:
            lock.release()

        if is_time:
            x_data = data[:, 0] / rate
            min_x, max_x = x_data[0], max(x_data[0] + (1.0 / rate), x_data[-1])
        else:
            x_data = data[:, 0]
            min_x, max_x = x_data[0], max(x_data[0] + 1, x_data[-1])

        for index, curve in enumerate(self.curves):
            curve.setData(x_data, data[:, index + 1])
        for plot in self.plots:
            plot.setXRange(min_x, max_x, padding=0)
        if self.auto_y.isChecked() and time.perf_counter() >= self.next_scale:
            self.fit_ranges()
            self.next_scale = time.perf_counter() + 1
        self.last_preview = key
        self.render_label.setText(f"{points} 点/曲线 · 更新 {(time.perf_counter() - start) * 1000:.1f} ms")

    def fit_ranges(self, *_):
        for plot in self.plots:
            plot.enableAutoRange(axis="y", enable=True)
            plot.getViewBox().updateAutoRange()
            plot.disableAutoRange()

    def open_dashboard(self, folder=None):
        """随时选择已记录会话进入看板；自动测试可直接传入目录跳过低弹窗。"""
        if folder is None:
            default = self.session if self.session is not None else self.save_dir
            selected = QtWidgets.QFileDialog.getExistingDirectory(
                self, "选择数据看板会话文件夹（包含 ch1/ch2/ch3）", str(default)
            )
            if not selected:
                return
            folder = selected
        folder = Path(folder)
        # 用户直接选中 chN 文件夹时，自动回到其会话根目录。
        if folder.name[:2].lower() == "ch" and folder.name[2:].isdigit() and (folder / "data.txt").is_file():
            folder = folder.parent
        if self.session is not None and any(job["process"].is_alive() for job in self.jobs) \
                and folder.resolve() == self.session.resolve():
            QtWidgets.QMessageBox.information(
                self, "正在记录",
                "该会话仍在记录中，看板只能读取当前已经落盘的部分，内容可能不完整。",
            )
        from dashboard import DashboardWindow
        try:
            dashboard = DashboardWindow(folder)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "无法打开数据看板",
                f"{exc}\n\n请选择包含 ch1、ch2 等通道子目录的已记录会话文件夹。",
            )
            return
        dashboard.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dashboard.destroyed.connect(lambda: self.dashboards.remove(dashboard) if dashboard in self.dashboards else None)
        self.dashboards.append(dashboard)
        dashboard.show()
        self.notice.setText(f"数据看板已打开，分析会话：{folder}；原始记录只读。")

    def open_analysis(self, folder=None):
        """打开数据分析与处理窗口；当前有会话时自动带入。"""
        from analysis_window import AnalysisWindow
        target = Path(folder) if folder else (self.session if self.session is not None else None)
        try:
            window = AnalysisWindow(self.base, target)
        except Exception as exc:                       # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "无法打开数据分析与处理", str(exc))
            return
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        window.destroyed.connect(lambda: self.analyses.remove(window) if window in self.analyses else None)
        self.analyses.append(window)
        window.show()
        self.notice.setText("数据分析与处理已打开；选择会话、导入光捕 CSV，点击开始处理即可出结论。")

    def export_plots(self):
        if not self.session or self.export_process:
            return
        from export import export_session
        self.export_process = self.ctx.Process(target=export_session, args=(str(self.session),))
        self.export_process.start()
        self.export_button.setEnabled(False)
        self.notice.setText("正在独立进程导出本次完整波形，可继续操作界面。")

    def closeEvent(self, event):
        if any(job["process"].is_alive() for job in self.jobs) or self.export_process:
            self.close_pending = True
            self.stop_collection()
            event.ignore()
        else:
            event.accept()


def run_gui(base, smoke_test=False):
    """启动及自动界面自检；自检只用仿真源，不打开物理串口。"""
    log_dir = Path(base) / "log"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(filename=log_dir / "app_v7_0.log", level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    logging.info("界面启动 smoke_test=%s", smoke_test)
    app = QtWidgets.QApplication([])
    def report_exception(kind, value, tb):
        """窗口版 EXE 无控制台，未处理的界面异常必须写入日志并显示。"""
        logging.error("界面异常", exc_info=(kind, value, tb))
        QtWidgets.QMessageBox.critical(None, "界面异常", f"{value}\n详情见 log/app_v7_0.log；采集进程仍独立运行。")
    sys.excepthook = report_exception
    app.setFont(QtGui.QFont("Microsoft YaHei", 10))
    app.setStyleSheet("QMainWindow {background:#f1f5f9;} QLabel#title {font-size:25px;font-weight:700;color:#0f172a;} QPushButton {padding:9px 14px;border:1px solid #cbd5e1;border-radius:6px;background:white;} QPushButton#primary {background:#0284c7;color:white;} QPushButton:disabled,QPushButton#primary:disabled {color:#94a3b8;background:#e2e8f0;} QComboBox,QSpinBox {padding:6px;background:white;border:1px solid #cbd5e1;border-radius:4px;}")
    window = MainWindow(base)
    window.show()
    if smoke_test:
        folder = Path(base) / "_temp" / "data" / "ui_smoke"
        folder.mkdir(parents=True, exist_ok=True)
        window.save_dir = folder
        dashboard_image = folder / "v7_dashboard.png"
        dashboard_always_enabled = window.dashboard_button.isEnabled()
        smoke_state = dict(applied=False, independent=False)
        for channel in window.channels:
            channel["port"].setCurrentIndex(channel["port"].findData("SIM"))
        def exercise_dashboard():
            if not window.dashboards:
                return
            dashboard = window.dashboards[-1]
            dashboard.filter_toggle_button.click()
            tab = dashboard.filter_tabs[0]
            tab.method_combo.setCurrentIndex(3)
            tab.average_window.setCurrentText("5")
            tab.apply_button.click()
        def finish_dashboard():
            if not window.dashboards:
                return
            dashboard = window.dashboards[-1]
            smoke_state["applied"] = dashboard.filtered_groups[0] is not None
            for index, plot in enumerate(dashboard.plots):
                plot.setXRange(index * 10, index * 10 + 5, padding=0)
            ranges = [tuple(plot.getViewBox().viewRange()[0]) for plot in dashboard.plots]
            smoke_state["independent"] = len({tuple(round(v, 6) for v in r) for r in ranges}) == 4
            dashboard.fit_all()
            dashboard.graphics.grab().save(str(dashboard_image))
            dashboard.close()
        QtCore.QTimer.singleShot(200, window.start_collection)
        QtCore.QTimer.singleShot(1500, lambda: window.fps.setCurrentIndex(1))
        QtCore.QTimer.singleShot(2500, lambda: window.fps.setCurrentIndex(0))
        QtCore.QTimer.singleShot(3500, lambda: window.fps.setCurrentIndex(3))
        QtCore.QTimer.singleShot(4000, lambda: window.preview_channel.setCurrentIndex(2))
        QtCore.QTimer.singleShot(4200, lambda: (window.window.setValue(2000), window.x_mode.setCurrentIndex(1)))
        QtCore.QTimer.singleShot(4500, lambda: window.fps.setCurrentIndex(2))
        QtCore.QTimer.singleShot(5000, lambda: window.grab().save(str(folder / "v7_preview.png")))
        QtCore.QTimer.singleShot(5500, window.stop_collection)
        QtCore.QTimer.singleShot(6300, lambda: window.open_dashboard(window.session))
        QtCore.QTimer.singleShot(6500, window.export_plots)
        QtCore.QTimer.singleShot(6900, exercise_dashboard)
        QtCore.QTimer.singleShot(7700, finish_dashboard)
        QtCore.QTimer.singleShot(8000, window.close)
    exit_code = app.exec()
    if smoke_test:
        # 读取真正落盘结果，冻结 EXE 的自检也能自动判定是否通过。
        summaries = [json.loads((j["folder"] / "summary.json").read_text(encoding="utf-8")) for j in window.jobs]
        exported = json.loads((window.session / "export_result.json").read_text(encoding="utf-8"))
        passed = len(summaries) == 3 and all(s["state"] == "complete" and s["counter_anomalies"] == 0 and s["written_frames"] >= 500 for s in summaries)
        passed = passed and len(exported["results"]) == 3 and all(r["state"] == "success" for r in exported["results"])
        passed = passed and dashboard_image.exists() and dashboard_image.stat().st_size > 0 and dashboard_always_enabled
        passed = passed and smoke_state["applied"] and smoke_state["independent"]
        report = dict(state="passed" if passed else "failed", session=str(window.session), frames=[s["written_frames"] for s in summaries], checks=["三路仿真", "1/关闭/10/5 FPS 切换", "CH3 预览", "停止后 TXT 完整收尾", "独立进程 PNG 导出", "看板入口无需已有会话即可用", "指定独立看板会话、逐图移动平均、四图独立视窗和当前图片保存", "关闭窗口等待后台任务结束"])
        (folder / "ui_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if passed else 1
    return exit_code
