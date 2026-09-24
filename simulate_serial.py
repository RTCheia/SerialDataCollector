import os
import sys
import time
import serial
import serial.tools.list_ports

def send_simulated_stream_multi(port_names, baudrate=230400, rate_hz=200, d_file=r"C:\Users\RTC\Desktop\222\d.txt"):
    """
    支持同时打开 1~3 个串口，按 200 Hz (230400 波特率) 同步回放 d.txt 数据。
    """
    alt_paths = [d_file, r"C:\Users\RTC\Desktop\222\demo\d.txt", "d.txt"]
    target_d = None
    for p in alt_paths:
        if os.path.exists(p):
            target_d = p
            break

    if not target_d:
        print(f"错误: 找不到原始数据文件 d.txt (尝试路径: {alt_paths})")
        return

    print(f"正在读取原始数据文件: {target_d} ...")
    with open(target_d, "r", encoding="utf-8", errors="ignore") as f:
        clean_hex = "".join([c for c in f.read() if c in "0123456789abcdefABCDEF"])
    if len(clean_hex) % 2 != 0:
        clean_hex = clean_hex[:-1]
    
    raw_data = bytes.fromhex(clean_hex)
    print(f"解析成功，总字节数: {len(raw_data)} 字节 (~{len(raw_data)//61} 帧)")

    frame_len = 61
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

    print(f"共提取到完整数据帧: {len(frames)} 帧")
    if not frames:
        print("未提取到有效帧，请检查 d.txt！")
        return

    # 打开指定的所有串口
    opened_serials = []
    for port in port_names:
        p_clean = port.strip().upper()
        if not p_clean:
            continue
        try:
            ser = serial.Serial(p_clean, baudrate=baudrate, timeout=1.0)
            opened_serials.append(ser)
            print(f"  -> 成功打开串口 [{p_clean}]，波特率: {baudrate}")
        except Exception as e:
            print(f"  -> 打开串口 [{p_clean}] 失败: {e}")

    if not opened_serials:
        print("没有成功打开任何串口，退出发送。")
        return

    interval = 1.0 / rate_hz
    print(f"\n开始向 {len(opened_serials)} 个串口同步广播... 频率: {rate_hz} Hz (每帧 5.00 ms)")
    print("按 Ctrl+C 停止发送\n")

    frame_idx = 0
    total_sent = 0
    try:
        while True:
            t0 = time.perf_counter()
            frame = frames[frame_idx]
            for ser in opened_serials:
                try:
                    ser.write(frame)
                except Exception as e:
                    print(f"\n写入串口 {ser.port} 异常: {e}")

            total_sent += 1
            frame_idx = (frame_idx + 1) % len(frames)

            if total_sent % 100 == 0:
                print(f"\r已向 {len(opened_serials)} 路串口同步发送 {total_sent} 帧 (当前回放第 {frame_idx}/{len(frames)} 帧)...", end="", flush=True)

            elapsed = time.perf_counter() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\n\n模拟发送已手动终止。")
    finally:
        for ser in opened_serials:
            ser.close()
            print(f"串口 [{ser.port}] 已关闭。")
        print(f"总计完成发送: {total_sent} 帧。")

if __name__ == "__main__":
    ports = [p.device for p in serial.tools.list_ports.comports()]
    print("当前系统检测到的可用串口:", ports if ports else "暂无物理串口 (若需虚拟串口可用 VSPD 创建 COM2/COM4/COM6)")

    default_ports = "COM2, COM4, COM6" if not ports else ", ".join(ports[:3])
    user_input = input(f"\n请输入要同时发送的目标串口 (多个串口用逗号或空格隔开，默认: {default_ports}): ").strip()
    target_ports_str = user_input if user_input else default_ports
    
    # 分割端口
    import re
    port_list = [p for p in re.split(r"[,; \t]+", target_ports_str) if p]

    hz_str = input("请输入模拟发送频率 Hz (默认 200 Hz): ").strip()
    try:
        hz = float(hz_str) if hz_str else 200.0
    except ValueError:
        hz = 200.0

    send_simulated_stream_multi(port_list, baudrate=230400, rate_hz=hz)
