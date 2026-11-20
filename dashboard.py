"""V7.1 独立离线数据分析看板。"""
from datetime import datetime
from functools import partial
import logging
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtWidgets
import pyqtgraph as pg

from plot_metadata import PLOT_COLORS, PLOT_GROUPS
from session_data import (apply_filter, discover_channels, export_processed_csv,
                          load_channel, nominal_times)


FILTER_METHODS = (
    ("四阶低通滤波", "lowpass"),
    ("一阶高通滤波", "highpass"),
    ("带通滤波（高通 + 低通）", "bandpass"),
    ("移动平均", "moving_average"),
)


class DashboardTask(QtCore.QThread):
    """在后台执行加载、滤波或导出；Qt 控件仍只由主线程操作。"""
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            result = self.function()
        except Exception as exc:
            logging.exception("数据看板后台任务失败")
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


class FilterTab(QtWidgets.QWidget):
    """单个图表的三轴滤波设置；由主窗口统一调度后台计算。"""
    apply_requested = QtCore.Signal(int, object)
    clear_requested = QtCore.Signal(int)

    def __init__(self, index, spec, parent=None):
        super().__init__(parent)
        self.index = index
        self.applied = None
        self.available = True
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(6)
        self.method_combo = QtWidgets.QComboBox()
        for label, method in FILTER_METHODS:
            self.method_combo.addItem(label, method)
        self.method_combo.setMinimumWidth(210)
        self.low_cut = self.make_number_combo(("160", "320", "640", "1280", "2560"), "160")
        self.high_cut = self.make_number_combo(("0.24", "0.49", "0.5", "0.97", "1.0", "1.9", "3.96"), "0.5")
        self.average_window = self.make_number_combo(("3", "5", "11", "21", "51"), "5")
        self.apply_button = QtWidgets.QPushButton("应用到此图")
        self.clear_button = QtWidgets.QPushButton("取消此图滤波")
        self.clear_button.setEnabled(False)
        for label, widget in [("方式", self.method_combo), ("低通", self.low_cut),
                              ("高通", self.high_cut), ("平均", self.average_window)]:
            controls.addWidget(QtWidgets.QLabel(label))
            controls.addWidget(widget)
        controls.addWidget(self.apply_button)
        controls.addWidget(self.clear_button)
        controls.addStretch()
        layout.addLayout(controls)

        hint = QtWidgets.QLabel(
            "四阶低通滤波（默认 160 Hz，抗混叠）；一阶高通滤波（去直流/温漂）；"
            "两者串联为带通滤波。仅作用于本图三轴。"
        )
        hint.setStyleSheet("color:#64748b;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status_label = QtWidgets.QLabel("原始数据（未滤波）")
        self.status_label.setStyleSheet("color:#475569;font-weight:600;")
        layout.addWidget(self.status_label)

        self.method_combo.currentIndexChanged.connect(self.sync_controls)
        self.method_combo.currentIndexChanged.connect(self.refresh_status)
        self.low_cut.currentTextChanged.connect(self.refresh_status)
        self.high_cut.currentTextChanged.connect(self.refresh_status)
        self.average_window.currentTextChanged.connect(self.refresh_status)
        self.apply_button.clicked.connect(self.request_apply)
        self.clear_button.clicked.connect(lambda: self.clear_requested.emit(self.index))
        self.sync_controls()

    @staticmethod
    def make_number_combo(values, default):
        combo = QtWidgets.QComboBox()
        combo.setEditable(True)
        combo.addItems(values)
        combo.setCurrentText(default)
        combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        combo.setMinimumWidth(76)
        combo.setToolTip("可从常用值选择，也可直接输入数值")
        return combo

    def sync_controls(self, *_):
        method = self.method_combo.currentData()
        self.low_cut.setEnabled(method in ("lowpass", "bandpass"))
        self.high_cut.setEnabled(method in ("highpass", "bandpass"))
        self.average_window.setEnabled(method == "moving_average")

    def settings(self):
        method = self.method_combo.currentData()
        try:
            if method == "lowpass":
                low_cut = float(self.low_cut.currentText())
                return {"method": method, "low_cut": low_cut}
            if method == "highpass":
                high_cut = float(self.high_cut.currentText())
                return {"method": method, "high_cut": high_cut}
            if method == "bandpass":
                low_cut = float(self.low_cut.currentText())
                high_cut = float(self.high_cut.currentText())
                if low_cut <= high_cut:
                    raise ValueError("带通滤波的低通截止频率必须高于高通截止频率")
                return {"method": method, "low_cut": low_cut, "high_cut": high_cut}
            window = int(self.average_window.currentText())
            if window < 1:
                raise ValueError("移动平均点数必须为正整数")
            return {"method": "moving_average", "window": window}
        except ValueError as exc:
            raise ValueError(f"{PLOT_GROUPS[self.index]['title']}：{exc}") from exc

    @staticmethod
    def describe(settings):
        if settings["method"] == "lowpass":
            return f"四阶低通 {settings['low_cut']:g} Hz"
        if settings["method"] == "highpass":
            return f"一阶高通 {settings['high_cut']:g} Hz"
        if settings["method"] == "bandpass":
            return f"带通 {settings['high_cut']:g}–{settings['low_cut']:g} Hz"
        return f"移动平均 {settings['window']} 点"

    def refresh_status(self, *_):
        if not self.available:
            self.status_label.setText("当前帧型无数据，本图不参与滤波")
            self.status_label.setStyleSheet("color:#b45309;font-weight:600;")
            self.clear_button.setEnabled(False)
            return
        self.clear_button.setEnabled(self.applied is not None)
        if self.applied is None:
            self.status_label.setText("原始数据（未滤波）")
            self.status_label.setStyleSheet("color:#475569;font-weight:600;")
            return
        try:
            dirty = self.settings() != self.applied
        except ValueError:
            dirty = True
        if dirty:
            self.status_label.setText(f"已应用 {self.describe(self.applied)}；参数已修改，需重新应用")
            self.status_label.setStyleSheet("color:#b45309;font-weight:600;")
        else:
            self.status_label.setText(f"已应用 {self.describe(self.applied)}")
            self.status_label.setStyleSheet("color:#15803d;font-weight:600;")

    def request_apply(self):
        try:
            settings = self.settings()
        except ValueError as exc:
            self.status_label.setText(str(exc))
            self.status_label.setStyleSheet("color:#b91c1c;font-weight:600;")
            return
        self.apply_requested.emit(self.index, settings)

    def mark_applied(self, settings):
        self.applied = dict(settings)
        self.clear_button.setEnabled(True)
        self.refresh_status()

    def mark_original(self):
        self.applied = None
        self.clear_button.setEnabled(False)
        self.refresh_status()

    def set_available(self, available):
        self.available = available
        self.refresh_status()


class DashboardWindow(QtWidgets.QMainWindow):
    """绑定一个已结束会话；原始记录只读，四图可独立分析。"""
    def __init__(self, session):
        super().__init__()
        self.session = Path(session)
        self.sources = discover_channels(self.session)
        self.channel_data = None
        self.filtered_groups = [None] * len(PLOT_GROUPS)
        self.x_values = None
        self.tasks = set()
        self.setWindowTitle(f"数据分析看板 V7.1 · {self.session.name}")
        self.resize(1500, 960)
        self.setMinimumSize(1100, 760)
        self.build_ui()
        self.channel_combo.currentIndexChanged.connect(self.load_selected_channel)
        QtCore.QTimer.singleShot(0, self.load_selected_channel)

    @property
    def busy(self):
        return bool(self.tasks)

    @property
    def has_any_filter(self):
        return any(values is not None for values in self.filtered_groups)

    def build_ui(self):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        title = QtWidgets.QLabel("数据分析看板  V7.1")
        title.setObjectName("title")
        layout.addWidget(title)
        self.session_label = QtWidgets.QLabel(str(self.session))
        self.session_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.session_label.setWordWrap(True)
        layout.addWidget(self.session_label)

        settings = QtWidgets.QGridLayout()
        self.channel_combo = QtWidgets.QComboBox()
        for source in self.sources:
            self.channel_combo.addItem(source.name.upper(), source.name)
        self.axis_combo = QtWidgets.QComboBox()
        self.axis_combo.currentIndexChanged.connect(self.axis_changed)
        self.sample_rate = QtWidgets.QSpinBox()
        self.sample_rate.setRange(1, 5000)
        self.sample_rate.setValue(1000)
        self.sample_rate.setSuffix(" Hz")
        self.sample_rate.setToolTip("标称时间 = 帧序号 / 采样率；不代表逐帧主机到达时间")
        self.sample_rate.valueChanged.connect(self.axis_changed)
        self.filter_toggle_button = QtWidgets.QPushButton("展开滤波设置 ▼")
        self.filter_toggle_button.setCheckable(True)
        self.filter_toggle_button.clicked.connect(self.toggle_filter_panel)
        widgets = [
            (QtWidgets.QLabel("通道"), self.channel_combo),
            (QtWidgets.QLabel("横轴"), self.axis_combo),
            (QtWidgets.QLabel("标称采样率"), self.sample_rate),
            (QtWidgets.QLabel("逐图滤波"), self.filter_toggle_button),
        ]
        for column, (label, widget) in enumerate(widgets):
            settings.addWidget(label, 0, column)
            settings.addWidget(widget, 1, column)
        settings.setColumnStretch(len(widgets), 1)
        layout.addLayout(settings)

        self.filter_panel = QtWidgets.QWidget()
        panel_layout = QtWidgets.QVBoxLayout(self.filter_panel)
        panel_layout.setContentsMargins(0, 4, 0, 4)
        panel_header = QtWidgets.QHBoxLayout()
        panel_header.addWidget(QtWidgets.QLabel("每张图独立设置滤波参数，互不影响；原始 TXT 始终只读。"))
        panel_header.addStretch()
        self.clear_all_filters_button = QtWidgets.QPushButton("全部取消滤波")
        self.clear_all_filters_button.clicked.connect(self.clear_all_filters)
        panel_header.addWidget(self.clear_all_filters_button)
        panel_layout.addLayout(panel_header)
        self.filter_tabs_widget = QtWidgets.QTabWidget()
        self.filter_tabs = []
        for index, spec in enumerate(PLOT_GROUPS):
            tab = FilterTab(index, spec)
            tab.apply_requested.connect(self.apply_group_filter)
            tab.clear_requested.connect(self.clear_group_filter)
            self.filter_tabs.append(tab)
            self.filter_tabs_widget.addTab(tab, spec["title"])
        panel_layout.addWidget(self.filter_tabs_widget)
        self.filter_panel.setVisible(False)
        layout.addWidget(self.filter_panel)
        self.filter_toggle_button.setChecked(False)

        self.graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self.graphics, 1)
        self.plots, self.curves = [], []
        for spec in PLOT_GROUPS:
            plot = self.graphics.addPlot(row=spec["row"], col=spec["column"], rowspan=spec["rowspan"], title=spec["title"])
            plot.setLabel("left", spec["unit"])
            plot.setLabel("bottom", "标称时间 (s)")
            plot.showGrid(x=True, y=True, alpha=0.15)
            plot.addLegend(offset=(8, 8))
            plot.setYRange(-1, 1)
            # 四个图表各自保留独立视窗；滚轮在哪个图上，就只缩放该图。
            self.plots.append(plot)
            for name, color in zip(spec["names"], PLOT_COLORS):
                curve = plot.plot(pen=pg.mkPen(color, width=1), name=name)
                curve.setClipToView(True)
                curve.setDownsampling(auto=True, method="peak")
                self.curves.append(curve)

        actions = QtWidgets.QHBoxLayout()
        self.zoom_in_button = QtWidgets.QPushButton("放大")
        self.zoom_in_button.clicked.connect(partial(self.zoom_views, 0.5))
        self.zoom_out_button = QtWidgets.QPushButton("缩小")
        self.zoom_out_button.clicked.connect(partial(self.zoom_views, 2.0))
        self.fit_button = QtWidgets.QPushButton("适应全记录")
        self.fit_button.clicked.connect(self.fit_all)
        self.export_data_button = QtWidgets.QPushButton("导出处理后数据 CSV")
        self.export_data_button.clicked.connect(self.export_data)
        self.save_image_button = QtWidgets.QPushButton("保存当前图片 PNG")
        self.save_image_button.clicked.connect(self.save_current_image)
        for button in [self.zoom_in_button, self.zoom_out_button, self.fit_button,
                       self.export_data_button, self.save_image_button]:
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status_label = QtWidgets.QLabel("等待加载会话数据")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.update_buttons()

    def toggle_filter_panel(self, checked):
        self.filter_panel.setVisible(checked)
        self.update_filter_toggle_text()

    def update_filter_toggle_text(self):
        count = sum(values is not None for values in self.filtered_groups)
        suffix = f"（{count} 图已滤波）" if count else ""
        prefix = "收起滤波设置 ▲" if self.filter_toggle_button.isChecked() else "展开滤波设置 ▼"
        self.filter_toggle_button.setText(prefix + suffix)

    def run_task(self, function, success, message):
        if self.busy:
            self.status_label.setText("已有后台任务运行，请等待完成后再操作。")
            return False
        self.status_label.setText(message)
        task = DashboardTask(function, self)
        self.tasks.add(task)
        task.succeeded.connect(success)
        task.failed.connect(self.task_failed)
        task.finished.connect(partial(self.task_finished, task))
        task.start()
        self.update_buttons()
        return True

    def task_finished(self, task):
        self.tasks.discard(task)
        task.deleteLater()
        self.update_buttons()

    def task_failed(self, message):
        self.status_label.setText(f"操作失败：{message}")
        QtWidgets.QMessageBox.critical(self, "数据看板", message)

    def update_buttons(self):
        loaded = self.channel_data is not None
        enabled = loaded and not self.busy
        self.channel_combo.setEnabled(not self.busy)
        for widget in [self.axis_combo, self.sample_rate, self.zoom_in_button,
                       self.zoom_out_button, self.fit_button, self.export_data_button,
                       self.save_image_button]:
            widget.setEnabled(enabled)
        self.filter_toggle_button.setEnabled(True)
        self.clear_all_filters_button.setEnabled(enabled and self.has_any_filter)
        for index, tab in enumerate(self.filter_tabs):
            available = loaded and self.channel_data.available_groups[index]
            tab.set_available(available)
            tab.setEnabled(available and not self.busy)
        self.update_filter_toggle_text()

    def clear_plots(self):
        self.x_values = None
        for curve in self.curves:
            curve.setData([], [])
        for spec, plot in zip(PLOT_GROUPS, self.plots):
            plot.setTitle(spec["title"])

    def reset_filters(self):
        self.filtered_groups = [None] * len(PLOT_GROUPS)
        for tab in self.filter_tabs:
            tab.mark_original()
        self.update_filter_toggle_text()

    def load_selected_channel(self):
        if self.busy or not self.sources:
            return
        self.channel_data = None
        self.reset_filters()
        self.clear_plots()
        self.update_buttons()
        name = self.channel_combo.currentData()
        self.run_task(partial(load_channel, self.session, name), self.channel_loaded,
                      f"正在后台加载 {name.upper()}，原始 TXT 保持不变……")

    def channel_loaded(self, data):
        self.channel_data = data
        self.reset_filters()
        self.sample_rate.blockSignals(True)
        self.sample_rate.setValue(max(1, min(5000, round(data.rate_hz))))
        self.sample_rate.blockSignals(False)
        current_axis = self.axis_combo.currentData() or "nominal"
        self.axis_combo.blockSignals(True)
        self.axis_combo.clear()
        self.axis_combo.addItem("标称时间 (s)", "nominal")
        self.axis_combo.addItem("采样序号", "frame")
        if data.has_device_time:
            self.axis_combo.addItem("设备时间 (s)", "device")
        index = self.axis_combo.findData(current_axis)
        self.axis_combo.setCurrentIndex(max(0, index))
        self.axis_combo.blockSignals(False)
        self.refresh_plots(reset_range=True)
        summary = data.summary
        state = summary.get("state", "旧记录/无摘要")
        header = summary.get("frame_header") or "未知帧型"
        warning = f"；{summary['summary_warning']}" if summary.get("summary_warning") else ""
        self.status_label.setText(
            f"{data.name.upper()}：{data.frame_count:,} 帧；{state}；{header}；"
            f"标称 {data.rate_hz:g} Hz。仅显示降采样，导出保留全部采样点{warning}。"
        )
        self.update_buttons()

    def current_values(self):
        if self.channel_data is None:
            return None
        if not self.has_any_filter:
            return self.channel_data.values
        values = self.channel_data.values.copy()
        for index, filtered in enumerate(self.filtered_groups):
            if filtered is not None:
                values[:, index * 3:(index + 1) * 3] = filtered
        return values

    def make_x_values(self):
        if self.channel_data is None:
            return None
        mode = self.axis_combo.currentData() or "nominal"
        if mode == "frame":
            return np.arange(self.channel_data.frame_count, dtype=np.float64)
        if mode == "device" and self.channel_data.has_device_time:
            return self.channel_data.device_time
        return nominal_times(self.channel_data.frame_count, self.sample_rate.value())

    def refresh_plots(self, reset_range=False):
        values = self.current_values()
        if values is None:
            self.clear_plots()
            return
        self.x_values = self.make_x_values()
        mode = self.axis_combo.currentData() or "nominal"
        axis_label = {"nominal": "标称时间 (s)", "frame": "采样序号", "device": "设备时间 (s)"}[mode]
        for group_index, (spec, plot) in enumerate(zip(PLOT_GROUPS, self.plots)):
            available = self.channel_data.available_groups[group_index]
            title = spec["title"]
            if not available:
                title += " · 当前帧型无数据"
            elif self.filtered_groups[group_index] is not None:
                title += " · 已滤波"
            plot.setTitle(title)
            plot.setLabel("bottom", axis_label)
            for axis in range(3):
                curve = self.curves[group_index * 3 + axis]
                if available:
                    curve.setData(self.x_values, values[:, group_index * 3 + axis])
                else:
                    curve.setData([], [])
        if reset_range:
            self.fit_all()

    def axis_changed(self, *_):
        if self.channel_data is not None:
            self.refresh_plots(reset_range=True)

    def apply_group_filter(self, group_index, settings):
        if self.channel_data is None or self.busy:
            return
        if not self.channel_data.available_groups[group_index]:
            self.status_label.setText("当前帧型下该组是补零占位，不能应用滤波。")
            return
        values = self.channel_data.values[:, group_index * 3:(group_index + 1) * 3]
        rate = self.channel_data.rate_hz
        title = PLOT_GROUPS[group_index]["title"]
        self.run_task(
            partial(apply_filter, values, settings, rate),
            lambda result: self.group_filter_applied(group_index, result, settings),
            f"正在后台处理{title}：{FilterTab.describe(settings)}……",
        )

    def group_filter_applied(self, group_index, values, settings):
        self.filtered_groups[group_index] = values
        self.filter_tabs[group_index].mark_applied(settings)
        self.refresh_plots()
        self.update_buttons()
        self.status_label.setText(
            f"{PLOT_GROUPS[group_index]['title']}已应用 {FilterTab.describe(settings)}，仅本图生效；原始 TXT 未修改。"
        )

    def clear_group_filter(self, group_index):
        if self.busy:
            return
        self.filtered_groups[group_index] = None
        self.filter_tabs[group_index].mark_original()
        self.refresh_plots()
        self.update_buttons()
        self.status_label.setText(f"{PLOT_GROUPS[group_index]['title']}已取消滤波，恢复原始数据。")

    def clear_all_filters(self):
        if self.busy:
            return
        self.reset_filters()
        self.refresh_plots()
        self.update_buttons()
        self.status_label.setText("已取消全部滤波，四组图表恢复原始数据。")

    def zoom_views(self, factor):
        """按钮缩放同时作用于四图，但各自围绕自己的当前视窗中心。"""
        if self.channel_data is None or self.busy:
            return
        for plot in self.plots:
            x_min, x_max = plot.getViewBox().viewRange()[0]
            x_center = (x_min + x_max) / 2
            x_half = max(1e-9, (x_max - x_min) * factor / 2)
            plot.setXRange(x_center - x_half, x_center + x_half, padding=0)
            y_min, y_max = plot.getViewBox().viewRange()[1]
            y_center = (y_min + y_max) / 2
            y_half = max(1e-9, (y_max - y_min) * factor / 2)
            plot.setYRange(y_center - y_half, y_center + y_half, padding=0)

    def fit_all(self):
        if self.channel_data is None or self.x_values is None:
            return
        finite = self.x_values[np.isfinite(self.x_values)]
        low, high = (float(np.min(finite)), float(np.max(finite))) if finite.size else (0.0, 1.0)
        if high <= low:
            high = low + 1.0 / self.sample_rate.value()
        for plot in self.plots:
            plot.setXRange(low, high, padding=0)
            plot.enableAutoRange(axis="y", enable=True)
            plot.getViewBox().updateAutoRange()
            plot.disableAutoRange()

    def export_data(self):
        if self.channel_data is None or self.busy:
            return
        default = self.session / "analysis" / f"{self.channel_data.name}_processed_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出处理后数据", str(default), "CSV 文件 (*.csv)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".csv"
        values = self.current_values().copy()
        channel = self.channel_data
        rate = self.sample_rate.value()
        self.run_task(partial(export_processed_csv, channel, values, path, rate),
                      self.data_exported, "正在后台导出完整处理后数据，不包含屏幕降采样……")

    def data_exported(self, path):
        self.status_label.setText(f"处理后数据已导出：{path}")

    def save_current_image(self):
        if self.channel_data is None or self.busy:
            return
        default = self.session / "analysis" / f"{self.channel_data.name}_dashboard_{datetime.now():%Y%m%d_%H%M%S}.png"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存当前图表图片", str(default), "PNG 图片 (*.png)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".png"
        output = Path(path)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            if not self.graphics.grab().save(str(output), "PNG"):
                raise OSError("Qt 图片保存失败")
        except Exception as exc:
            self.task_failed(str(exc))
        else:
            self.status_label.setText(f"当前图表已保存：{output}；未覆盖原完整波形 waveform.png。")

    def closeEvent(self, event):
        if self.busy:
            self.status_label.setText("后台任务仍在运行，完成后才能安全关闭看板。")
            event.ignore()
            return
        event.accept()
