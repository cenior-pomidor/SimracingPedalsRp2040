#!/usr/bin/env python3
"""
SimPedal Configurator v1.1
Графический конфигуратор для тормозной педали SimPedal на базе RP2040 + ADS1220.

Возможности:
- Современный интерфейс в стиле гоночной телеметрии
- Двухканальный осциллограф: сравнение сырого (до фильтра) и очищенного сигнала в реальном времени
- Живой расчёт задержки (Latency, мс), времени установления и подавления шума
- 6 алгоритмов фильтрации (1-Euro, Баттерворт, Калман, Медианный, EMA, SMA) + анти-выброс Медиана ×3
- Пошаговая калибровка, настройка кривой отклика с живой точкой на графике и мёртвой зоны
- Встроенный режим симуляции тензодатчика 330 SPS для демонстрации и проверки фильтров без железа
"""

import json
import math
import queue
import random
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import serial
import serial.tools.list_ports

# =============================================================================
# Константы и палитра интерфейса (Motorsport Telemetry Dark Theme)
# =============================================================================

APP_TITLE = "SimPedal Configurator v1.1"
MIN_WIDTH = 1080
MIN_HEIGHT = 720

ADC_SAMPLE_RATE = 330.0
ADC_PERIOD_MS = 1000.0 / ADC_SAMPLE_RATE  # ~3.0303 мс
USB_HID_LATENCY_MS = 1.0                  # 1000 Гц опрос HID

# Цветовая палитра
C_BG_APP = "#0f131a"         # Глубокий графитово-синий фон окна
C_BG_CARD = "#161c26"        # Фон карточек/панелей
C_BG_ELEVATED = "#1f2735"    # Поля ввода, элементы управления, кнопки
C_BG_CANVAS = "#090c11"      # Глубокий чёрный фон графиков
C_BORDER = "#273142"         # Тонкие границы карточек и разделители
C_BORDER_LIGHT = "#36445b"

C_TEXT = "#edf2f7"           # Основной светлый текст
C_TEXT_MUTED = "#8b9bb4"     # Второстепенный текст
C_TEXT_DIM = "#5c6b82"       # Подписи осей и сетки

C_ACCENT = "#00e68a"         # Основной изумрудно-неоновый (выход фильтра)
C_ACCENT_DARK = "#0b3d2b"    # Тёмно-зелёный для подложки кнопок/бейджей
C_RAW = "#ff5c7c"            # Кораллово-красный (сырой сигнал до фильтра)
C_BLUE = "#3b82f6"           # Синий акцент (интерактивные кнопки)
C_BLUE_HOVER = "#2563eb"
C_YELLOW = "#fbbf24"         # Янтарный (пик / средняя задержка)
C_ORANGE = "#fb923c"         # Оранжевый (повышенная задержка)
C_RED = "#ef4444"            # Красный (ошибка / 100% упор / высокая задержка)
C_DEADZONE = "#47232c"       # Подсветка мёртвой зоны на шкалах

GRAPH_HISTORY_SECONDS = 5
GRAPH_UPDATE_MS = 40         # 25 FPS плавная отрисовка телеметрии
SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 0.1
PING_TIMEOUT = 1.0

DEVICE_VID = 0x1209
DEVICE_PID = 0xBEEF

# Метаданные фильтров (индексы строго совпадают с filter_type_t в прошивке)
FILTER_SPECS = [
    {
        "id": 0,
        "name": "Скользящее среднее (SMA)",
        "short": "SMA",
        "desc": "Равномерное усреднение последних N отсчётов. Простое базовое сглаживание белого шума, но при больших окнах заметно задерживает фронт нажатия.",
        "noise_type": "Высокочастотный равномерный шум",
    },
    {
        "id": 1,
        "name": "Экспоненциальный (EMA)",
        "short": "EMA",
        "desc": "Классический ФНЧ 1-го порядка. Более свежие измерения имеют больший вес — отклик плавнее и естественнее, чем у обычного скользящего среднего.",
        "noise_type": "Высокочастотный шум и наводки БП",
    },
    {
        "id": 2,
        "name": "Медианный (Anti-Spike)",
        "short": "Median",
        "desc": "Выбирает серединное значение в окне. Идеально вырезает резкие импульсные скачки (EMI от DD-руля, ESD), практически не размывая резкое нажатие тормоза.",
        "noise_type": "Импульсные выбросы и щелчки",
    },
    {
        "id": 3,
        "name": "Баттерворт 2-го порядка (LPF)",
        "short": "Butterworth",
        "desc": "Цифровой IIR биквад-фильтр со спадом −40 дБ/дек. Эффективно отсекает 50/60 Гц фон и вибрации при низкой задержке и максимально гладкой кривой.",
        "noise_type": "ВЧ-шум + сетевой фон 50/60 Гц",
    },
    {
        "id": 4,
        "name": "Фильтр Калмана 1D (Kalman)",
        "short": "Kalman",
        "desc": "Оценивает истинное усилие с учётом ковариации шума измерения. Отлично стабилизирует удержание постоянного давления тормоза (trail braking).",
        "noise_type": "Случайный гауссов шум АЦП",
    },
    {
        "id": 5,
        "name": "Адаптивный 1€ (One-Euro)",
        "short": "One-Euro",
        "desc": "Рекомендуется для симрейсинга: сильно сглаживает дрожание при удержании ноги, но мгновенно снижает задержку при резком ударе по педали тормоза.",
        "noise_type": "Оба режима: покой + быстрая реакция",
    },
]

FILTER_NAME_TO_ID = {f["name"]: f["id"] for f in FILTER_SPECS}
FILTER_ID_TO_SPEC = {f["id"]: f for f in FILTER_SPECS}


# =============================================================================
# Точная математика фильтров и расчёт задержки (синхронизировано с C-прошивкой)
# =============================================================================

def calculate_filter_latency(filter_type: int, size: int, spike_filter: bool) -> dict:
    """
    Рассчитывает характеристики фильтра и задержку (в мс) при Fs = 330 Гц.
    Формулы полностью соответствуют реализации в firmware/src/filter.c.
    """
    size = max(1, min(32, int(size)))
    spike_ms = ADC_PERIOD_MS if spike_filter else 0.0
    adc_base_ms = ADC_PERIOD_MS
    usb_ms = USB_HID_LATENCY_MS

    if size <= 1:
        main_ms = 0.0
        dynamic_ms = 0.0
        settle_95_ms = spike_ms
        param_text = "Основной фильтр отключён (Bypass)"
    elif filter_type == 0:  # SMA
        main_ms = ((size - 1) / 2.0) * ADC_PERIOD_MS
        dynamic_ms = main_ms
        settle_95_ms = (size - 1) * ADC_PERIOD_MS + spike_ms
        param_text = f"Окно усреднения: {size} сэмплов"
    elif filter_type == 1:  # EMA
        alpha = 2.0 / (size + 1.0)
        main_ms = ((1.0 - alpha) / alpha) * ADC_PERIOD_MS
        dynamic_ms = main_ms
        fc = -ADC_SAMPLE_RATE / (2.0 * math.pi) * math.log(1.0 - alpha)
        settle_95_ms = (math.log(0.05) / math.log(1.0 - alpha)) * ADC_PERIOD_MS + spike_ms
        param_text = f"α = {alpha:.3f} • Срез f_c ≈ {fc:.1f} Гц"
    elif filter_type == 2:  # Median
        win = min(15, 3 + 2 * ((size - 2) // 5))
        main_ms = ((win - 1) / 2.0) * ADC_PERIOD_MS
        dynamic_ms = main_ms
        settle_95_ms = main_ms + spike_ms
        param_text = f"Нечётное окно: {win} сэмплов"
    elif filter_type == 3:  # Butterworth 2nd order
        t = (size - 2) / 30.0
        fc = 80.0 * ((4.0 / 80.0) ** t)
        q = 0.7071067811865475
        main_ms = 1000.0 / (2.0 * math.pi * fc * q)
        dynamic_ms = main_ms
        settle_95_ms = (2.9 / (2.0 * math.pi * fc)) * 1000.0 + spike_ms
        param_text = f"Срез f_c = {fc:.1f} Гц (−40 дБ/дек)"
    elif filter_type == 4:  # Kalman 1D
        q = 1.0
        r = 0.08 * (size ** 2.2)
        k_ss = (-q + math.sqrt(q * q + 4.0 * q * r)) / (2.0 * r)
        main_ms = ((1.0 - k_ss) / k_ss) * ADC_PERIOD_MS
        dynamic_ms = main_ms
        settle_95_ms = (math.log(0.05) / math.log(1.0 - k_ss)) * ADC_PERIOD_MS + spike_ms
        param_text = f"Усиление K_ss = {k_ss:.3f} (R = {r:.1f})"
    else:  # One-Euro (5)
        t = (size - 2) / 30.0
        f_min = 25.0 * ((1.5 / 25.0) ** t)
        main_ms = 1000.0 / (2.0 * math.pi * f_min)           # В покое (удержание)
        dynamic_ms = 1000.0 / (2.0 * math.pi * 120.0)        # При резком нажатии (~1.3 мс)
        settle_95_ms = dynamic_ms * 3.0 + spike_ms
        param_text = f"f_min = {f_min:.1f} Гц (покой) → >120 Гц (удар)"

    filter_total_ms = main_ms + spike_ms
    filter_dynamic_ms = dynamic_ms + spike_ms
    pipeline_total_ms = filter_total_ms + adc_base_ms + usb_ms
    pipeline_dynamic_ms = filter_dynamic_ms + adc_base_ms + usb_ms

    # Уровень отзывчивости определяем по реальной динамической задержке нажатия
    rating_ms = filter_dynamic_ms
    if rating_ms <= 6.0:
        badge, color = "Мгновенный отклик", C_ACCENT
    elif rating_ms <= 16.0:
        badge, color = "Оптимальный баланс", C_ACCENT
    elif rating_ms <= 30.0:
        badge, color = "Умеренное сглаживание", C_YELLOW
    else:
        badge, color = "Заметная инерционность", C_ORANGE

    return {
        "is_adaptive": (filter_type == 5 and size > 1),
        "filter_ms": filter_total_ms,
        "filter_dynamic_ms": filter_dynamic_ms,
        "spike_ms": spike_ms,
        "pipeline_ms": pipeline_total_ms,
        "pipeline_dynamic_ms": pipeline_dynamic_ms,
        "settle_95_ms": settle_95_ms,
        "param_text": param_text,
        "badge": badge,
        "color": color,
    }


class PythonFilterMirror:
    """Точная копия фильтров прошивки на Python для встроенного демо-режима."""

    def __init__(self):
        self.type = 5
        self.size = 8
        self.spike_filter = True
        self.spike_buf = []
        self.buf = []
        self.initialized = False
        self.state_y = 0.0
        self.bq_z1 = 0.0
        self.bq_z2 = 0.0
        self.kalman_p = 1.0
        self.oe_prev_x = 0.0
        self.oe_dx = 0.0

    def configure(self, f_type: int, size: int, spike: bool):
        changed_buf = (f_type != self.type) or (size != self.size and f_type in (0, 2))
        self.type = f_type
        self.size = max(1, min(32, int(size)))
        self.spike_filter = bool(spike)
        if changed_buf:
            self.buf.clear()

    def update(self, raw_val: float, cal_span: float = 500000.0) -> float:
        x = raw_val
        if self.spike_filter:
            self.spike_buf.append(raw_val)
            if len(self.spike_buf) > 3:
                self.spike_buf.pop(0)
            if len(self.spike_buf) == 3:
                x = sorted(self.spike_buf)[1]

        if self.size <= 1:
            self.initialized = True
            self.state_y = x
            self.oe_prev_x = x
            return x

        if self.type == 0:  # SMA
            self.buf.append(x)
            if len(self.buf) > self.size:
                self.buf.pop(0)
            res = sum(self.buf) / len(self.buf)
        elif self.type == 1:  # EMA
            alpha = 2.0 / (self.size + 1.0)
            self.state_y = x if not self.initialized else (alpha * x + (1.0 - alpha) * self.state_y)
            res = self.state_y
        elif self.type == 2:  # Median
            win = min(15, 3 + 2 * ((self.size - 2) // 5))
            self.buf.append(x)
            if len(self.buf) > win:
                self.buf.pop(0)
            s = sorted(self.buf)
            res = s[len(s) // 2]
        elif self.type == 3:  # Butterworth
            t = (self.size - 2) / 30.0
            fc = 80.0 * ((4.0 / 80.0) ** t)
            w0 = 2.0 * math.pi * fc / ADC_SAMPLE_RATE
            cw, sw = math.cos(w0), math.sin(w0)
            alpha = sw / (2.0 * 0.7071067811865475)
            a0 = 1.0 + alpha
            b0 = ((1.0 - cw) * 0.5) / a0
            b1 = (1.0 - cw) / a0
            b2 = b0
            a1 = (-2.0 * cw) / a0
            a2 = (1.0 - alpha) / a0
            if not self.initialized:
                self.bq_z1 = x * (1.0 - b0)
                self.bq_z2 = x * (b2 - a2)
                res = x
            else:
                y = b0 * x + self.bq_z1
                self.bq_z1 = b1 * x - a1 * y + self.bq_z2
                self.bq_z2 = b2 * x - a2 * y
                res = y
        elif self.type == 4:  # Kalman
            q = 1.0
            r = 0.08 * (self.size ** 2.2)
            if not self.initialized:
                self.state_y = x
                self.kalman_p = r
            else:
                self.kalman_p += q
                k = self.kalman_p / (self.kalman_p + r)
                self.state_y = self.state_y + k * (x - self.state_y)
                self.kalman_p = (1.0 - k) * self.kalman_p
            res = self.state_y
        else:  # One-Euro
            t = (self.size - 2) / 30.0
            f_min = 25.0 * ((1.5 / 25.0) ** t)
            if not self.initialized:
                self.state_y = x
                self.oe_prev_x = x
                self.oe_dx = 0.0
            else:
                span = max(1000.0, cal_span)
                dx = ((x - self.oe_prev_x) * ADC_SAMPLE_RATE) / span
                self.oe_prev_x = x
                rd = 2.0 * math.pi * 1.0 / ADC_SAMPLE_RATE
                ad = rd / (rd + 1.0)
                self.oe_dx = ad * dx + (1.0 - ad) * self.oe_dx
                fc = min(160.0, f_min + 60.0 * abs(self.oe_dx))
                r_fc = 2.0 * math.pi * fc / ADC_SAMPLE_RATE
                a = r_fc / (r_fc + 1.0)
                self.state_y = a * x + (1.0 - a) * self.state_y
            res = self.state_y

        self.initialized = True
        self.state_y = res
        return res


def map_force(raw_val: float, cal_min: int, cal_max: int,
              deadzone: int, curve_type: int, inverted: bool) -> int:
    if cal_max == cal_min:
        return 0
    norm = (raw_val - cal_min) / float(cal_max - cal_min)
    if inverted:
        norm = 1.0 - norm
    norm = max(0.0, min(1.0, norm))
    dz = min(0.95, deadzone / 10000.0)
    if norm <= dz:
        return 0
    eff = max(0.0, min(1.0, (norm - dz) / (1.0 - dz)))
    if curve_type == 1:
        shaped = eff * eff
    elif curve_type == 2:
        shaped = math.sqrt(eff)
    else:
        shaped = eff
    return max(0, min(65535, int(round(shaped * 65535.0))))


# =============================================================================
# Управление последовательным портом + поддержка демо-симулятора
# =============================================================================

class PedalSerial:
    """Управление соединением с педалью по COM-порту или через демо-симулятор."""

    def __init__(self):
        self.port: serial.Serial | None = None
        self.connected = False
        self.demo_mode = False
        self.streaming = False
        self._read_thread: threading.Thread | None = None
        self._running = False
        self._data_queue: queue.Queue[tuple[int, int, int]] = queue.Queue()
        self._response_queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()

        # Для встроенного демо-режима
        self._demo_cfg = {
            "cal_min": 12000,
            "cal_max": 512000,
            "deadzone": 150,
            "filter_size": 8,
            "filter_type": 5,
            "spike_filter": 1,
            "curve_type": 1,
            "inverted": 0,
            "sample_rate": 330,
            "version": 2,
        }
        self._demo_filter = PythonFilterMirror()
        self._demo_t = 0.0

    @staticmethod
    def list_ports() -> list[str]:
        ports = serial.tools.list_ports.comports()
        return [p.device for p in sorted(ports)]

    @staticmethod
    def find_pedal_ports() -> list[str]:
        return [p.device for p in serial.tools.list_ports.comports()
                if p.vid == DEVICE_VID and p.pid == DEVICE_PID]

    def connect(self, port_name: str) -> bool:
        try:
            self.demo_mode = False
            self.port = serial.Serial(
                port=port_name,
                baudrate=SERIAL_BAUDRATE,
                timeout=SERIAL_TIMEOUT,
            )
            time.sleep(0.1)
            self.port.reset_input_buffer()
            self._running = True
            self._read_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._read_thread.start()

            resp = self.send_command("CMD:PING", timeout=PING_TIMEOUT)
            if resp and resp.startswith("RSP:PONG"):
                self.connected = True
                return True
            self.disconnect()
            return False
        except (serial.SerialException, OSError) as e:
            self.disconnect()
            raise ConnectionError(f"Ошибка подключения: {e}")

    def connect_demo(self):
        """Запускает программный симулятор 24-битного АЦП и тензодатчика."""
        self.disconnect()
        self.demo_mode = True
        self.connected = True
        self._running = True
        self._demo_filter = PythonFilterMirror()
        self._demo_filter.configure(
            self._demo_cfg["filter_type"],
            self._demo_cfg["filter_size"],
            bool(self._demo_cfg["spike_filter"]),
        )
        self._demo_t = 0.0
        self._read_thread = threading.Thread(target=self._demo_loop, daemon=True)
        self._read_thread.start()

    def disconnect(self):
        if self.streaming and not self.demo_mode:
            self.stop_stream()
        self._running = False
        self.connected = False
        self.demo_mode = False
        self.streaming = False
        if self._read_thread and self._read_thread.is_alive():
            self._read_thread.join(timeout=1.0)
        if self.port and self.port.is_open:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None
        while not self._data_queue.empty():
            try:
                self._data_queue.get_nowait()
            except queue.Empty:
                break

    def send_command(self, cmd: str, timeout: float = 1.0) -> str | None:
        if self.demo_mode:
            return self._handle_demo_command(cmd)
        if not self.port or not self.port.is_open:
            return None
        with self._lock:
            try:
                while not self._response_queue.empty():
                    self._response_queue.get_nowait()
                self.port.write((cmd + "\n").encode("utf-8"))
                self.port.flush()
            except (serial.SerialException, OSError):
                return None
        try:
            return self._response_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def send_raw(self, cmd: str):
        if self.demo_mode:
            self._handle_demo_command(cmd)
            return
        if not self.port or not self.port.is_open:
            return
        try:
            self.port.write((cmd + "\n").encode("utf-8"))
            self.port.flush()
        except (serial.SerialException, OSError):
            pass

    def start_stream(self):
        self.send_raw("CMD:STREAM_ON")
        self.streaming = True

    def stop_stream(self):
        self.send_raw("CMD:STREAM_OFF")
        self.streaming = False

    def get_data(self) -> list[tuple[int, int, int]]:
        """Возвращает [(filtered_raw, force, unfiltered_raw), ...]."""
        data = []
        while not self._data_queue.empty():
            try:
                data.append(self._data_queue.get_nowait())
            except queue.Empty:
                break
        return data

    def _reader_loop(self):
        buffer = ""
        while self._running:
            try:
                if self.port and self.port.is_open and self.port.in_waiting > 0:
                    raw = self.port.read(self.port.in_waiting)
                    buffer += raw.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("DAT:"):
                            self._parse_data_line(line)
                        elif line.startswith("RSP:"):
                            self._response_queue.put(line)
                else:
                    time.sleep(0.005)
            except (serial.SerialException, OSError):
                self._running = False
                self.connected = False
                break
            except Exception:
                time.sleep(0.01)

    def _parse_data_line(self, line: str):
        """Разбирает DAT:<filtered_raw>,<force>[,<unfiltered_raw>]."""
        try:
            parts = line[4:].split(",")
            if len(parts) >= 3:
                filt_val = int(parts[0])
                force_val = int(parts[1])
                raw_val = int(parts[2])
                self._data_queue.put((filt_val, force_val, raw_val))
            elif len(parts) == 2:
                filt_val = int(parts[0])
                force_val = int(parts[1])
                self._data_queue.put((filt_val, force_val, filt_val))
        except (ValueError, IndexError):
            pass

    def _handle_demo_command(self, cmd: str) -> str:
        c = self._demo_cfg
        if cmd == "CMD:PING":
            return "RSP:PONG:SimPedal:v1.1-DEMO"
        if cmd == "CMD:GET_CONFIG":
            return "RSP:CONFIG:" + json.dumps(c)
        if cmd == "CMD:LOAD" or cmd == "CMD:SAVE":
            return "RSP:OK:" + ("LOADED" if cmd == "CMD:LOAD" else "SAVED")
        if cmd.startswith("CMD:SET_DEADZONE:"):
            c["deadzone"] = max(0, min(10000, int(cmd.split(":")[-1])))
            return f"RSP:OK:DEADZONE:{c['deadzone']}"
        if cmd.startswith("CMD:SET_FILTER:"):
            c["filter_size"] = max(1, min(32, int(cmd.split(":")[-1])))
            self._demo_filter.configure(c["filter_type"], c["filter_size"], bool(c["spike_filter"]))
            return f"RSP:OK:FILTER:{c['filter_size']}"
        if cmd.startswith("CMD:SET_FILTER_TYPE:"):
            c["filter_type"] = max(0, min(5, int(cmd.split(":")[-1])))
            self._demo_filter.configure(c["filter_type"], c["filter_size"], bool(c["spike_filter"]))
            return f"RSP:OK:FILTER_TYPE:{c['filter_type']}"
        if cmd.startswith("CMD:SET_SPIKE_FILTER:"):
            c["spike_filter"] = 1 if int(cmd.split(":")[-1]) else 0
            self._demo_filter.configure(c["filter_type"], c["filter_size"], bool(c["spike_filter"]))
            return f"RSP:OK:SPIKE_FILTER:{c['spike_filter']}"
        if cmd.startswith("CMD:SET_CURVE:"):
            c["curve_type"] = max(0, min(2, int(cmd.split(":")[-1])))
            return f"RSP:OK:CURVE:{c['curve_type']}"
        if cmd.startswith("CMD:SET_INVERT:"):
            c["inverted"] = 1 if int(cmd.split(":")[-1]) else 0
            return f"RSP:OK:INVERT:{c['inverted']}"
        if cmd == "CMD:CAL_MIN":
            c["cal_min"] = int(round(self._demo_filter.state_y))
            return f"RSP:OK:CAL_MIN:{c['cal_min']}"
        if cmd == "CMD:CAL_MAX":
            c["cal_max"] = int(round(self._demo_filter.state_y))
            return f"RSP:OK:CAL_MAX:{c['cal_max']}"
        if cmd == "CMD:RESET_CAL":
            c["cal_min"] = 12000
            c["cal_max"] = 512000
            return "RSP:OK:RESET"
        return "RSP:OK"

    def _demo_loop(self):
        """Генерирует сигнал 330 Гц с имитацией торможения + шума + импульсных помех."""
        dt = 1.0 / ADC_SAMPLE_RATE
        step = 0
        while self._running:
            t_cycle = self._demo_t % 6.0
            # Профиль нажатия: резкий удар в тормоз + плавный трейл-брейкинг + отпускание
            if t_cycle < 1.2:
                base_norm = 0.0
            elif t_cycle < 1.45:
                base_norm = (t_cycle - 1.2) / 0.25 * 0.90
            elif t_cycle < 2.3:
                base_norm = 0.90 - (t_cycle - 1.45) * 0.20
            elif t_cycle < 3.6:
                base_norm = 0.73 - (t_cycle - 2.3) / 1.3 * 0.50
            elif t_cycle < 4.5:
                base_norm = 0.23  # Удержание слабого усилия — здесь виден 1-Euro фильтр!
            elif t_cycle < 4.9:
                base_norm = max(0.0, 0.23 * (1.0 - (t_cycle - 4.5) / 0.4))
            else:
                base_norm = 0.0

            c = self._demo_cfg
            span = max(1000, abs(c["cal_max"] - c["cal_min"]))
            clean = c["cal_min"] + base_norm * span

            # Реалистичный шум: белый шум + 50 Гц наводка + редкие ЭМИ-спайки
            white = random.gauss(0.0, span * 0.009)
            hum_50hz = math.sin(2.0 * math.pi * 50.0 * self._demo_t) * (span * 0.012)
            spike = (span * 0.14 * random.choice([-1, 1])) if random.random() < 0.012 else 0.0
            raw_val = clean + white + hum_50hz + spike

            filt_val = self._demo_filter.update(raw_val, span)
            force_val = map_force(
                filt_val, c["cal_min"], c["cal_max"],
                c["deadzone"], c["curve_type"], bool(c["inverted"])
            )

            # Отдаём телеметрию с частотой ~100 Гц (каждый 3-й сэмпл АЦП)
            if self.streaming and (step % 3 == 0):
                self._data_queue.put((int(round(filt_val)), force_val, int(round(raw_val))))

            self._demo_t += dt
            step += 1
            time.sleep(dt)


# =============================================================================
# Главное окно приложения
# =============================================================================

class PedalConfiguratorApp:
    """Главное окно конфигуратора тормозной педали."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.root.configure(bg=C_BG_APP)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.serial = PedalSerial()

        # История графика: [(filtered_pct, raw_pct), ...]
        self.max_graph_points = int(GRAPH_HISTORY_SECONDS * (1000 / GRAPH_UPDATE_MS))
        self.graph_filt: list[float] = []
        self.graph_raw: list[float] = []
        self.raw_noise_window: list[int] = []
        self.filt_noise_window: list[int] = []

        self.peak_force = 0.0
        self.current_norm_input = 0.0  # Для бегающей точки на кривой отклика

        self.config = {
            "cal_min": 0,
            "cal_max": 500000,
            "deadzone": 100,
            "filter_size": 8,
            "filter_type": 5,
            "spike_filter": 1,
            "curve_type": 0,
            "inverted": 0,
        }

        # Переменные tkinter
        self.var_port = tk.StringVar()
        self.var_deadzone = tk.IntVar(value=10)       # 0..1000 (0.00%..10.00%)
        self.var_filter_size = tk.IntVar(value=8)     # 1..32
        self.var_filter_name = tk.StringVar(value=FILTER_ID_TO_SPEC[5]["name"])
        self.var_spike_filter = tk.BooleanVar(value=True)
        self.var_curve = tk.IntVar(value=0)
        self.var_invert = tk.BooleanVar(value=False)
        self.var_show_raw = tk.BooleanVar(value=True)

        self._update_job = None
        self._apply_timer = None
        self._closing = False

        self.root.option_add("*TCombobox*Listbox.background", C_BG_ELEVATED)
        self.root.option_add("*TCombobox*Listbox.foreground", C_TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", C_BLUE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        self._setup_styles()
        self._build_ui()
        self._refresh_ports()
        self._update_latency_ui()
        self._draw_curve_preview()
        self._schedule_update()

    def _safe_ui(self, func):
        """Безопасно планирует выполнение callback в основном потоке Tkinter."""
        if self._closing:
            return
        try:
            self.root.after(0, lambda: (None if self._closing else func()))
        except (RuntimeError, tk.TclError):
            pass

    # -------------------------------------------------------------------------
    # Стилизация ttk (тёмная тема телеметрии)
    # -------------------------------------------------------------------------

    def _setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use("clam")

        self.style.configure(".", background=C_BG_CARD, foreground=C_TEXT,
                             fieldbackground=C_BG_ELEVATED, borderwidth=0,
                             font=("Segoe UI", 9))

        self.style.configure("App.TFrame", background=C_BG_APP)
        self.style.configure("Card.TFrame", background=C_BG_CARD, relief="flat")
        self.style.configure("SubCard.TFrame", background=C_BG_ELEVATED, relief="flat")
        self.style.configure("TFrame", background=C_BG_CARD)

        self.style.configure("TLabel", background=C_BG_CARD, foreground=C_TEXT)
        self.style.configure("Sub.TLabel", background=C_BG_ELEVATED, foreground=C_TEXT)
        self.style.configure("App.TLabel", background=C_BG_APP, foreground=C_TEXT)

        self.style.configure("CardTitle.TLabel", background=C_BG_CARD,
                             foreground=C_ACCENT, font=("Segoe UI", 10, "bold"))
        self.style.configure("Section.TLabel", background=C_BG_CARD,
                             foreground=C_TEXT, font=("Segoe UI", 9, "bold"))
        self.style.configure("Muted.TLabel", background=C_BG_CARD,
                             foreground=C_TEXT_MUTED, font=("Segoe UI", 8))
        self.style.configure("SubMuted.TLabel", background=C_BG_ELEVATED,
                             foreground=C_TEXT_MUTED, font=("Segoe UI", 8))

        self.style.configure("BigVal.TLabel", background=C_BG_CARD,
                             foreground=C_ACCENT, font=("Consolas", 26, "bold"))
        self.style.configure("Mono.TLabel", background=C_BG_CARD,
                             foreground=C_TEXT, font=("Consolas", 10, "bold"))
        self.style.configure("SubMono.TLabel", background=C_BG_ELEVATED,
                             foreground=C_ACCENT, font=("Consolas", 10, "bold"))

        # Кнопки
        self.style.configure("TButton", background=C_BG_ELEVATED, foreground=C_TEXT,
                             padding=(10, 6), font=("Segoe UI", 9, "bold"),
                             borderwidth=1, bordercolor=C_BORDER_LIGHT)
        self.style.map("TButton",
                       background=[("active", C_BORDER_LIGHT), ("disabled", "#121720")],
                       foreground=[("disabled", C_TEXT_DIM)])

        self.style.configure("Primary.TButton", background=C_BLUE, foreground="#ffffff",
                             padding=(12, 6), font=("Segoe UI", 9, "bold"),
                             borderwidth=0)
        self.style.map("Primary.TButton",
                       background=[("active", C_BLUE_HOVER), ("disabled", "#1a2942")],
                       foreground=[("disabled", C_TEXT_DIM)])

        self.style.configure("Connect.TButton", background="#0e7a4d", foreground="#ffffff",
                             padding=(12, 6), font=("Segoe UI", 9, "bold"), borderwidth=0)
        self.style.map("Connect.TButton", background=[("active", "#129c62")])

        self.style.configure("Disconnect.TButton", background="#992b2b", foreground="#ffffff",
                             padding=(12, 6), font=("Segoe UI", 9, "bold"), borderwidth=0)
        self.style.map("Disconnect.TButton", background=[("active", "#bd3535")])

        self.style.configure("Save.TButton", background="#186043", foreground=C_ACCENT,
                             padding=(10, 7), font=("Segoe UI", 9, "bold"), borderwidth=1,
                             bordercolor=C_ACCENT_DARK)
        self.style.map("Save.TButton",
                       background=[("active", "#207b56"), ("disabled", "#121720")],
                       foreground=[("disabled", C_TEXT_DIM)])

        self.style.configure("TCheckbutton", background=C_BG_CARD, foreground=C_TEXT, font=("Segoe UI", 9))
        self.style.map("TCheckbutton",
                       background=[("active", C_BG_CARD)],
                       foreground=[("disabled", C_TEXT_DIM)])

        self.style.configure("TRadiobutton", background=C_BG_CARD, foreground=C_TEXT, font=("Segoe UI", 9))
        self.style.map("TRadiobutton",
                       background=[("active", C_BG_CARD)],
                       foreground=[("disabled", C_TEXT_DIM)])

        self.style.configure("TScale", background=C_BG_CARD, troughcolor=C_BG_CANVAS,
                             sliderlength=16, sliderrelief="flat")
        self.style.configure("TCombobox", fieldbackground=C_BG_ELEVATED,
                             background=C_BG_ELEVATED, foreground=C_TEXT,
                             arrowcolor=C_TEXT, padding=4)
        self.style.map("TCombobox",
                       fieldbackground=[("readonly", C_BG_ELEVATED)],
                       foreground=[("readonly", C_TEXT), ("disabled", C_TEXT_DIM)])

        self.style.configure("TSeparator", background=C_BORDER)

    def _make_card(self, parent, title: str, badge: str = "") -> tuple[tk.Frame, tk.Frame]:
        """Создаёт аккуратную плоскую карточку с заголовком и внутренней областью."""
        outer = tk.Frame(parent, bg=C_BG_CARD, highlightbackground=C_BORDER,
                         highlightthickness=1, bd=0)
        header = tk.Frame(outer, bg=C_BG_CARD)
        header.pack(fill=tk.X, padx=12, pady=(10, 6))

        tk.Label(header, text=title, bg=C_BG_CARD, fg=C_ACCENT,
                 font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        if badge:
            tk.Label(header, text=badge, bg=C_BG_ELEVATED, fg=C_TEXT_MUTED,
                     font=("Consolas", 8), padx=6, pady=1).pack(side=tk.RIGHT)

        tk.Frame(outer, bg=C_BORDER, height=1).pack(fill=tk.X, padx=12)

        body = tk.Frame(outer, bg=C_BG_CARD)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        return outer, body

    # -------------------------------------------------------------------------
    # Построение интерфейса
    # -------------------------------------------------------------------------

    def _build_ui(self):
        self._build_header_bar()

        content = tk.Frame(self.root, bg=C_BG_APP)
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 12))
        content.columnconfigure(0, weight=0, minsize=255)  # Калибровка
        content.columnconfigure(1, weight=1, minsize=430)  # Реальное время
        content.columnconfigure(2, weight=0, minsize=340)  # Фильтры и настройки
        content.rowconfigure(0, weight=1)

        self._build_calibration_column(content)
        self._build_telemetry_column(content)
        self._build_settings_column(content)

    def _build_header_bar(self):
        bar = tk.Frame(self.root, bg=C_BG_CARD, highlightbackground=C_BORDER, highlightthickness=1)
        bar.pack(fill=tk.X, padx=12, pady=(10, 6))

        inner = tk.Frame(bar, bg=C_BG_CARD)
        inner.pack(fill=tk.X, padx=12, pady=8)

        # Лого и название
        tk.Label(inner, text="SIMPEDAL", bg=C_BG_CARD, fg=C_TEXT,
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        tk.Label(inner, text="BRAKE 24-BIT", bg=C_ACCENT_DARK, fg=C_ACCENT,
                 font=("Consolas", 8, "bold"), padx=7, pady=2).pack(side=tk.LEFT, padx=(6, 16))

        # Выбор COM порта
        tk.Label(inner, text="Порт:", bg=C_BG_CARD, fg=C_TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 5))

        self.combo_port = ttk.Combobox(inner, textvariable=self.var_port,
                                       state="readonly", width=11)
        self.combo_port.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_refresh = ttk.Button(inner, text="Обновить", command=self._refresh_ports)
        self.btn_refresh.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_connect = ttk.Button(inner, text="Подключить",
                                      command=self._toggle_connection,
                                      style="Connect.TButton")
        self.btn_connect.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_demo = ttk.Button(inner, text="Демо (симулятор)",
                                   command=self._toggle_demo)
        self.btn_demo.pack(side=tk.LEFT, padx=(0, 12))

        # Индикатор статуса
        self.status_canvas = tk.Canvas(inner, width=14, height=14,
                                       bg=C_BG_CARD, highlightthickness=0)
        self.status_canvas.pack(side=tk.LEFT, padx=(0, 6))
        self.status_indicator = self.status_canvas.create_oval(
            2, 2, 12, 12, fill=C_RED, outline=C_BORDER_LIGHT
        )

        self.lbl_status = tk.Label(inner, text="Отключено", bg=C_BG_CARD,
                                   fg=C_TEXT_MUTED, font=("Segoe UI", 9, "bold"))
        self.lbl_status.pack(side=tk.LEFT)

        tk.Label(inner, text="330 SPS АЦП • 1000 Гц HID", bg=C_BG_CARD,
                 fg=C_TEXT_DIM, font=("Consolas", 8)).pack(side=tk.RIGHT)

    # --- Левая колонка: Калибровка и статистика шума ---

    def _build_calibration_column(self, parent):
        card, body = self._make_card(parent, "КАЛИБРОВКА", "FLASH")
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # Карточка текущих пределов АЦП
        vals_box = tk.Frame(body, bg=C_BG_ELEVATED, padx=10, pady=8,
                            highlightbackground=C_BORDER, highlightthickness=1)
        vals_box.pack(fill=tk.X, pady=(0, 12))

        for row_idx, (label, attr) in enumerate([
            ("Ноль (0%):", "lbl_cal_min"),
            ("Макс (100%):", "lbl_cal_max"),
            ("Размах АЦП:", "lbl_cal_span"),
        ]):
            tk.Label(vals_box, text=label, bg=C_BG_ELEVATED, fg=C_TEXT_MUTED,
                     font=("Segoe UI", 8)).grid(row=row_idx, column=0, sticky="w", pady=2)
            lbl = tk.Label(vals_box, text="---", bg=C_BG_ELEVATED, fg=C_TEXT,
                           font=("Consolas", 10, "bold"))
            lbl.grid(row=row_idx, column=1, sticky="e", padx=(8, 0), pady=2)
            setattr(self, attr, lbl)
        vals_box.columnconfigure(1, weight=1)

        # Пошаговый мастер
        tk.Label(body, text="Пошаговый мастер:", bg=C_BG_CARD, fg=C_TEXT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        steps = [
            ("1. Отпустите педаль полностью", "Задать ноль (0%)", self._calibrate_min, "btn_cal_min"),
            ("2. Нажмите с рабочим макс. усилием", "Задать упор (100%)", self._calibrate_max, "btn_cal_max"),
        ]
        for hint, btn_text, cmd, attr in steps:
            tk.Label(body, text=hint, bg=C_BG_CARD, fg=C_TEXT_MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 2))
            btn = ttk.Button(body, text=btn_text, command=cmd, state=tk.DISABLED)
            btn.pack(fill=tk.X, pady=(0, 4))
            setattr(self, attr, btn)

        self.btn_cal_reset = ttk.Button(body, text="Сброс калибровки",
                                        command=self._reset_calibration, state=tk.DISABLED)
        self.btn_cal_reset.pack(fill=tk.X, pady=(4, 12))

        tk.Frame(body, bg=C_BORDER, height=1).pack(fill=tk.X, pady=(0, 10))

        # Карточка анализатора шума
        tk.Label(body, text="Анализатор шума сигнала:", bg=C_BG_CARD, fg=C_TEXT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))

        noise_box = tk.Frame(body, bg=C_BG_ELEVATED, padx=10, pady=8,
                             highlightbackground=C_BORDER, highlightthickness=1)
        noise_box.pack(fill=tk.X)

        for r, (cap, attr, col) in enumerate([
            ("До фильтра (p-p):", "lbl_noise_raw", C_RAW),
            ("После фильтра (p-p):", "lbl_noise_filt", C_ACCENT),
            ("Очистка шума:", "lbl_noise_gain", C_YELLOW),
        ]):
            tk.Label(noise_box, text=cap, bg=C_BG_ELEVATED, fg=C_TEXT_MUTED,
                     font=("Segoe UI", 8)).grid(row=r, column=0, sticky="w", pady=2)
            lbl = tk.Label(noise_box, text="---", bg=C_BG_ELEVATED, fg=col,
                           font=("Consolas", 9, "bold"))
            lbl.grid(row=r, column=1, sticky="e", padx=(6, 0), pady=2)
            setattr(self, attr, lbl)
        noise_box.columnconfigure(1, weight=1)

        tk.Label(body, text="Оценка разброса за последние 40 отсчётов",
                 bg=C_BG_CARD, fg=C_TEXT_DIM, font=("Segoe UI", 7)).pack(anchor="w", pady=(4, 0))

        # Кнопки памяти внизу левой колонки для симметрии
        mem_frame = tk.Frame(body, bg=C_BG_CARD)
        mem_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self.btn_save = ttk.Button(mem_frame, text="Сохранить в Flash педали",
                                   command=self._save_config, state=tk.DISABLED,
                                   style="Save.TButton")
        self.btn_save.pack(fill=tk.X, pady=(0, 6))
        self.btn_load = ttk.Button(mem_frame, text="Перезагрузить из Flash",
                                   command=self._load_config, state=tk.DISABLED)
        self.btn_load.pack(fill=tk.X)

    # --- Центральная колонка: Шкала усилия + 2-канальный осциллограф + Блок задержки ---

    def _build_telemetry_column(self, parent):
        card, body = self._make_card(parent, "ТЕЛЕМЕТРИЯ РЕАЛЬНОГО ВРЕМЕНИ", "100 Hz STREAM")
        card.grid(row=0, column=1, sticky="nsew", padx=4)

        top = tk.Frame(body, bg=C_BG_CARD)
        top.pack(fill=tk.BOTH, expand=True)

        # Вертикальный измеритель силы торможения
        meter_col = tk.Frame(top, bg=C_BG_CARD, width=118)
        meter_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        meter_col.pack_propagate(False)

        self.lbl_percent = tk.Label(meter_col, text="0.0%", bg=C_BG_CARD, fg=C_ACCENT,
                                    font=("Consolas", 20, "bold"))
        self.lbl_percent.pack(pady=(0, 2))

        self.lbl_hid = tk.Label(meter_col, text="HID: 0", bg=C_BG_CARD, fg=C_TEXT_MUTED,
                                font=("Consolas", 8))
        self.lbl_hid.pack(pady=(0, 6))

        self.brake_canvas = tk.Canvas(meter_col, width=76, height=280, bg=C_BG_CANVAS,
                                      highlightthickness=1, highlightbackground=C_BORDER)
        self.brake_canvas.pack(fill=tk.Y, expand=True)

        self.lbl_raw = tk.Label(meter_col, text="АЦП: 0", bg=C_BG_CARD, fg=C_TEXT_MUTED,
                                font=("Consolas", 8))
        self.lbl_raw.pack(pady=(6, 0))

        self.lbl_peak = tk.Label(meter_col, text="Пик: 0.0%", bg=C_BG_CARD, fg=C_YELLOW,
                                 font=("Consolas", 9, "bold"))
        self.lbl_peak.pack(pady=(2, 4))

        self.btn_reset_peak = ttk.Button(meter_col, text="Сброс пика",
                                         command=self._reset_peak, state=tk.DISABLED)
        self.btn_reset_peak.pack(fill=tk.X)

        # Осциллограф
        graph_col = tk.Frame(top, bg=C_BG_CARD)
        graph_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        legend = tk.Frame(graph_col, bg=C_BG_CARD)
        legend.pack(fill=tk.X, pady=(0, 6))

        tk.Label(legend, text="Осциллограмма (5 сек)", bg=C_BG_CARD, fg=C_TEXT,
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        tk.Checkbutton(
            legend, text="Сырой АЦП", variable=self.var_show_raw,
            bg=C_BG_CARD, fg=C_RAW, selectcolor=C_BG_ELEVATED,
            activebackground=C_BG_CARD, activeforeground=C_RAW,
            font=("Segoe UI", 8, "bold"), bd=0, highlightthickness=0
        ).pack(side=tk.RIGHT, padx=(8, 0))

        tk.Label(legend, text="● После фильтра", bg=C_BG_CARD, fg=C_ACCENT,
                 font=("Segoe UI", 8, "bold")).pack(side=tk.RIGHT)

        self.graph_canvas = tk.Canvas(graph_col, bg=C_BG_CANVAS,
                                      highlightthickness=1, highlightbackground=C_BORDER)
        self.graph_canvas.pack(fill=tk.BOTH, expand=True)

        # Нижняя карточка оценки задержки тракта
        lat_card = tk.Frame(body, bg=C_BG_ELEVATED, padx=12, pady=10,
                            highlightbackground=C_BORDER, highlightthickness=1)
        lat_card.pack(fill=tk.X, pady=(12, 0))

        lat_top = tk.Frame(lat_card, bg=C_BG_ELEVATED)
        lat_top.pack(fill=tk.X)

        tk.Label(lat_top, text="ЗАДЕРЖКА ФИЛЬТРАЦИИ", bg=C_BG_ELEVATED,
                 fg=C_TEXT_MUTED, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        self.lbl_latency_badge = tk.Label(lat_top, text="Оптимальный баланс",
                                          bg=C_ACCENT_DARK, fg=C_ACCENT,
                                          font=("Segoe UI", 8, "bold"), padx=7, pady=1)
        self.lbl_latency_badge.pack(side=tk.RIGHT)

        lat_vals = tk.Frame(lat_card, bg=C_BG_ELEVATED)
        lat_vals.pack(fill=tk.X, pady=(6, 4))

        # Крупное значение задержки фильтра
        f_lat_blk = tk.Frame(lat_vals, bg=C_BG_ELEVATED)
        f_lat_blk.pack(side=tk.LEFT, padx=(0, 20))
        self.lbl_filter_latency = tk.Label(f_lat_blk, text="0.0 мс", bg=C_BG_ELEVATED,
                                           fg=C_ACCENT, font=("Consolas", 18, "bold"))
        self.lbl_filter_latency.pack(anchor="w")
        self.lbl_filter_latency_sub = tk.Label(f_lat_blk, text="Добавленная фильтром",
                                               bg=C_BG_ELEVATED, fg=C_TEXT_MUTED,
                                               font=("Segoe UI", 8))
        self.lbl_filter_latency_sub.pack(anchor="w")

        # Суммарная задержка + время установления
        details_blk = tk.Frame(lat_vals, bg=C_BG_ELEVATED)
        details_blk.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.lbl_total_latency = tk.Label(details_blk, text="Полный тракт (АЦП + Фильтр + USB): ---",
                                          bg=C_BG_ELEVATED, fg=C_TEXT, font=("Consolas", 9))
        self.lbl_total_latency.pack(anchor="w")
        self.lbl_settle_time = tk.Label(details_blk, text="Время установления 95%: ---",
                                        bg=C_BG_ELEVATED, fg=C_TEXT_MUTED, font=("Consolas", 9))
        self.lbl_settle_time.pack(anchor="w", pady=(2, 0))

        # Полоска шкалы задержки
        self.latency_bar = tk.Canvas(lat_card, height=8, bg=C_BG_CANVAS,
                                     highlightthickness=0)
        self.latency_bar.pack(fill=tk.X, pady=(4, 0))

    # --- Правая колонка: Фильтры, Мёртвая зона, Кривая отклика ---

    def _build_settings_column(self, parent):
        card, body = self._make_card(parent, "ФИЛЬТРЫ И ОТКЛИК", "DSP")
        card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

        # Выбор алгоритма фильтрации
        tk.Label(body, text="Алгоритм очистки шума:", bg=C_BG_CARD, fg=C_TEXT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self.combo_filter = ttk.Combobox(
            body,
            textvariable=self.var_filter_name,
            values=[f["name"] for f in FILTER_SPECS],
            state="disabled",
        )
        self.combo_filter.pack(fill=tk.X, pady=(4, 6))
        self.combo_filter.bind("<<ComboboxSelected>>", self._on_filter_type_changed)

        # Карточка-подсказка по выбранному фильтру
        self.lbl_filter_desc = tk.Label(
            body, text="", bg=C_BG_ELEVATED, fg=C_TEXT_MUTED,
            font=("Segoe UI", 8), justify="left", wraplength=305,
            padx=8, pady=6, anchor="w"
        )
        self.lbl_filter_desc.pack(fill=tk.X, pady=(0, 8))

        # Анти-глитч пре-фильтр
        self.chk_spike = ttk.Checkbutton(
            body, text="Пре-фильтр Анти-выброс (Медиана ×3, +3.0 мс)",
            variable=self.var_spike_filter, command=self._on_spike_changed
        )
        self.chk_spike.pack(anchor="w", pady=(0, 8))
        self.chk_spike.state(["disabled"])

        # Сила фильтрации
        flt_hdr = tk.Frame(body, bg=C_BG_CARD)
        flt_hdr.pack(fill=tk.X)
        tk.Label(flt_hdr, text="Интенсивность фильтра:", bg=C_BG_CARD, fg=C_TEXT,
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.lbl_filter_val = tk.Label(flt_hdr, text="8 / 32", bg=C_BG_CARD, fg=C_ACCENT,
                                       font=("Consolas", 9, "bold"))
        self.lbl_filter_val.pack(side=tk.RIGHT)

        self.scale_filter = ttk.Scale(
            body, from_=1, to=32, orient=tk.HORIZONTAL,
            variable=self.var_filter_size, command=self._on_filter_size_changed
        )
        self.scale_filter.pack(fill=tk.X, pady=(2, 2))
        self.scale_filter.state(["disabled"])

        self.lbl_filter_param = tk.Label(body, text="", bg=C_BG_CARD, fg=C_TEXT_MUTED,
                                         font=("Consolas", 8))
        self.lbl_filter_param.pack(anchor="w", pady=(0, 8))

        tk.Frame(body, bg=C_BORDER, height=1).pack(fill=tk.X, pady=6)

        # Мёртвая зона
        dz_hdr = tk.Frame(body, bg=C_BG_CARD)
        dz_hdr.pack(fill=tk.X)
        tk.Label(dz_hdr, text="Начальная мёртвая зона:", bg=C_BG_CARD, fg=C_TEXT,
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.lbl_deadzone_val = tk.Label(dz_hdr, text="1.00%", bg=C_BG_CARD, fg=C_YELLOW,
                                         font=("Consolas", 9, "bold"))
        self.lbl_deadzone_val.pack(side=tk.RIGHT)

        self.scale_deadzone = ttk.Scale(
            body, from_=0, to=1000, orient=tk.HORIZONTAL,
            variable=self.var_deadzone, command=self._on_deadzone_changed
        )
        self.scale_deadzone.pack(fill=tk.X, pady=(2, 8))
        self.scale_deadzone.state(["disabled"])

        # Кривая отклика и предпросмотр рядом
        curve_row = tk.Frame(body, bg=C_BG_CARD)
        curve_row.pack(fill=tk.X, pady=(2, 8))

        rb_col = tk.Frame(curve_row, bg=C_BG_CARD)
        rb_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))

        tk.Label(rb_col, text="Кривая отклика:", bg=C_BG_CARD, fg=C_TEXT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

        self.rb_linear = ttk.Radiobutton(rb_col, text="Линейная", variable=self.var_curve,
                                         value=0, command=self._on_curve_changed)
        self.rb_progressive = ttk.Radiobutton(rb_col, text="Прогрессивная", variable=self.var_curve,
                                              value=1, command=self._on_curve_changed)
        self.rb_degressive = ttk.Radiobutton(rb_col, text="Дегрессивная", variable=self.var_curve,
                                             value=2, command=self._on_curve_changed)
        for rb in (self.rb_linear, self.rb_progressive, self.rb_degressive):
            rb.pack(anchor="w", pady=1)
            rb.state(["disabled"])

        self.chk_invert = ttk.Checkbutton(rb_col, text="Инверсия оси",
                                          variable=self.var_invert,
                                          command=self._on_invert_changed)
        self.chk_invert.pack(anchor="w", pady=(8, 0))
        self.chk_invert.state(["disabled"])

        self.curve_canvas = tk.Canvas(curve_row, width=160, height=130, bg=C_BG_CANVAS,
                                      highlightthickness=1, highlightbackground=C_BORDER)
        self.curve_canvas.pack(side=tk.RIGHT)

        # Кнопка Применить
        self.btn_apply = ttk.Button(
            body, text="Применить в ОЗУ педали", command=self._apply_settings,
            state=tk.DISABLED, style="Primary.TButton"
        )
        self.btn_apply.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

    # -------------------------------------------------------------------------
    # Логика подключения и демо
    # -------------------------------------------------------------------------

    def _refresh_ports(self):
        ports = PedalSerial.list_ports()
        self.combo_port["values"] = ports
        pedal_ports = PedalSerial.find_pedal_ports()
        if pedal_ports:
            self.var_port.set(pedal_ports[0])
        elif ports:
            self.var_port.set(ports[0])
        else:
            self.var_port.set("")

    def _toggle_connection(self):
        if self.serial.connected and not self.serial.demo_mode:
            self._disconnect()
        else:
            self._connect()

    def _toggle_demo(self):
        if self.serial.connected and self.serial.demo_mode:
            self._disconnect()
        else:
            self.serial.connect_demo()
            self._set_connected_state(True, is_demo=True)
            self._load_config()
            self.serial.start_stream()

    def _connect(self):
        port = self.var_port.get()
        if not port:
            messagebox.showwarning("Внимание", "Выберите COM-порт устройства или запустите Демо.")
            return

        self.btn_connect.configure(state=tk.DISABLED)
        self.lbl_status.configure(text="Подключение...", fg=C_YELLOW)

        def task():
            try:
                ok = self.serial.connect(port)
                self._safe_ui(lambda: self._on_connect_result(ok))
            except ConnectionError as e:
                msg = str(e)
                self._safe_ui(lambda: self._on_connect_error(msg))

        threading.Thread(target=task, daemon=True).start()

    def _on_connect_result(self, success: bool):
        if success:
            self._set_connected_state(True, is_demo=False)
            self._load_config()
            self.serial.start_stream()
        else:
            self._set_connected_state(False)
            messagebox.showerror("Ошибка", "Устройство не отвечает на CMD:PING.\nПроверьте прошивку RP2040.")

    def _on_connect_error(self, error: str):
        self._set_connected_state(False)
        messagebox.showerror("Ошибка подключения", error)

    def _disconnect(self):
        self.serial.disconnect()
        self._set_connected_state(False)
        self.graph_filt.clear()
        self.graph_raw.clear()
        self.raw_noise_window.clear()
        self.filt_noise_window.clear()
        self.peak_force = 0.0
        self.current_norm_input = 0.0
        self.lbl_percent.configure(text="0.0%")
        self.lbl_hid.configure(text="HID: 0")
        self.lbl_raw.configure(text="АЦП: 0")
        self.lbl_peak.configure(text="Пик: 0.0%")
        self.lbl_noise_raw.configure(text="---")
        self.lbl_noise_filt.configure(text="---")
        self.lbl_noise_gain.configure(text="---")
        self._update_brake_bar(0.0)
        self._update_oscilloscope()
        self._draw_curve_preview()

    def _set_connected_state(self, connected: bool, is_demo: bool = False):
        if connected:
            self.status_canvas.itemconfigure(self.status_indicator, fill=C_YELLOW if is_demo else C_ACCENT)
            self.lbl_status.configure(
                text="Демо-симулятор (330 SPS)" if is_demo else "Подключено (SimPedal)",
                fg=C_YELLOW if is_demo else C_ACCENT
            )
            self.btn_connect.configure(
                text="Отключить" if not is_demo else "Подключить",
                style="Disconnect.TButton" if not is_demo else "Connect.TButton",
            )
            self.btn_demo.configure(text="Стоп Демо" if is_demo else "Демо (симулятор)")
            state = tk.NORMAL
        else:
            self.status_canvas.itemconfigure(self.status_indicator, fill=C_RED)
            self.lbl_status.configure(text="Отключено", fg=C_TEXT_MUTED)
            self.btn_connect.configure(text="Подключить", style="Connect.TButton")
            self.btn_demo.configure(text="Демо (симулятор)")
            state = tk.DISABLED

        self.btn_connect.configure(state=tk.NORMAL)

        for btn in (self.btn_cal_min, self.btn_cal_max, self.btn_cal_reset,
                    self.btn_apply, self.btn_save, self.btn_load, self.btn_reset_peak):
            btn.configure(state=state)

        self.combo_filter.configure(state="readonly" if connected else "disabled")
        w_state = ["!disabled"] if connected else ["disabled"]
        for w in (self.scale_deadzone, self.scale_filter, self.rb_linear,
                  self.rb_progressive, self.rb_degressive, self.chk_invert, self.chk_spike):
            w.state(w_state)

    # -------------------------------------------------------------------------
    # Калибровка и настройки
    # -------------------------------------------------------------------------

    def _calibrate_min(self):
        def task():
            resp = self.serial.send_command("CMD:CAL_MIN")
            self._safe_ui(lambda: self._on_cal_result(resp, "min"))
        threading.Thread(target=task, daemon=True).start()

    def _calibrate_max(self):
        def task():
            resp = self.serial.send_command("CMD:CAL_MAX")
            self._safe_ui(lambda: self._on_cal_result(resp, "max"))
        threading.Thread(target=task, daemon=True).start()

    def _on_cal_result(self, resp: str | None, which: str):
        prefix = "RSP:OK:CAL_MIN:" if which == "min" else "RSP:OK:CAL_MAX:"
        if resp and resp.startswith(prefix):
            val = int(resp.split(":")[-1])
            self.config[f"cal_{which}"] = val
            self._update_cal_labels()
        else:
            messagebox.showerror("Ошибка", "Не удалось выполнить калибровку.")

    def _reset_calibration(self):
        def task():
            resp = self.serial.send_command("CMD:RESET_CAL")
            self._safe_ui(lambda: self._on_reset_cal_result(resp))
        threading.Thread(target=task, daemon=True).start()

    def _on_reset_cal_result(self, resp: str | None):
        if resp and "RSP:OK:RESET" in resp:
            self._load_config()
        else:
            messagebox.showerror("Ошибка", "Не удалось сбросить калибровку.")

    def _update_cal_labels(self):
        cmin = self.config.get("cal_min", 0)
        cmax = self.config.get("cal_max", 0)
        span = abs(cmax - cmin)
        self.lbl_cal_min.configure(text=f"{cmin:,}".replace(",", " "))
        self.lbl_cal_max.configure(text=f"{cmax:,}".replace(",", " "))
        self.lbl_cal_span.configure(text=f"{span:,}".replace(",", " "))

    def _on_deadzone_changed(self, value):
        v = int(float(value))
        self.lbl_deadzone_val.configure(text=f"{v / 100:.2f}%")
        self._draw_curve_preview()

    def _on_filter_type_changed(self, _event=None):
        self._update_latency_ui()
        if self.serial.demo_mode:
            self._apply_settings()

    def _on_spike_changed(self):
        self._update_latency_ui()
        if self.serial.demo_mode:
            self._apply_settings()

    def _on_filter_size_changed(self, value):
        v = int(round(float(value)))
        self.lbl_filter_val.configure(text=f"{v} / 32")
        self._update_latency_ui()
        if self.serial.demo_mode:
            self._apply_settings()

    def _on_curve_changed(self):
        self._draw_curve_preview()
        if self.serial.demo_mode:
            self._apply_settings()

    def _on_invert_changed(self):
        if self.serial.demo_mode:
            self._apply_settings()

    def _update_latency_ui(self):
        """Пересчитывает и отображает оценку задержки выбранного фильтра."""
        f_id = FILTER_NAME_TO_ID.get(self.var_filter_name.get(), 5)
        f_size = max(1, min(32, int(round(float(self.var_filter_size.get())))))
        spike = bool(self.var_spike_filter.get())

        spec = FILTER_ID_TO_SPEC[f_id]
        self.lbl_filter_desc.configure(
            text=f"{spec['desc']}\nПодавляет: {spec['noise_type']}."
        )

        lat = calculate_filter_latency(f_id, f_size, spike)
        self.lbl_filter_param.configure(text=lat["param_text"])
        self.lbl_latency_badge.configure(text=lat["badge"], fg=lat["color"])

        if lat["is_adaptive"]:
            self.lbl_filter_latency.configure(
                text=f"{lat['filter_dynamic_ms']:.1f}–{lat['filter_ms']:.1f} мс",
                fg=lat["color"],
            )
            self.lbl_filter_latency_sub.configure(
                text="При ударе по педали → При удержании"
            )
            self.lbl_total_latency.configure(
                text=f"Полный тракт: {lat['pipeline_dynamic_ms']:.1f} мс (удар) / {lat['pipeline_ms']:.1f} мс (покой)"
            )
        else:
            self.lbl_filter_latency.configure(
                text=f"{lat['filter_ms']:.1f} мс",
                fg=lat["color"],
            )
            self.lbl_filter_latency_sub.configure(
                text=f"Фильтр (вкл. анти-выброс +{lat['spike_ms']:.1f} мс)" if spike else "Чистая задержка фильтра"
            )
            self.lbl_total_latency.configure(
                text=f"Полный тракт (АЦП 3.0 + Фильтр + USB 1.0): {lat['pipeline_ms']:.1f} мс"
            )

        self.lbl_settle_time.configure(text=f"Время установления 95%: {lat['settle_95_ms']:.1f} мс")

        # Отрисовка шкалы задержки (0 .. 55 мс)
        self.latency_bar.update_idletasks()
        bw = self.latency_bar.winfo_width() or 300
        self.latency_bar.delete("all")
        pct = min(1.0, lat["filter_dynamic_ms"] / 55.0)
        fill_w = max(4, int(bw * pct))
        self.latency_bar.create_rectangle(0, 0, bw, 8, fill=C_BG_CANVAS, outline="")
        self.latency_bar.create_rectangle(0, 0, fill_w, 8, fill=lat["color"], outline="")

    def _apply_settings(self):
        if not self.serial.connected:
            return

        dz = int(self.var_deadzone.get()) * 10
        f_size = max(1, min(32, int(round(float(self.var_filter_size.get())))))
        f_type = FILTER_NAME_TO_ID.get(self.var_filter_name.get(), 5)
        spike = 1 if self.var_spike_filter.get() else 0
        curve = self.var_curve.get()
        inv = 1 if self.var_invert.get() else 0

        self.config.update({
            "deadzone": dz,
            "filter_size": f_size,
            "filter_type": f_type,
            "spike_filter": spike,
            "curve_type": curve,
            "inverted": inv,
        })

        def task():
            self.serial.send_command(f"CMD:SET_DEADZONE:{dz}")
            self.serial.send_command(f"CMD:SET_FILTER_TYPE:{f_type}")
            self.serial.send_command(f"CMD:SET_SPIKE_FILTER:{spike}")
            self.serial.send_command(f"CMD:SET_FILTER:{f_size}")
            self.serial.send_command(f"CMD:SET_CURVE:{curve}")
            self.serial.send_command(f"CMD:SET_INVERT:{inv}")
            if not self.serial.demo_mode:
                self._safe_ui(lambda: self.lbl_status.configure(
                    text="Настройки применены (ОЗУ)", fg=C_ACCENT))

        threading.Thread(target=task, daemon=True).start()

    def _save_config(self):
        if not self.serial.connected:
            return
        self._apply_settings()

        def task():
            time.sleep(0.05)
            resp = self.serial.send_command("CMD:SAVE")
            if resp and "RSP:OK:SAVED" in resp:
                self._safe_ui(lambda: self.lbl_status.configure(
                    text="Сохранено во Flash педали", fg=C_ACCENT))
            else:
                self._safe_ui(lambda: messagebox.showerror(
                    "Ошибка", "Не удалось сохранить во Flash память"))

        threading.Thread(target=task, daemon=True).start()

    def _load_config(self):
        if not self.serial.connected:
            return

        def task():
            self.serial.send_command("CMD:LOAD")
            resp = self.serial.send_command("CMD:GET_CONFIG")
            self._safe_ui(lambda: self._on_config_received(resp))

        threading.Thread(target=task, daemon=True).start()

    def _on_config_received(self, resp: str | None):
        if not resp or not resp.startswith("RSP:CONFIG:"):
            return
        try:
            cfg = json.loads(resp[len("RSP:CONFIG:"):])
            self.config.update(cfg)
            self._update_cal_labels()

            dz = cfg.get("deadzone", 0)
            self.var_deadzone.set(dz // 10)
            self.lbl_deadzone_val.configure(text=f"{(dz // 10) / 100:.2f}%")

            f_type = cfg.get("filter_type", 0)
            if f_type not in FILTER_ID_TO_SPEC:
                f_type = 0
            self.var_filter_name.set(FILTER_ID_TO_SPEC[f_type]["name"])

            self.var_spike_filter.set(bool(cfg.get("spike_filter", 0)))

            f_size = cfg.get("filter_size", 4)
            self.var_filter_size.set(f_size)
            self.lbl_filter_val.configure(text=f"{f_size} / 32")

            self.var_curve.set(cfg.get("curve_type", 0))
            self.var_invert.set(bool(cfg.get("inverted", 0)))

            self._update_latency_ui()
            self._draw_curve_preview()
            if not self.serial.demo_mode:
                self.lbl_status.configure(text="Конфигурация загружена", fg=C_ACCENT)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            messagebox.showerror("Ошибка", f"Ошибка разбора конфигурации:\n{e}")

    # -------------------------------------------------------------------------
    # Графики и телеметрия
    # -------------------------------------------------------------------------

    def _draw_curve_preview(self):
        c = self.curve_canvas
        c.delete("all")
        w, h = 160, 130
        m_l, m_b, m_t, m_r = 22, 18, 10, 10
        pw = w - m_l - m_r
        ph = h - m_t - m_b

        # Сетка 25%
        for step in (0.25, 0.5, 0.75):
            gx = m_l + step * pw
            gy = h - m_b - step * ph
            c.create_line(gx, m_t, gx, h - m_b, fill=C_BORDER, width=1)
            c.create_line(m_l, gy, m_l + pw, gy, fill=C_BORDER, width=1)

        # Заливка мёртвой зоны
        dz_frac = min(0.95, (self.var_deadzone.get() / 1000.0) * 0.10)
        if dz_frac > 0.002:
            c.create_rectangle(m_l, m_t, m_l + dz_frac * pw, h - m_b,
                               fill=C_DEADZONE, outline="")

        # Оси
        c.create_line(m_l, h - m_b, m_l + pw, h - m_b, fill=C_TEXT_DIM, width=1)
        c.create_line(m_l, h - m_b, m_l, m_t, fill=C_TEXT_DIM, width=1)
        c.create_text(m_l + pw / 2, h - 7, text="Усилие %", fill=C_TEXT_DIM, font=("Segoe UI", 7))

        # Линейная диагональ-ориентир
        c.create_line(m_l, h - m_b, m_l + pw, m_t, fill=C_TEXT_DIM, dash=(3, 3))

        # Расчёт и отрисовка кривой с учётом мёртвой зоны
        curve_type = self.var_curve.get()
        pts = []
        for i in range(61):
            t = i / 60.0
            if t <= dz_frac:
                out = 0.0
            else:
                eff = (t - dz_frac) / (1.0 - dz_frac)
                if curve_type == 1:
                    out = eff * eff
                elif curve_type == 2:
                    out = math.sqrt(eff)
                else:
                    out = eff
            pts.extend([m_l + t * pw, (h - m_b) - out * ph])

        c.create_line(pts, fill=C_ACCENT, width=2)

        # Живая рабочая точка на кривой при нажатии педали
        if self.serial.connected:
            t_cur = max(0.0, min(1.0, self.current_norm_input))
            if t_cur <= dz_frac:
                o_cur = 0.0
            else:
                eff = (t_cur - dz_frac) / (1.0 - dz_frac)
                o_cur = eff * eff if curve_type == 1 else (math.sqrt(eff) if curve_type == 2 else eff)
            px = m_l + t_cur * pw
            py = (h - m_b) - o_cur * ph
            c.create_oval(px - 4, py - 4, px + 4, py + 4, fill=C_YELLOW, outline="#ffffff", width=1)

    def _schedule_update(self):
        self._update_realtime()
        self._update_job = self.root.after(GRAPH_UPDATE_MS, self._schedule_update)

    def _update_realtime(self):
        if not self.serial.connected:
            return

        if not self.serial._running:
            self._disconnect()
            messagebox.showwarning("Соединение потеряно", "Связь с педалью по COM-порту потеряна.")
            return

        data = self.serial.get_data()
        if not data:
            return

        cmin = self.config.get("cal_min", 0)
        cmax = self.config.get("cal_max", 500000)
        inv = bool(self.config.get("inverted", 0))
        span = (cmax - cmin) if cmax != cmin else 1

        for filt_raw, force_val, unfilt_raw in data:
            filt_pct = (force_val / 65535.0) * 100.0
            raw_norm = (unfilt_raw - cmin) / float(span)
            if inv:
                raw_norm = 1.0 - raw_norm
            raw_pct = max(0.0, min(100.0, raw_norm * 100.0))

            self.graph_filt.append(filt_pct)
            self.graph_raw.append(raw_pct)
            self.raw_noise_window.append(unfilt_raw)
            self.filt_noise_window.append(filt_raw)

        self.graph_filt = self.graph_filt[-self.max_graph_points:]
        self.graph_raw = self.graph_raw[-self.max_graph_points:]
        self.raw_noise_window = self.raw_noise_window[-40:]
        self.filt_noise_window = self.filt_noise_window[-40:]

        last_filt_raw, last_force, _ = data[-1]
        pct = (last_force / 65535.0) * 100.0
        if pct > self.peak_force:
            self.peak_force = pct

        # Обновляем положение живой точки на кривой отклика
        in_norm = (last_filt_raw - cmin) / float(span)
        self.current_norm_input = 1.0 - in_norm if inv else in_norm
        self._draw_curve_preview()

        self.lbl_percent.configure(text=f"{pct:.1f}%")
        self.lbl_hid.configure(text=f"HID: {last_force} / 65535")
        self.lbl_raw.configure(text=f"АЦП: {last_filt_raw:,}".replace(",", " "))
        self.lbl_peak.configure(text=f"Пик: {self.peak_force:.1f}%")

        # Оценка пикового разброса шума (Peak-to-Peak за последние 40 отсчетов)
        if len(self.raw_noise_window) >= 10:
            raw_pp = max(self.raw_noise_window) - min(self.raw_noise_window)
            filt_pp = max(self.filt_noise_window) - min(self.filt_noise_window)
            self.lbl_noise_raw.configure(text=f"{raw_pp:,}".replace(",", " "))
            self.lbl_noise_filt.configure(text=f"{filt_pp:,}".replace(",", " "))
            if raw_pp > 0:
                red = max(0.0, (1.0 - filt_pp / raw_pp) * 100.0)
                self.lbl_noise_gain.configure(text=f"−{red:.0f}%")

        self._update_brake_bar(pct)
        self._update_oscilloscope()

    def _update_brake_bar(self, percent: float):
        c = self.brake_canvas
        c.delete("all")
        w = c.winfo_width() or 76
        h = c.winfo_height() or 280
        pad = 4
        bar_h = h - pad * 2
        if bar_h <= 10:
            return

        # Зона мёртвой зоны снизу
        dz_pct = (self.var_deadzone.get() / 1000.0) * 10.0
        dz_y = h - pad - (dz_pct / 100.0) * bar_h
        if dz_pct > 0.1:
            c.create_rectangle(pad, dz_y, w - pad, h - pad, fill=C_DEADZONE, outline="")

        # Активная полоса заполнения
        fill_h = (percent / 100.0) * bar_h
        y_top = (h - pad) - fill_h
        if percent < 55:
            col = C_ACCENT
        elif percent < 82:
            col = C_YELLOW
        else:
            col = C_RED

        if fill_h > 1:
            c.create_rectangle(pad + 2, y_top, w - pad - 2, h - pad, fill=col, outline="")

        # Риски шкалы каждые 25%
        for tick in (25, 50, 75):
            ty = (h - pad) - (tick / 100.0) * bar_h
            c.create_line(pad, ty, 16, ty, fill=C_TEXT_DIM, width=1)
            c.create_line(w - 16, ty, w - pad, ty, fill=C_TEXT_DIM, width=1)
            c.create_text(w / 2, ty, text=f"{tick}%", fill=C_TEXT_DIM, font=("Consolas", 7))

        # Линия пика
        peak_y = (h - pad) - (self.peak_force / 100.0) * bar_h
        c.create_line(2, peak_y, w - 2, peak_y, fill=C_YELLOW, width=2, dash=(4, 2))

    def _update_oscilloscope(self):
        c = self.graph_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 20 or h <= 20:
            return

        m_l, m_r, m_t, m_b = 38, 10, 12, 20
        pw = w - m_l - m_r
        ph = h - m_t - m_b
        if pw <= 10 or ph <= 10:
            return

        # Горизонтальная сетка процентов
        for pct in (0, 25, 50, 75, 100):
            y = m_t + ph - (pct / 100.0) * ph
            c.create_line(m_l, y, m_l + pw, y, fill=C_BORDER, width=1,
                          dash=(2, 4) if pct not in (0, 100) else None)
            c.create_text(m_l - 6, y, text=f"{pct}%", fill=C_TEXT_DIM,
                          font=("Consolas", 7), anchor="e")

        # Вертикальная сетка секунд
        for sec in range(GRAPH_HISTORY_SECONDS + 1):
            x = m_l + (sec / GRAPH_HISTORY_SECONDS) * pw
            c.create_line(x, m_t, x, m_t + ph, fill=C_BORDER, width=1, dash=(2, 4))
            lbl = f"-{GRAPH_HISTORY_SECONDS - sec}с" if sec < GRAPH_HISTORY_SECONDS else "0с"
            c.create_text(x, m_t + ph + 10, text=lbl, fill=C_TEXT_DIM, font=("Consolas", 7))

        # Порог мёртвой зоны
        dz_pct = (self.var_deadzone.get() / 1000.0) * 10.0
        if dz_pct > 0.1:
            dy = m_t + ph - (dz_pct / 100.0) * ph
            c.create_line(m_l, dy, m_l + pw, dy, fill="#6e2f3d", width=1, dash=(4, 3))

        n = len(self.graph_filt)
        if n < 2:
            return

        # 1. Сырой сигнал до фильтра (если включён)
        if self.var_show_raw.get() and len(self.graph_raw) == n:
            raw_pts = []
            for i, v in enumerate(self.graph_raw):
                x = m_l + (i / max(self.max_graph_points - 1, n - 1)) * pw
                y = m_t + ph - (v / 100.0) * ph
                raw_pts.extend([x, y])
            c.create_line(raw_pts, fill=C_RAW, width=1.2)

        # 2. Основной фильтрованный выход
        filt_pts = []
        for i, v in enumerate(self.graph_filt):
            x = m_l + (i / max(self.max_graph_points - 1, n - 1)) * pw
            y = m_t + ph - (v / 100.0) * ph
            filt_pts.extend([x, y])
        c.create_line(filt_pts, fill=C_ACCENT, width=2.2)

    def _reset_peak(self):
        self.peak_force = 0.0
        self.lbl_peak.configure(text="Пик: 0.0%")

    def _on_close(self):
        self._closing = True
        if self._update_job:
            try:
                self.root.after_cancel(self._update_job)
            except (RuntimeError, tk.TclError):
                pass
            self._update_job = None
        if self.serial.connected:
            try:
                self.serial.stop_stream()
                time.sleep(0.05)
            except Exception:
                pass
            self.serial.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    root.configure(bg=C_BG_APP)
    try:
        root.iconbitmap(default="")
    except tk.TclError:
        pass

    app = PedalConfiguratorApp(root)

    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    ww = max(MIN_WIDTH, min(1240, root.winfo_width()))
    wh = max(MIN_HEIGHT, min(800, root.winfo_height()))
    root.geometry(f"{ww}x{wh}+{(sw - ww) // 2}+{(sh - wh) // 2}")
    root.mainloop()


if __name__ == "__main__":
    main()
