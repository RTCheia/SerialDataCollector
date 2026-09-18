import os
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports

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

class SerialWorker:
    def __init__(self, port, baudrate, rules, output_file, update_cb):
        self.port = port
        self.baudrate = baudrate
        self.rules = rules
        self.output_file = output_file
        self.update_cb = update_cb
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

                    # 帧头同步：0xEB 0x90 (对应 37099)
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

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("多串口数据采集与自动转换工具")
        self.root.geometry("640x360")
        self.root.resizable(False, False)

        self.rules = load_fr_rules("fr.txt")
        self.workers = []
        self.is_collecting = False

        self._create_widgets()
        self._refresh_ports()

    def _create_widgets(self):
        top_frame = ttk.Frame(self.root, padding=10)
        top_frame.pack(fill="x")

        rule_count = len(self.rules)
        byte_sum = sum(r[1] for r in self.rules) if self.rules else 0
        ttk.Label(top_frame, text=f"配置文件 fr.txt：已加载 {rule_count} 条规则 (包长 {byte_sum} 字节)", foreground="green" if self.rules else "red").pack(side="left")
        ttk.Button(top_frame, text="刷新串口列表", command=self._refresh_ports).pack(side="right")

        # 串口配置区 (3个串口)
        self.port_configs = []
        cfg_box = ttk.LabelFrame(self.root, text="串口配置 (勾选启用的串口)", padding=10)
        cfg_box.pack(fill="x", padx=10, pady=5)

        baud_options = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]

        for i in range(3):
            row = ttk.Frame(cfg_box, padding=4)
            row.pack(fill="x")

            enable_var = tk.BooleanVar(value=True if i == 0 else False)
            chk = ttk.Checkbutton(row, text=f"串口 {i+1}", variable=enable_var)
            chk.pack(side="left", padx=5)

            ttk.Label(row, text="端口:").pack(side="left")
            port_cb = ttk.Combobox(row, width=12, state="readonly")
            port_cb.pack(side="left", padx=5)

            ttk.Label(row, text="波特率:").pack(side="left")
            baud_cb = ttk.Combobox(row, values=baud_options, width=10)
            baud_cb.set("230400")
            baud_cb.pack(side="left", padx=5)

            ttk.Label(row, text="保存文件:").pack(side="left")
            out_entry = ttk.Entry(row, width=16)
            out_entry.insert(0, f"data_ch{i+1}.txt")
            out_entry.pack(side="left", padx=5)

            status_lbl = ttk.Label(row, text="未连接", foreground="gray", width=22)
            status_lbl.pack(side="left", padx=5)

            self.port_configs.append({
                "enable": enable_var,
                "port_cb": port_cb,
                "baud_cb": baud_cb,
                "out_entry": out_entry,
                "status_lbl": status_lbl,
            })

        # 控制区
        ctrl_frame = ttk.Frame(self.root, padding=15)
        ctrl_frame.pack(fill="x")

        self.start_btn = tk.Button(ctrl_frame, text="▶ 开始采集并转换", bg="#4CAF50", fg="white", font=("Microsoft YaHei", 12, "bold"), width=16, height=2, command=self.start_collection)
        self.start_btn.pack(side="left", padx=20)

        self.stop_btn = tk.Button(ctrl_frame, text="■ 结束采集", bg="#f44336", fg="white", font=("Microsoft YaHei", 12, "bold"), width=14, height=2, state="disabled", command=self.stop_collection)
        self.stop_btn.pack(side="left", padx=10)

        self.msg_lbl = ttk.Label(self.root, text="就绪。勾选串口后点击【开始采集并转换】即可直接生成 data 文件。", foreground="#555")
        self.msg_lbl.pack(side="left", padx=15, pady=5)

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if not ports:
            ports = ["无可用串口"]
        for cfg in self.port_configs:
            cfg["port_cb"]["values"] = ports
            if ports and (not cfg["port_cb"].get() or cfg["port_cb"].get() not in ports):
                cfg["port_cb"].set(ports[0])

    def update_status(self, port, status_text, count):
        def _ui():
            for cfg in self.port_configs:
                if cfg["port_cb"].get() == port:
                    cfg["status_lbl"].config(text=f"{status_text} ({count}条)")
        self.root.after(0, _ui)

    def start_collection(self):
        if not self.rules:
            messagebox.showerror("错误", "未找到或未能加载 fr.txt 配置文件！")
            return

        enabled_cfgs = [c for c in self.port_configs if c["enable"].get()]
        if not enabled_cfgs:
            messagebox.showwarning("提醒", "请至少勾选一个启用的串口！")
            return

        selected_ports = [c["port_cb"].get() for c in enabled_cfgs]
        if "无可用串口" in selected_ports:
            messagebox.showwarning("提醒", "所选串口不可用，请确认串口已连接并点击刷新！")
            return
        if len(selected_ports) != len(set(selected_ports)):
            messagebox.showwarning("提醒", "选中的串口存在重复，请为不同通道分配不同串口！")
            return

        self.workers = []
        for cfg in enabled_cfgs:
            port = cfg["port_cb"].get()
            try:
                baud = int(cfg["baud_cb"].get())
            except ValueError:
                baud = 115200
            out_file = cfg["out_entry"].get().strip() or f"data_{port}.txt"

            worker = SerialWorker(port, baud, self.rules, out_file, self.update_status)
            self.workers.append(worker)

        for w in self.workers:
            w.start()

        self.is_collecting = True
        self.start_btn.config(state="disabled", bg="#cccccc")
        self.stop_btn.config(state="normal", bg="#f44336")
        self.msg_lbl.config(text=f"正在采集转换中... 共启用 {len(self.workers)} 个串口", foreground="blue")

    def stop_collection(self):
        for w in self.workers:
            w.stop()
        self.workers = []
        self.is_collecting = False
        self.start_btn.config(state="normal", bg="#4CAF50")
        self.stop_btn.config(state="disabled", bg="#cccccc")
        self.msg_lbl.config(text="采集已结束，所有已转换的 data 数据已直接保存为对应文件。", foreground="green")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
