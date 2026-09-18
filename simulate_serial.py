import time
import os
import sys
import serial
import serial.tools.list_ports

def send_simulated_stream(port_name, baudrate=230400, rate_hz=200, d_file="d.txt"):
    if not os.path.exists(d_file):
        print(f"错误: 找不到文件 {d_file}")
        return

    print(f"正在读取 {d_file} ...")
    with open(d_file, "r", encoding="utf-8", errors="ignore") as f:
        hex_text = f.read()

    # 过滤非16进制字符
    clean_hex = "".join([c for c in hex_text if c in "0123456789abcdefABCDEF"])
    if len(clean_hex) % 2 != 0:
        clean_hex = clean_hex[:-1]
    
    raw_data = bytes.fromhex(clean_hex)
    total_bytes = len(raw_data)
    print(f"解析成功，总字节数: {total_bytes} 字节")

    # 寻找帧头 0xEB 0x90 切割出有效帧 (每帧61字节)
    frame_len = 61
    frames = []
    idx = 0
    while idx <= total_bytes - frame_len:
        pos = raw_data.find(b"\xeb\x90", idx)
        if pos == -1:
            break
        if pos + frame_len <= total_bytes:
            frames.append(raw_data[pos : pos + frame_len])
            idx = pos + frame_len
        else:
            break

    print(f"共提取到完整数据帧: {len(frames)} 帧")
    if not frames:
        print("未提取到有效帧，请检查 d.txt！")
        return

    try:
        ser = serial.Serial(port_name, baudrate=baudrate, timeout=1.0)
        print(f"\n成功打开串口 [{port_name}]，波特率: {baudrate}")
    except Exception as e:
        print(f"打开串口 [{port_name}] 失败: {e}")
        return

    interval = 1.0 / rate_hz
    print(f"开始模拟发送... 设定频率: {rate_hz} Hz (每包间隔 {interval*1000:.2f} ms)")
    print("按 Ctrl+C 停止发送\n")

    frame_idx = 0
    total_sent = 0
    try:
        while True:
            t0 = time.perf_counter()
            frame = frames[frame_idx]
            ser.write(frame)
            total_sent += 1
            frame_idx = (frame_idx + 1) % len(frames) # 循环循环重放

            if total_sent % 100 == 0:
                print(f"\r已发送 {total_sent} 帧 (当前循环第 {frame_idx}/{len(frames)} 帧)...", end="", flush=True)

            # 精确时钟控制
            elapsed = time.perf_counter() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\n\n模拟发送已手动终止。")
    finally:
        ser.close()
        print(f"串口 [{port_name}] 已关闭，共发送 {total_sent} 帧。")

if __name__ == "__main__":
    ports = [p.device for p in serial.tools.list_ports.comports()]
    print("当前系统检测到的可用串口:", ports if ports else "暂无串口 (建议使用 VSPD 创建虚拟串口对)")

    default_port = ports[0] if ports else "COM2"
    target_port = input(f"请输入要发送的串口端口号 (默认 {default_port}): ").strip() or default_port
    
    hz_str = input("请输入模拟发送频率 Hz (默认 200 Hz, 即 5ms一帧): ").strip()
    try:
        hz = float(hz_str) if hz_str else 200.0
    except ValueError:
        hz = 200.0

    send_simulated_stream(target_port, baudrate=230400, rate_hz=hz, d_file="d.txt")
