import os
import sys
import time
import threading
from collections import deque
import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# 支持中文字体显示
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'SimSun', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

DEFAULT_FR_RULES = [
    (0, 2, 1.0),
    (0, 1, 1.0),
    (1, 4, 21474.83648),
    (1, 4, 21474.83648),
    (1, 4, 21474.83648),
    (1, 4, 26843.5456),
    (1, 4, 26843.5456),
    (1, 4, 26843.5456),
    (1, 2, 80.0),
    (1, 2, 80.0),
    (1, 2, 80.0),
    (0, 4, 1000.0),
    (0, 1, 100.0),
    (0, 1, 100.0),
    (0, 1, 100.0),
    (1, 3, 1000.0),
    (1, 3, 1000.0),
    (1, 3, 1000.0),
    (1, 3, 10000.0),
    (1, 3, 10000.0),
    (1, 3, 10000.0),
    (0, 2, 1000.0),
    (0, 1, 1.0),
]

def load_fr_rules(filepath="fr.txt"):
    if not os.path.exists(filepath):
        return list(DEFAULT_FR_RULES)
    rules = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) >= 3:
                    try:
                        is_signed = int(parts[0])
                        length = int(parts[1])
                        scale = float(parts[2])
                        rules.append((is_signed, length, scale))
                    except ValueError:
                        continue
    except Exception:
        pass
    return rules if rules else list(DEFAULT_FR_RULES)

def parse_single_frame(b_arr, rules):
    vals = []
    offset = 0
    for is_signed, length, scale in rules:
        sub = b_arr[offset : offset + length]
        offset += length
        val = int.from_bytes(sub, byteorder="little", signed=(is_signed == 1))
        vals.append(val / scale)
    return vals

def format_row(vals, rules):
    parts = []
    for i, v in enumerate(vals):
        scale = rules[i][2]
        if scale == 1.0:
            parts.append(f"{int(v):17d}")
        else:
            parts.append(f"{v:24.8f}")
    return "".join(parts) + "\n"

# 虚拟串口仿真 Worker (从 d.txt 模拟以 200Hz 发送与解析)
class VirtualSerialWorker:
    def __init__(self, d_file, rules, output_file, update_cb, data_queue, rate_hz=200):
        self.d_file = d_file
        self.rules = rules
        self.output_file = output_file
        self.update_cb = update_cb
        self.data_queue = data_queue
        self.rate_hz = rate_hz
        self.is_running = False
        self.thread = None
        self.count = 0
        self.port = "虚拟仿真(d.txt)"

    def start(self):
        self.is_running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.is_running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def _run(self):
        if not os.path.exists(self.d_file):
            self.update_cb(self.port, f"找不到 {self.d_file}", 0)
            return

        with open(self.d_file, "r", encoding="utf-8", errors="ignore") as f:
            hex_text = f.read()
        clean_hex = "".join([c for c in hex_text if c in "0123456789abcdefABCDEF"])
        if len(clean_hex) % 2 != 0:
            clean_hex = clean_hex[:-1]
        raw_data = bytes.fromhex(clean_hex)
        frame_len = sum(r[1] for r in self.rules) if self.rules else 61

        frames = []
        idx = 0
        while idx <= len(raw_data) - frame_len:
            pos = raw_data.find(b"\xeb\x90", idx)
            if pos == -1:
                break
            if pos + frame_len <= len(raw_data):
                frames.append(raw_data[pos : pos + frame_len])
                idx = pos + frame_len
            else:
                break

        if not frames:
            self.update_cb(self.port, "d.txt中无有效帧", 0)
            return

        interval = 1.0 / self.rate_hz
        with open(self.output_file, "w", encoding="utf-8") as out_f:
            self.update_cb(self.port, "仿真运行中...", 0)
            frame_idx = 0
            while self.is_running:
                t0 = time.perf_counter()
                frame = frames[frame_idx]
                frame_idx = (frame_idx + 1) % len(frames)

                vals = parse_single_frame(frame, self.rules)
                line_str = format_row(vals, self.rules)
                out_f.write(line_str)
                out_f.flush()

                self.data_queue.append(vals)
                self.count += 1

                if self.count % 10 == 0:
                    self.update_cb(self.port, "仿真运行中", self.count)

                elapsed = time.perf_counter() - t0
                sleep_time = interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        self.update_cb(self.port, "已停止", self.count)

class SerialWorker:
    def __init__(self, port, baudrate, rules, output_file, update_cb, data_queue):
        self.port = port
        self.baudrate = baudrate
        self.rules = rules
        self.output_file = output_file
        self.update_cb = update_cb
        self.data_queue = data_queue
        self.is_running = False
        self.thread = None
        self.count = 0
        self.frame_len = sum(r[1] for r in rules) if rules else 61

    def start(self):
        self.is_running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.is_running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def _run(self):
        buffer = bytearray()
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
        except Exception as e:
            self.update_cb(self.port, f"打开失败: {e}", 0)
            return

        with open(self.output_file, "w", encoding="utf-8") as out_f:
            self.update_cb(self.port, "采集运行中...", 0)
            while self.is_running:
                try:
                    chunk = ser.read(1024)
                    if not chunk:
                        continue
                    buffer.extend(chunk)

                    # 帧头同步：0xEB 0x90
                    while len(buffer) >= self.frame_len:
                        idx = buffer.find(b"\xeb\x90")
                        if idx == -1:
                            buffer = buffer[-1:]
                            break
                        if idx > 0:
                            buffer = buffer[idx:]

                        if len(buffer) < self.frame_len:
                            break

                        frame = bytes(buffer[: self.frame_len])
                        vals = parse_single_frame(frame, self.rules)
                        line_str = format_row(vals, self.rules)
                        out_f.write(line_str)
                        out_f.flush()

                        # 放入波形实时预览缓冲
                        self.data_queue.append(vals)

                        buffer = buffer[self.frame_len :]
                        self.count += 1
                        if self.count % 10 == 0:
                            self.update_cb(self.port, "采集运行中", self.count)
                except Exception as e:
                    self.update_cb(self.port, f"异常: {e}", self.count)
                    break

        try:
            ser.close()
        except:
            pass
        self.update_cb(self.port, "已停止", self.count)

class AppV2:
    def __init__(self, root):
        self.root = root
        self.root.title("多串口数据采集与实时波形分析系统 V2.0")
        self.root.geometry("1280x780")
        self.root.minsize(960, 600)

        # 全屏状态
        self.is_fullscreen = False
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self.exit_fullscreen)

        self.rules = load_fr_rules("fr.txt")
        self.workers = {}
        self.data_buffers = [deque(maxlen=200) for _ in range(3)]
        self.current_preview_ch = 0
        self.is_collecting = False

        self._create_widgets()
        self._refresh_ports()
        self._init_plot()
        self._schedule_plot_update()

    def toggle_fullscreen(self, event=None):
        self.is_fullscreen = not self.is_fullscreen
        self.root.attributes("-fullscreen", self.is_fullscreen)
        self.btn_fullscreen.config(text="退出全屏 (Esc)" if self.is_fullscreen else "全屏显示 (F11)")

    def exit_fullscreen(self, event=None):
        if self.is_fullscreen:
            self.is_fullscreen = False
            self.root.attributes("-fullscreen", False)
            self.btn_fullscreen.config(text="全屏显示 (F11)")

    def _create_widgets(self):
        # 顶部工具栏
        top_bar = ttk.Frame(self.root, padding=6)
        top_bar.pack(fill="x", side="top")

        byte_sum = sum(r[1] for r in self.rules) if self.rules else 0
        ttk.Label(top_bar, text=f"配置文件 fr.txt：已加载 {len(self.rules)} 条规则 ({byte_sum} 字节/帧)", 
                  foreground="#008000" if self.rules else "red", font=("Microsoft YaHei", 9, "bold")).pack(side="left", padx=5)

        self.btn_fullscreen = ttk.Button(top_bar, text="全屏显示 (F11)", command=self.toggle_fullscreen)
        self.btn_fullscreen.pack(side="right", padx=5)
        ttk.Button(top_bar, text="刷新可用串口", command=self._refresh_ports).pack(side="right", padx=5)

        # 串口配置容器 (3通道)
        cfg_box = ttk.LabelFrame(self.root, text="串口硬件配置", padding=6)
        cfg_box.pack(fill="x", padx=10, pady=2)

        baud_options = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]
        self.port_configs = []

        for i in range(3):
            row = ttk.Frame(cfg_box, padding=2)
            row.pack(fill="x")

            enable_var = tk.BooleanVar(value=True if i == 0 else False)
            chk = ttk.Checkbutton(row, text=f"通道 {i+1}", variable=enable_var)
            chk.pack(side="left", padx=5)

            ttk.Label(row, text="端口:").pack(side="left")
            port_cb = ttk.Combobox(row, width=18, state="readonly")
            port_cb.pack(side="left", padx=4)

            ttk.Label(row, text="波特率:").pack(side="left")
            baud_cb = ttk.Combobox(row, values=baud_options, width=10)
            baud_cb.set("230400")
            baud_cb.pack(side="left", padx=4)

            ttk.Label(row, text="输出文件名:").pack(side="left")
            out_entry = ttk.Entry(row, width=16)
            out_entry.insert(0, f"data_ch{i+1}.txt")
            out_entry.pack(side="left", padx=4)

            status_lbl = ttk.Label(row, text="未连接", foreground="gray", width=20)
            status_lbl.pack(side="left", padx=6)

            self.port_configs.append({
                "enable": enable_var,
                "port_cb": port_cb,
                "baud_cb": baud_cb,
                "out_entry": out_entry,
                "status_lbl": status_lbl,
            })

        # 控制栏 & 通道切换栏
        mid_bar = ttk.Frame(self.root, padding=6)
        mid_bar.pack(fill="x", padx=10, pady=2)

        self.start_btn = tk.Button(mid_bar, text="▶ 开始采集并转换", bg="#2E7D32", fg="white", 
                                   font=("Microsoft YaHei", 10, "bold"), width=16, relief="raised", command=self.start_collection)
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = tk.Button(mid_bar, text="■ 结束采集", bg="#C62828", fg="white", 
                                  font=("Microsoft YaHei", 10, "bold"), width=12, relief="raised", state="disabled", command=self.stop_collection)
        self.stop_btn.pack(side="left", padx=5)

        ttk.Separator(mid_bar, orient="vertical").pack(side="left", fill="y", padx=15)

        ttk.Label(mid_bar, text="实时波形预览通道切换：", font=("Microsoft YaHei", 10, "bold")).pack(side="left", padx=5)

        self.ch_btns = []
        for i in range(3):
            btn = tk.Button(mid_bar, text=f"通道 {i+1} (CH{i+1})", font=("Microsoft YaHei", 9, "bold"),
                            width=14, relief="solid", bd=1,
                            command=lambda ch=i: self.switch_preview_channel(ch))
            btn.pack(side="left", padx=4)
            self.ch_btns.append(btn)

        self._update_channel_button_styles()

        # 图表主区域
        self.plot_frame = ttk.Frame(self.root)
        self.plot_frame.pack(fill="both", expand=True, padx=10, pady=5)

    def _init_plot(self):
        self.fig = plt.Figure(figsize=(12, 5.5), dpi=100, constrained_layout=True)
        gs = self.fig.add_gridspec(2, 3)

        self.ax_gyro = self.fig.add_subplot(gs[0, 0])
        self.ax_acc = self.fig.add_subplot(gs[1, 0])
        self.ax_hacc = self.fig.add_subplot(gs[:, 1])
        self.ax_uwb = self.fig.add_subplot(gs[:, 2])

        # 1. 陀螺仪角速度 (列 3,4,5 -> 索引 2,3,4)
        self.line_wx, = self.ax_gyro.plot([], [], label="wx", color="#E53935", lw=1.2)
        self.line_wy, = self.ax_gyro.plot([], [], label="wy", color="#43A047", lw=1.2)
        self.line_wz, = self.ax_gyro.plot([], [], label="wz", color="#1E88E5", lw=1.2)
        self.ax_gyro.set_title("1. IMU 角速度 (ADIS16505)", fontsize=10, fontweight="bold", pad=4)
        self.ax_gyro.set_ylabel("deg/s", fontsize=9)
        self.ax_gyro.grid(True, linestyle="--", alpha=0.5)
        self.ax_gyro.legend(loc="upper right", fontsize=8)

        # 2. IMU 线加速度 (列 6,7,8 -> 索引 5,6,7)
        self.line_ax, = self.ax_acc.plot([], [], label="ax", color="#E53935", lw=1.2)
        self.line_ay, = self.ax_acc.plot([], [], label="ay", color="#43A047", lw=1.2)
        self.line_az, = self.ax_acc.plot([], [], label="az", color="#1E88E5", lw=1.2)
        self.ax_acc.set_title("IMU 线加速度 (ADIS16505)", fontsize=10, fontweight="bold", pad=4)
        self.ax_acc.set_ylabel("m/s²", fontsize=9)
        self.ax_acc.grid(True, linestyle="--", alpha=0.5)
        self.ax_acc.legend(loc="upper right", fontsize=8)

        # 3. 大量程加速度 (列 9,10,11 -> 索引 8,9,10)
        self.line_hx, = self.ax_hacc.plot([], [], label="X", color="#E53935", lw=1.2)
        self.line_hy, = self.ax_hacc.plot([], [], label="Y", color="#43A047", lw=1.2)
        self.line_hz, = self.ax_hacc.plot([], [], label="Z", color="#1E88E5", lw=1.2)
        self.ax_hacc.set_title("2. 大量程加速度计 (3轴)", fontsize=11, fontweight="bold", pad=6)
        self.ax_hacc.set_ylabel("m/s²", fontsize=9)
        self.ax_hacc.grid(True, linestyle="--", alpha=0.5)
        self.ax_hacc.legend(loc="upper right", fontsize=8)

        # 4. UWB 空间三维位置 (列 16,17,18 -> 索引 15,16,17)
        self.line_px, = self.ax_uwb.plot([], [], label="pos_x", color="#8E24AA", lw=1.2)
        self.line_py, = self.ax_uwb.plot([], [], label="pos_y", color="#3949AB", lw=1.2)
        self.line_pz, = self.ax_uwb.plot([], [], label="pos_z", color="#00897B", lw=1.2)
        self.ax_uwb.set_title("3. UWB 空间三维位置 (3轴)", fontsize=11, fontweight="bold", pad=6)
        self.ax_uwb.set_ylabel("m", fontsize=9)
        self.ax_uwb.grid(True, linestyle="--", alpha=0.5)
        self.ax_uwb.legend(loc="upper right", fontsize=8)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _refresh_ports(self):
        detected_ports = [p.device for p in serial.tools.list_ports.comports()]
        # 允许选择仿真回放源，方便无硬件时自测
        options = ["【仿真】d.txt回放"] + detected_ports
        for cfg in self.port_configs:
            cfg["port_cb"]["values"] = options
            curr = cfg["port_cb"].get()
            if not curr or curr not in options:
                cfg["port_cb"].set(options[0])

    def switch_preview_channel(self, ch_idx):
        self.current_preview_ch = ch_idx
        self._update_channel_button_styles()

    def _update_channel_button_styles(self):
        for i, btn in enumerate(self.ch_btns):
            if i == self.current_preview_ch:
                btn.config(bg="#1565C0", fg="white", relief="sunken")
            else:
                btn.config(bg="#F5F5F5", fg="black", relief="raised")

    def _schedule_plot_update(self):
        self._update_plot()
        self.root.after(100, self._schedule_plot_update)

    def _update_plot(self):
        buf = self.data_buffers[self.current_preview_ch]
        if not buf:
            return

        pts = list(buf)
        n = len(pts)
        x_axis = list(range(n))

        try:
            wx = [p[2] for p in pts]
            wy = [p[3] for p in pts]
            wz = [p[4] for p in pts]

            ax = [p[5] for p in pts]
            ay = [p[6] for p in pts]
            az = [p[7] for p in pts]

            hx = [p[8] for p in pts]
            hy = [p[9] for p in pts]
            hz = [p[10] for p in pts]

            px = [p[15] for p in pts]
            py = [p[16] for p in pts]
            pz = [p[17] for p in pts]

            self.line_wx.set_data(x_axis, wx)
            self.line_wy.set_data(x_axis, wy)
            self.line_wz.set_data(x_axis, wz)
            self.ax_gyro.relim()
            self.ax_gyro.autoscale_view()

            self.line_ax.set_data(x_axis, ax)
            self.line_ay.set_data(x_axis, ay)
            self.line_az.set_data(x_axis, az)
            self.ax_acc.relim()
            self.ax_acc.autoscale_view()

            self.line_hx.set_data(x_axis, hx)
            self.line_hy.set_data(x_axis, hy)
            self.line_hz.set_data(x_axis, hz)
            self.ax_hacc.relim()
            self.ax_hacc.autoscale_view()

            self.line_px.set_data(x_axis, px)
            self.line_py.set_data(x_axis, py)
            self.line_pz.set_data(x_axis, pz)
            self.ax_uwb.relim()
            self.ax_uwb.autoscale_view()

            self.canvas.draw_idle()
        except Exception:
            pass

    def update_status(self, port, status_text, count):
        def _ui():
            for cfg in self.port_configs:
                if cfg["port_cb"].get() == port or (cfg["port_cb"].get() == "【仿真】d.txt回放" and "仿真" in port):
                    cfg["status_lbl"].config(text=f"{status_text} ({count}条)")
        self.root.after(0, _ui)

    def start_collection(self):
        if not self.rules:
            messagebox.showerror("错误", "未找到或未能加载 fr.txt 配置文件！")
            return

        enabled_cfgs = [(i, c) for i, c in enumerate(self.port_configs) if c["enable"].get()]
        if not enabled_cfgs:
            messagebox.showwarning("提醒", "请至少勾选一个启用的串口通道！")
            return

        selected_ports = [c["port_cb"].get() for _, c in enabled_cfgs]
        # 如果不是纯仿真模式，检查物理串口是否冲突
        non_sim_ports = [p for p in selected_ports if not p.startswith("【仿真】")]
        if len(non_sim_ports) != len(set(non_sim_ports)):
            messagebox.showwarning("提醒", "选中的物理串口存在重复，请为不同通道分配不同串口！")
            return

        for b in self.data_buffers:
            b.clear()

        self.workers = {}
        for ch_idx, cfg in enabled_cfgs:
            port = cfg["port_cb"].get()
            out_file = cfg["out_entry"].get().strip() or f"data_ch{ch_idx+1}.txt"

            if port.startswith("【仿真】"):
                # 使用内置的 d.txt 仿真 Worker
                worker = VirtualSerialWorker("d.txt", self.rules, out_file, self.update_status, self.data_buffers[ch_idx], rate_hz=200)
            else:
                try:
                    baud = int(cfg["baud_cb"].get())
                except ValueError:
                    baud = 230400
                worker = SerialWorker(port, baud, self.rules, out_file, self.update_status, self.data_buffers[ch_idx])

            self.workers[ch_idx] = worker
            worker.start()

        first_enabled_ch = enabled_cfgs[0][0]
        self.switch_preview_channel(first_enabled_ch)

        self.is_collecting = True
        self.start_btn.config(state="disabled", bg="#BDBDBD")
        self.stop_btn.config(state="normal", bg="#C62828")

    def stop_collection(self):
        for worker in self.workers.values():
            worker.stop()
        self.workers = {}
        self.is_collecting = False
        self.start_btn.config(state="normal", bg="#2E7D32")
        self.stop_btn.config(state="disabled", bg="#BDBDBD")

if __name__ == "__main__":
    root = tk.Tk()
    app = AppV2(root)
    root.mainloop()
