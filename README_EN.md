# SerialDataCollector

Multi-channel High-frequency Serial Data Collector & Real-time Visualizer

[English Documentation](README_EN.md) | [中文说明](README.md) | [ Download Release EXE ](https://github.com/RTCheia/SerialDataCollector/releases)

---

## 🌟 Overview

**SerialDataCollector** is a high-performance, multi-channel serial data acquisition and real-time waveform visualization tool built with Python, PyQt5, and PyQtGraph. Designed for industrial and scientific testing environments, it features a hardware-decoupled multi-threaded pipeline that maintains smooth UI rendering and zero data loss under high sampling rates (100 Hz ~ 1000 Hz+) and continuous recording workloads.

---

## ✨ Key Features

- **Multi-channel Concurrent Acquisition**: Supports independent configuration of baud rate, port, and parity across multiple channels (3 channels by default), with auto-reconnection and disconnect detection.
- **Smooth Waveform Rendering**: Employs PyQtGraph and dual-buffered ring queues, capable of rendering tens of thousands of data points at high refresh rates without freezing the GUI.
- **Flexible Display Window**:
  - **Dynamic Buffer Window**: Easily adjust the visible sample window from `10` to `2000` points (default: 100 points).
  - **X-Axis Mode Toggle**: Seamlessly switch between **Frame Number (Frame)** and **Time in Seconds (s)**, with configurable calibration sampling frequency (default: 200 Hz).
- **Professional Data Export**:
  - Supports CSV, TXT, and raw binary formats.
  - Supports split per-channel logging or unified timestamp-aligned exports.
- **Robust Shutdown & Data Protection**:
  - Completely decouples acquisition workers from the UI thread. Catches abrupt window close events to guarantee full buffer flushing before exit.
- **Integrated Self-Test & Diagnostic Suite**:
  - Built-in 3-channel simulation source requiring no physical hardware.
  - Run `--self-test` (packet integrity, drop simulation, shutdown verification) or `--ui-smoke-test` (GUI rendering regression test).

---

## 🏗️ Architecture

```text
+-------------------+      +-------------------+      +-------------------+
| Channel 1 (COMx)  |      | Channel 2 (COMy)  |      | Channel 3 (COMz)  |
+---------+---------+      +---------+---------+      +---------+---------+
          |                          |                          |
          v                          v                          v
+---------+--------------------------+--------------------------+---------+
|                AcquisitionWorker (Multi-threaded Buffer Queue)          |
+------------------------------------+------------------------------------+
                                     |
              +----------------------+----------------------+
              |                                             |
              v                                             v
     +-----------------+                           +-----------------+
     |   Ring Buffer   |                           |  File Writer    |
     |  (Pre-render)   |                           |  (CSV / TXT)    |
     +--------+--------+                           +-----------------+
              |
              v
     +-----------------+
     | PyQtGraph Plot  |
     | (Downsampled)   |
     +-----------------+
```

---

## 📁 Directory Structure

```text
SerialDataCollector/
├── main.py             # Main entry point (CLI args, self-test dispatcher, Qt application)
├── ui.py               # Main window interface and plotting logic (PyQt5 + PyQtGraph)
├── acquisition.py      # Serial collection and background threads
├── protocol.py         # Packet parsing and protocol framing (header, CRC, timestamp)
├── export.py           # Logging and data persistence module (CSV / TXT)
├── selftest.py         # Automated test suite (simulation, error injection, shutdown check)
├── simulate_serial.py  # Standalone virtual serial port emulator
├── requirements.txt    # Python dependency specifications
├── LICENSE             # Open source license (MIT)
├── README.md           # Chinese documentation
└── README_EN.md        # English documentation
```

---

## 🚀 Quick Start

### 1. Download Pre-compiled Binary (Releases)
If you do not wish to set up Python, grab the latest standalone executable from **[GitHub Releases](https://github.com/RTCheia/SerialDataCollector/releases)**:
- File: `SerialDataCollector_V5_2.exe`
- Standalone portable executable; no Python installation required.

### 2. Run from Source
Ensure Python 3.9 ~ 3.12 is installed:

```bash
# 1. Clone repository
git clone https://github.com/RTCheia/SerialDataCollector.git
cd SerialDataCollector

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch application
python main.py
```

### 3. Self-Test & Diagnostic Verification
Verify application stability without any serial hardware:

```bash
# Comprehensive acquisition & teardown self-test
python main.py --self-test _temp/data/selftest_run

# GUI rendering and smoke test
python main.py --ui-smoke-test
```

---

## ⚙️ Configuration Parameters

| Parameter | Options / Range | Default | Description |
| :--- | :--- | :--- | :--- |
| **Window Size** | 10 ~ 2000 points | 100 points | Number of recent samples displayed on the waveform plot |
| **X-Axis Mode** | Frame / Seconds (s) | Frame | Switch X-axis units between raw frame indices and elapsed time |
| **Sampling Rate** | 1 ~ 2000 Hz | 200 Hz | Sampling frequency used to calculate elapsed seconds in time mode |
| **Baud Rate** | 9600 ~ 921600 | 115200 | Configurable per channel independently |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
