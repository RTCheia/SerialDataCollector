import os
import sys
import time
import threading
from collections import deque
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import ctypes

# ----------------- 开启 Windows 高分屏 (DPI-Aware) -----------------
# 彻底解决 4K 屏 / 150% 缩放下的字体渲染发虚模糊问题
try:
    # Per-monitor DPI awareness (Windows 8.1+)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        # System DPI awareness (Vista+)
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# 支持中文字体显示与高精度平滑抗锯齿
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Segoe UI', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# ----------------- 默认写死的协议规则 -----------------
DEFAULT_FR_RULES = [
    (0, 2, 1.0),          # 列1: 帧头 37099
    (0, 1, 1.0),          # 列2: Counter 帧计数
    (1, 4, 21474.83648),  # 列3: wx (deg/s)
    (1, 4, 21474.83648),  # 列4: wy (deg/s)
    (1, 4, 21474.83648),  # 列5: wz (deg/s)
    (1, 4, 26843.5456),   # 列6: ax (m/s²)
    (1, 4, 26843.5456),   # 列7: ay (m/s²)
    (1, 4, 26843.5456),   # 列8: az (m/s²)
    (1, 2, 80.0),         # 列9: hacc_x (m/s²)
    (1, 2, 80.0),         # 列10: hacc_y (m/s²)
    (1, 2, 80.0),         # 列11: hacc_z (m/s²)
    (0, 4, 1000.0),       # 列12: 时间戳 (s)
    (0, 1, 100.0),        # 列13: eop_x
    (0, 1, 100.0),        # 列14: eop_y
    (0, 1, 100.0),        # 列15: eop_z
    (1, 3, 1000.0),       # 列16: pos_x (m)
    (1, 3, 1000.0),       # 列17: pos_y (m)
    (1, 3, 1000.0),       # 列18: pos_z (m)
    (1, 3, 10000.0),      # 列19: vel_x (m/s)
    (1, 3, 10000.0),      # 列20: vel_y (m/s)
    (1, 3, 10000.0),      # 列21: vel_z (m/s)
    (0, 2, 1000.0),       # 列22: 电压 (V)
    (0, 1, 1.0),          # 列23: 校验位
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

# ----------------- 完整数据高清绘图与保存 (浅色现代风格) -----------------
def export_complete_plots(data_file, output_png_path, title_prefix=""):
    """读取已保存的完整 data_ch*.txt，生成全长 3 框高清大图并保存"""
    if not os.path.exists(data_file):
        return False

    records = []
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 18:
                    try:
                        records.append([float(x) for x in parts])
                    except ValueError:
                        continue
    except Exception:
        return False

    if not records:
        return False

    n = len(records)
    t_vals = [r[11] for r in records]
    if len(t_vals) > 1 and (t_vals[-1] - t_vals[0] > 0):
        t_axis = [t - t_vals[0] for t in t_vals]
        x_label = "时间 Time (s)"
    else:
        t_axis = list(range(n))
        x_label = "采样点数 Sample Points"

    wx = [r[2] for r in records]
    wy = [r[3] for r in records]
    wz = [r[4] for r in records]

    ax = [r[5] for r in records]
    ay = [r[6] for r in records]
    az = [r[7] for r in records]

    hx = [r[8] for r in records]
    hy = [r[9] for r in records]
    hz = [r[10] for r in records]

    px = [r[15] for r in records]
    py = [r[16] for r in records]
    pz = [r[17] for r in records]

    # 浅色清爽明快风格
    fig = plt.Figure(figsize=(18, 8), dpi=150)
    fig.patch.set_facecolor('#F8F9FA')
    gs = fig.add_gridspec(2, 3, wspace=0.22, hspace=0.26)

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[1, 0])
    ax3 = fig.add_subplot(gs[:, 1])
    ax4 = fig.add_subplot(gs[:, 2])

    for a in [ax1, ax2, ax3, ax4]:
        a.set_facecolor('#FFFFFF')
        a.tick_params(colors='#495057', labelsize=8)
        for spine in a.spines.values():
            spine.set_color('#CED4DA')
        a.grid(True, linestyle="--", alpha=0.6, color="#E9ECEF")

    # 1. 陀螺仪角速度
    ax1.plot(t_axis, wx, label="wx", color="#D32F2F", lw=1.0)
    ax1.plot(t_axis, wy, label="wy", color="#2E7D32", lw=1.0)
    ax1.plot(t_axis, wz, label="wz", color="#1565C0", lw=1.0)
    ax1.set_title("1. IMU 角速度 (ADIS16505)", color="#212529", fontsize=10, fontweight="bold", pad=5)
    ax1.set_ylabel("deg/s", color="#495057", fontsize=8)
    ax1.legend(loc="upper right", fontsize=8, facecolor='#FFFFFF', edgecolor='#CED4DA', labelcolor='#212529')

    # 2. IMU 线加速度
    ax2.plot(t_axis, ax, label="ax", color="#D32F2F", lw=1.0)
    ax2.plot(t_axis, ay, label="ay", color="#2E7D32", lw=1.0)
    ax2.plot(t_axis, az, label="az", color="#1565C0", lw=1.0)
    ax2.set_title("IMU 线加速度 (ADIS16505)", color="#212529", fontsize=10, fontweight="bold", pad=5)
    ax2.set_ylabel("m/s²", color="#495057", fontsize=8)
    ax2.set_xlabel(x_label, color="#495057", fontsize=8)
    ax2.legend(loc="upper right", fontsize=8, facecolor='#FFFFFF', edgecolor='#CED4DA', labelcolor='#212529')

    # 3. 大量程冲击加速度
    ax3.plot(t_axis, hx, label="X", color="#D32F2F", lw=1.0)
    ax3.plot(t_axis, hy, label="Y", color="#2E7D32", lw=1.0)
    ax3.plot(t_axis, hz, label="Z", color="#1565C0", lw=1.0)
    ax3.set_title("2. 大量程冲击加速度计 (3轴)", color="#212529", fontsize=11, fontweight="bold", pad=6)
    ax3.set_ylabel("m/s²", color="#495057", fontsize=9)
    ax3.set_xlabel(x_label, color="#495057", fontsize=8)
    ax3.legend(loc="upper right", fontsize=8, facecolor='#FFFFFF', edgecolor='#CED4DA', labelcolor='#212529')

    # 4. UWB 空间三维位置
    ax4.plot(t_axis, px, label="pos_x", color="#7B1FA2", lw=1.0)
    ax4.plot(t_axis, py, label="pos_y", color="#0288D1", lw=1.0)
    ax4.plot(t_axis, pz, label="pos_z", color="#00796B", lw=1.0)
    ax4.set_title("3. UWB 空间三维位置 (3轴)", color="#212529", fontsize=11, fontweight="bold", pad=6)
    ax4.set_ylabel("m", color="#495057", fontsize=9)
    ax4.set_xlabel(x_label, color="#495057", fontsize=8)
    ax4.legend(loc="upper right", fontsize=8, facecolor='#FFFFFF', edgecolor='#CED4DA', labelcolor='#212529')

    fig.suptitle(f"{title_prefix} - 全程数据波形综合汇总 (共 {n} 点)", color="#212529", fontsize=13, fontweight="bold", y=0.98)

    try:
        fig.savefig(output_png_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
        return True
    except Exception:
        return False

# ----------------- 虚拟仿真与串口接收线程 -----------------
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
        self.port = "【仿真】d.txt回放"

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
            self.update_cb(self.port, "d.txt无有效帧", 0)
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
        import serial
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

# ----------------- V3.0 VOFA 浅色纯净扁平风格主窗口 -----------------
class AppV3:
    def __init__(self, root):
        self.root = root
        self.root.title("多串口数据采集分析系统 V3.0 (VOFA Modern Light Edition)")
        self.root.geometry("1366x850")
        self.root.minsize(1050, 680)
        self.root.configure(bg="#F0F2F5") # 纯净优雅浅灰底色

        # 默认保存目录：当前目录下的 /data 文件夹
        self.save_dir = os.path.abspath(os.path.join(os.getcwd(), "data"))
        if not os.path.exists(self.save_dir):
            try:
                os.makedirs(self.save_dir, exist_ok=True)
            except:
                pass

        # 全屏状态
        self.is_fullscreen = False
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self.exit_fullscreen)

        # 动态 X 轴缩放（滑动窗口长度）
        self.x_window_len = 200
        self.min_window_len = 50
        self.max_window_len = 1000
        self.step_window_len = 50

        self.rules = load_fr_rules("fr.txt")
        self.workers = {}
        self.data_buffers = [deque(maxlen=1500) for _ in range(3)]
        self.current_preview_ch = 0
        self.is_collecting = False
        self.saved_output_files = {}

        self._apply_light_theme()
        self._create_widgets()
        self._refresh_ports()
        self._init_plot()
        self._schedule_plot_update()

    def _apply_light_theme(self):
        style = ttk.Style()
        style.theme_use("clam")

        # 全局大字体清晰浅色系配置
        style.configure(".", background="#F0F2F5", foreground="#212529", font=("Microsoft YaHei", 10))
        style.configure("TFrame", background="#F0F2F5")
        style.configure("TLabelframe", background="#FFFFFF", bordercolor="#DEE2E6", font=("Microsoft YaHei", 10, "bold"))
        style.configure("TLabelframe.Label", background="#FFFFFF", foreground="#007ACC", font=("Microsoft YaHei", 10, "bold"))
        style.configure("TLabel", background="#FFFFFF", foreground="#212529", font=("Microsoft YaHei", 10))

        # Combobox 浅色立体清晰样式
        style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")], selectbackground=[("readonly", "#007ACC")], foreground=[("readonly", "#212529")])
        style.configure("TCombobox", fieldbackground="#FFFFFF", foreground="#212529", bordercolor="#CED4DA", padding=3)

        # Checkbutton 样式
        style.configure("TCheckbutton", background="#FFFFFF", foreground="#212529", font=("Microsoft YaHei", 10, "bold"))

        # Entry 样式
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground="#212529", bordercolor="#CED4DA", padding=3)

    def toggle_fullscreen(self, event=None):
        self.is_fullscreen = not self.is_fullscreen
        self.root.attributes("-fullscreen", self.is_fullscreen)
        self.btn_fullscreen.config(text="🗗 退出全屏 (Esc)" if self.is_fullscreen else "🗖 全屏显示 (F11)")

    def exit_fullscreen(self, event=None):
        if self.is_fullscreen:
            self.is_fullscreen = False
            self.root.attributes("-fullscreen", False)
            self.btn_fullscreen.config(text="🗖 全屏显示 (F11)")

    def _choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.save_dir, title="选择数据与图片保存目录")
        if d:
            self.save_dir = os.path.abspath(d)
            self.lbl_savedir.config(text=self.save_dir)

    def _zoom_x_in(self):
        if self.x_window_len > self.min_window_len:
            self.x_window_len -= self.step_window_len
            self.lbl_x_len.config(text=f"{self.x_window_len} 点")

    def _zoom_x_out(self):
        if self.x_window_len < self.max_window_len:
            self.x_window_len += self.step_window_len
            self.lbl_x_len.config(text=f"{self.x_window_len} 点")

    def _create_widgets(self):
        # 1. 顶部状态栏与目录配置条 (VOFA Light Bar)
        top_bar = tk.Frame(self.root, bg="#FFFFFF", height=50, padx=16, pady=8, highlightthickness=1, highlightbackground="#DEE2E6")
        top_bar.pack(fill="x", side="top")

        tk.Label(top_bar, text="VOFA+", fg="#007ACC", bg="#FFFFFF", font=("Microsoft YaHei", 13, "bold")).pack(side="left", padx=(0, 15))
        tk.Label(top_bar, text="数据存储目录:", fg="#6C757D", bg="#FFFFFF", font=("Microsoft YaHei", 10, "bold")).pack(side="left")

        self.lbl_savedir = tk.Label(top_bar, text=self.save_dir, fg="#2E7D32", bg="#E8F5E9", font=("Microsoft YaHei", 10), padx=10, pady=3, relief="flat")
        self.lbl_savedir.pack(side="left", padx=8)

        btn_dir = tk.Button(top_bar, text="更改目录...", bg="#E9ECEF", fg="#495057", activebackground="#DEE2E6", font=("Microsoft YaHei", 9),
                            relief="flat", cursor="hand2", command=self._choose_dir, padx=10, pady=2)
        btn_dir.pack(side="left", padx=5)

        self.btn_fullscreen = tk.Button(top_bar, text="🗖 全屏显示 (F11)", bg="#E9ECEF", fg="#495057", activebackground="#DEE2E6", font=("Microsoft YaHei", 9, "bold"),
                                        relief="flat", cursor="hand2", command=self.toggle_fullscreen, padx=12, pady=2)
        self.btn_fullscreen.pack(side="right", padx=6)

        btn_refresh = tk.Button(top_bar, text="🔄 刷新串口", bg="#E9ECEF", fg="#495057", activebackground="#DEE2E6", font=("Microsoft YaHei", 9, "bold"),
                                relief="flat", cursor="hand2", command=self._refresh_ports, padx=12, pady=2)
        btn_refresh.pack(side="right", padx=6)

        # 2. 串口配置面板
        cfg_container = tk.Frame(self.root, bg="#FFFFFF", padx=16, pady=10, highlightthickness=1, highlightbackground="#DEE2E6")
        cfg_container.pack(fill="x", padx=14, pady=8)

        tk.Label(cfg_container, text="串口硬件与输出通道配置", fg="#007ACC", bg="#FFFFFF", font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 6))

        baud_options = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]
        self.port_configs = []

        for i in range(3):
            row = tk.Frame(cfg_container, bg="#FFFFFF", pady=4)
            row.pack(fill="x")

            enable_var = tk.BooleanVar(value=True if i == 0 else False)
            chk = ttk.Checkbutton(row, text=f"通道 {i+1} (CH{i+1})", variable=enable_var)
            chk.pack(side="left", padx=(5, 12))

            tk.Label(row, text="端口:", bg="#FFFFFF", fg="#495057", font=("Microsoft YaHei", 10)).pack(side="left")
            port_cb = ttk.Combobox(row, width=19, state="readonly")
            port_cb.pack(side="left", padx=5)

            tk.Label(row, text="波特率:", bg="#FFFFFF", fg="#495057", font=("Microsoft YaHei", 10)).pack(side="left", padx=(12, 0))
            baud_cb = ttk.Combobox(row, values=baud_options, width=9)
            baud_cb.set("230400")
            baud_cb.pack(side="left", padx=5)

            tk.Label(row, text="文件名:", bg="#FFFFFF", fg="#495057", font=("Microsoft YaHei", 10)).pack(side="left", padx=(12, 0))
            out_entry = tk.Entry(row, bg="#F8F9FA", fg="#212529", insertbackground="#212529", relief="solid", bd=1, font=("Microsoft YaHei", 10), width=16)
            out_entry.insert(0, f"data_ch{i+1}.txt")
            out_entry.pack(side="left", padx=5)

            status_lbl = tk.Label(row, text="⚪ 空闲未连接", bg="#FFFFFF", fg="#6C757D", font=("Microsoft YaHei", 10), width=24, anchor="w")
            status_lbl.pack(side="left", padx=15)

            self.port_configs.append({
                "enable": enable_var,
                "port_cb": port_cb,
                "baud_cb": baud_cb,
                "out_entry": out_entry,
                "status_lbl": status_lbl,
            })

        # 3. 核心控制栏 (开始/结束 + 通道切换 + X轴时间窗调节)
        ctrl_bar = tk.Frame(self.root, bg="#FFFFFF", height=54, padx=16, pady=8, highlightthickness=1, highlightbackground="#DEE2E6")
        ctrl_bar.pack(fill="x", padx=14, pady=(0, 8))

        self.start_btn = tk.Button(ctrl_bar, text="▶ 开始采集并转换", bg="#28A745", fg="#FFFFFF", activebackground="#218838",
                                   font=("Microsoft YaHei", 10, "bold"), relief="flat", padx=18, pady=5, cursor="hand2", command=self.start_collection)
        self.start_btn.pack(side="left", padx=5)

        self.stop_btn = tk.Button(ctrl_bar, text="■ 结束采集并导出", bg="#DC3545", fg="#FFFFFF", activebackground="#C82333",
                                  font=("Microsoft YaHei", 10, "bold"), relief="flat", padx=18, pady=5, cursor="hand2", state="disabled", command=self.stop_collection)
        self.stop_btn.pack(side="left", padx=5)

        tk.Frame(ctrl_bar, width=1, bg="#DEE2E6").pack(side="left", fill="y", padx=18, pady=2)

        # 通道切换区
        tk.Label(ctrl_bar, text="实时预览通道:", bg="#FFFFFF", fg="#495057", font=("Microsoft YaHei", 10, "bold")).pack(side="left", padx=(0, 8))
        self.ch_btns = []
        for i in range(3):
            btn = tk.Button(ctrl_bar, text=f"CH {i+1}", font=("Microsoft YaHei", 9, "bold"),
                            relief="flat", cursor="hand2", padx=14, pady=4,
                            command=lambda ch=i: self.switch_preview_channel(ch))
            btn.pack(side="left", padx=4)
            self.ch_btns.append(btn)
        self._update_channel_button_styles()

        tk.Frame(ctrl_bar, width=1, bg="#DEE2E6").pack(side="left", fill="y", padx=18, pady=2)

        # X 轴坐标窗口加减微调器
        tk.Label(ctrl_bar, text="X轴采样窗:", bg="#FFFFFF", fg="#495057", font=("Microsoft YaHei", 10, "bold")).pack(side="left", padx=(0, 6))
        btn_minus = tk.Button(ctrl_bar, text="➖", bg="#E9ECEF", fg="#212529", activebackground="#DEE2E6", relief="flat", font=("Microsoft YaHei", 9, "bold"), width=3, cursor="hand2", command=self._zoom_x_in)
        btn_minus.pack(side="left", padx=2)

        self.lbl_x_len = tk.Label(ctrl_bar, text=f"{self.x_window_len} 点", bg="#F8F9FA", fg="#007ACC", font=("Microsoft YaHei", 10, "bold"), width=8, relief="solid", bd=1)
        self.lbl_x_len.pack(side="left", padx=4)

        btn_plus = tk.Button(ctrl_bar, text="➕", bg="#E9ECEF", fg="#212529", activebackground="#DEE2E6", relief="flat", font=("Microsoft YaHei", 9, "bold"), width=3, cursor="hand2", command=self._zoom_x_out)
        btn_plus.pack(side="left", padx=2)

        # 4. 图表主区域 (浅色干净背景)
        self.plot_frame = tk.Frame(self.root, bg="#FFFFFF", highlightthickness=1, highlightbackground="#DEE2E6")
        self.plot_frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))

    def _init_plot(self):
        # VOFA 浅色纯净绘图风格
        self.fig = plt.Figure(figsize=(13, 6.2), dpi=100)
        self.fig.patch.set_facecolor('#FFFFFF')
        gs = self.fig.add_gridspec(2, 3, wspace=0.20, hspace=0.24)

        self.ax_gyro = self.fig.add_subplot(gs[0, 0])
        self.ax_acc = self.fig.add_subplot(gs[1, 0])
        self.ax_hacc = self.fig.add_subplot(gs[:, 1])
        self.ax_uwb = self.fig.add_subplot(gs[:, 2])

        self.all_axes = [self.ax_gyro, self.ax_acc, self.ax_hacc, self.ax_uwb]

        for ax in self.all_axes:
            ax.set_facecolor('#FFFFFF')
            ax.tick_params(colors='#495057', labelsize=8)
            for spine in ax.spines.values():
                spine.set_color('#CED4DA')
            ax.grid(True, linestyle="--", alpha=0.5, color="#E9ECEF")

        # 1. 陀螺仪角速度 (列 3,4,5)
        self.line_wx, = self.ax_gyro.plot([], [], label="wx", color="#D32F2F", lw=1.2)
        self.line_wy, = self.ax_gyro.plot([], [], label="wy", color="#2E7D32", lw=1.2)
        self.line_wz, = self.ax_gyro.plot([], [], label="wz", color="#1565C0", lw=1.2)
        self.ax_gyro.set_title("1. IMU 角速度 (ADIS16505)", color="#212529", fontsize=10, fontweight="bold", pad=4)
        self.ax_gyro.set_ylabel("deg/s", color="#495057", fontsize=8)
        self.ax_gyro.legend(loc="upper right", fontsize=8, facecolor='#FFFFFF', edgecolor='#CED4DA', labelcolor='#212529')

        # 2. IMU 线加速度 (列 6,7,8)
        self.line_ax, = self.ax_acc.plot([], [], label="ax", color="#D32F2F", lw=1.2)
        self.line_ay, = self.ax_acc.plot([], [], label="ay", color="#2E7D32", lw=1.2)
        self.line_az, = self.ax_acc.plot([], [], label="az", color="#1565C0", lw=1.2)
        self.ax_acc.set_title("IMU 线加速度 (ADIS16505)", color="#212529", fontsize=10, fontweight="bold", pad=4)
        self.ax_acc.set_ylabel("m/s²", color="#495057", fontsize=8)
        self.ax_acc.legend(loc="upper right", fontsize=8, facecolor='#FFFFFF', edgecolor='#CED4DA', labelcolor='#212529')

        # 3. 大量程冲击加速度 (列 9,10,11)
        self.line_hx, = self.ax_hacc.plot([], [], label="X", color="#D32F2F", lw=1.2)
        self.line_hy, = self.ax_hacc.plot([], [], label="Y", color="#2E7D32", lw=1.2)
        self.line_hz, = self.ax_hacc.plot([], [], label="Z", color="#1565C0", lw=1.2)
        self.ax_hacc.set_title("2. 大量程冲击加速度计 (3轴)", color="#212529", fontsize=11, fontweight="bold", pad=6)
        self.ax_hacc.set_ylabel("m/s²", color="#495057", fontsize=9)
        self.ax_hacc.legend(loc="upper right", fontsize=8, facecolor='#FFFFFF', edgecolor='#CED4DA', labelcolor='#212529')

        # 4. UWB 空间三维位置 (列 16,17,18)
        self.line_px, = self.ax_uwb.plot([], [], label="pos_x", color="#7B1FA2", lw=1.2)
        self.line_py, = self.ax_uwb.plot([], [], label="pos_y", color="#0288D1", lw=1.2)
        self.line_pz, = self.ax_uwb.plot([], [], label="pos_z", color="#00796B", lw=1.2)
        self.ax_uwb.set_title("3. UWB 空间三维位置 (3轴)", color="#212529", fontsize=11, fontweight="bold", pad=6)
        self.ax_uwb.set_ylabel("m", color="#495057", fontsize=9)
        self.ax_uwb.legend(loc="upper right", fontsize=8, facecolor='#FFFFFF', edgecolor='#CED4DA', labelcolor='#212529')

        self.fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _refresh_ports(self):
        import serial.tools.list_ports
        detected_ports = [p.device for p in serial.tools.list_ports.comports()]
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
                btn.config(bg="#007ACC", fg="#FFFFFF")
            else:
                btn.config(bg="#E9ECEF", fg="#495057")

    def _schedule_plot_update(self):
        self._update_plot()
        self.root.after(80, self._schedule_plot_update)

    def _update_plot(self):
        buf = self.data_buffers[self.current_preview_ch]
        if not buf:
            return

        all_pts = list(buf)
        pts = all_pts[-self.x_window_len :]
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
                    cfg["status_lbl"].config(text=f"🟢 {status_text} ({count}条)", fg="#28A745")
        self.root.after(0, _ui)

    def start_collection(self):
        if not self.rules:
            messagebox.showerror("错误", "未找到或未能加载有效解析规则！")
            return

        enabled_cfgs = [(i, c) for i, c in enumerate(self.port_configs) if c["enable"].get()]
        if not enabled_cfgs:
            messagebox.showwarning("提醒", "请至少勾选一个启用的串口通道！")
            return

        selected_ports = [c["port_cb"].get() for _, c in enabled_cfgs]
        non_sim_ports = [p for p in selected_ports if not p.startswith("【仿真】")]
        if len(non_sim_ports) != len(set(non_sim_ports)):
            messagebox.showwarning("提醒", "选中的物理串口存在重复，请为不同通道分配不同串口！")
            return

        os.makedirs(self.save_dir, exist_ok=True)

        for b in self.data_buffers:
            b.clear()

        self.workers = {}
        self.saved_output_files = {}

        for ch_idx, cfg in enabled_cfgs:
            port = cfg["port_cb"].get()
            fname = cfg["out_entry"].get().strip() or f"data_ch{ch_idx+1}.txt"
            full_out_path = os.path.join(self.save_dir, fname)
            self.saved_output_files[ch_idx] = full_out_path

            if port.startswith("【仿真】"):
                worker = VirtualSerialWorker("d.txt", self.rules, full_out_path, self.update_status, self.data_buffers[ch_idx], rate_hz=200)
            else:
                try:
                    baud = int(cfg["baud_cb"].get())
                except ValueError:
                    baud = 230400
                worker = SerialWorker(port, baud, self.rules, full_out_path, self.update_status, self.data_buffers[ch_idx])

            self.workers[ch_idx] = worker
            worker.start()

        first_enabled_ch = enabled_cfgs[0][0]
        self.switch_preview_channel(first_enabled_ch)

        self.is_collecting = True
        self.start_btn.config(state="disabled", bg="#CED4DA")
        self.stop_btn.config(state="normal", bg="#DC3545")

    def stop_collection(self):
        for worker in self.workers.values():
            worker.stop()
        self.workers = {}
        self.is_collecting = False
        self.start_btn.config(state="normal", bg="#28A745")
        self.stop_btn.config(state="disabled", bg="#CED4DA")

        for cfg in self.port_configs:
            if cfg["enable"].get():
                cfg["status_lbl"].config(text="⏹ 采集已停止，正在绘图...", fg="#E67E22")

        threading.Thread(target=self._generate_summary_images, daemon=True).start()

    def _generate_summary_images(self):
        exported_imgs = []
        for ch_idx, txt_path in self.saved_output_files.items():
            base_name = os.path.splitext(os.path.basename(txt_path))[0]
            png_path = os.path.join(self.save_dir, f"{base_name}_waveform.png")
            ok = export_complete_plots(txt_path, png_path, title_prefix=f"通道 {ch_idx+1} ({base_name})")
            if ok:
                exported_imgs.append(png_path)

        def _notify():
            for cfg in self.port_configs:
                if cfg["enable"].get():
                    cfg["status_lbl"].config(text="✔ 已落盘并完成波形导出", fg="#28A745")
            msg = f"已成功保存全部数据，并生成全程波形大图！\n\n存储文件夹：\n{self.save_dir}\n\n生成图片数：{len(exported_imgs)} 张"
            messagebox.showinfo("采集完成", msg)

        self.root.after(0, _notify)

if __name__ == "__main__":
    root = tk.Tk()
    app = AppV3(root)
    root.mainloop()
