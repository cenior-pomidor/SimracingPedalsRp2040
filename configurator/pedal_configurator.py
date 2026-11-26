#!/usr/bin/env python3
"""
SimPedal Configurator v1.2
Графический конфигуратор для тормозной педали SimPedal на базе RP2040 + ADS1220.

Возможности:
- Современный интерфейс в стиле гоночной телеметрии
- Двухканальный осциллограф: сравнение сырого (до фильтра) и очищенного сигнала в реальном времени
- Живой расчёт задержки (Latency, мс), времени установления и подавления шума
- 6 алгоритмов фильтрации (1-Euro, Баттерворт, Калман, Медианный, EMA, SMA) + анти-выброс Медиана ×3
- Пошаговая калибровка, настройка кривой отклика с живой точкой на графике и мёртвой зоны
- Встроенный режим симуляции тензодатчика 330 SPS для демонстрации и проверки фильтров без железа
"""

from collections import deque
import json
import math
import queue
import random
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

import serial
import serial.tools.list_ports

# =============================================================================
# Константы и палитра интерфейса (Motorsport Telemetry Dark Theme)
# =============================================================================

APP_TITLE = "SimPedal Configurator v1.2"
MIN_WIDTH, MIN_HEIGHT = 990, 690
ADC_SAMPLE_RATE = 330.0
ADC_PERIOD_MS = 1000.0 / ADC_SAMPLE_RATE
USB_HID_LATENCY_MS = 1.0

C_BG_APP = "#10151b"
C_BG_CARD = "#191f27"
C_BG_ELEVATED = "#252e38"
C_BG_CANVAS = "#141b22"
C_BORDER = "#34404b"
C_TEXT = "#f1f4f4"
C_TEXT_MUTED = "#b0bdc6"
C_TEXT_DIM = "#8999a8"
C_ACCENT = "#caf476"
C_RAW = "#79b7dd"
C_YELLOW = "#f5cb79"
C_ORANGE = "#eaa16c"
C_RED = "#ff9a92"
C_FILL = "#2b3a31"
CURVE_NAMES = ("Линейная", "Прогрессивная", "Дегрессивная", "Пользовательская")
LINEAR_POINTS = (25, 50, 75)

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
        self.reseed_biquad = False
        self.bq_z1 = 0.0
        self.bq_z2 = 0.0
        self.kalman_p = 1.0
        self.oe_prev_x = 0.0
        self.oe_dx = 0.0

    def configure(self, f_type: int, size: int, spike: bool):
        changed_buf = (f_type != self.type) or (size != self.size and f_type in (0, 2))
        if self.spike_filter != spike:
            self.spike_buf.clear()
        if f_type == 3 and (self.type != f_type or self.size != size):
            self.reseed_biquad = True
        if self.type != f_type:
            self.oe_prev_x = self.state_y
            self.oe_dx = 0.0
            self.kalman_p = 0.08 * self.size ** 2.2
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
            if not self.initialized or self.reseed_biquad:
                seed = self.state_y if self.initialized else x
                self.bq_z1 = seed * (1.0 - b0)
                self.bq_z2 = seed * (b2 - a2)
                self.reseed_biquad = False
            res = b0 * x + self.bq_z1
            self.bq_z1 = b1 * x - a1 * res + self.bq_z2
            self.bq_z2 = b2 * x - a2 * res
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
                span = cal_span if cal_span > 1000 else 500000.0
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


def curve_output(norm, deadzone, curve_type, points=LINEAR_POINTS):
    """Выход 0..1 после калибровки и инверсии, как в map_to_output прошивки."""
    dz = min(0.95, deadzone / 10000)
    x = max(0., min(1., (norm - dz) / (1 - dz)))
    if curve_type == 1:
        return x * x
    if curve_type == 2:
        return math.sqrt(x)
    if curve_type == 3:
        knots = (0, *points, 100)
        segment = min(3, int(x * 4))
        return (knots[segment] + (knots[segment+1] - knots[segment]) * (4*x - segment)) / 100
    return x


def map_force(raw_val, cal_min, cal_max, deadzone, curve_type, inverted, points=LINEAR_POINTS):
    if cal_min == cal_max:
        return 0
    norm = (raw_val - cal_min) / (cal_max - cal_min)
    shaped = curve_output(1 - norm if inverted else norm, deadzone, curve_type, points)
    return int(shaped * 65535 + 0.5)


def noise_readout(window, cal_span):
    """Размах в кодах АЦП и изменение СКО в окне удержания."""
    if not window:
        return "—", "—", "—"
    raw, filt = zip(*window)
    filt_range = f"{max(filt) - min(filt):,}".replace(",", " ")
    if any(v is None for v in raw):
        return "—", filt_range, "Нет канала"
    raw_range = f"{max(raw) - min(raw):,}".replace(",", " ")
    n = len(raw)
    if n < 10:
        return raw_range, filt_range, "Сбор…"
    if max(raw) == min(raw):
        return raw_range, filt_range, "Нет разброса"
    # Сравниваем шум в покое. Наклон внутри окна отделяет нажатие от шума.
    def trend_and_rms(values):
        mid = (n - 1) / 2
        mean = sum(values) / n
        slope = sum((i-mid)*(v-mean) for i,v in enumerate(values)) / (n*(n*n-1)/12)
        rms = math.sqrt(sum((v-mean-slope*(i-mid))**2 for i,v in enumerate(values)) / n)
        return abs(slope)*(n-1), rms
    drift, before = trend_and_rms(raw)
    filt_drift, after = trend_and_rms(filt)
    if max(drift, filt_drift) > max(abs(cal_span)*0.003, before*3) or before < 1e-8:
        return raw_range, filt_range, "Движение"
    gain = 100 * (1 - after / before)
    return raw_range, filt_range, f"{gain:.1f}%"


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
        self._data_queue: queue.Queue[tuple[float, int, int, int | None]] = queue.Queue()
        self._response_queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self.adc_online: bool | None = None

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
            "version": 3,
            "curve_points": LINEAR_POINTS,
            "adc_ok": 1,
        }
        self._demo_filter = PythonFilterMirror()
        self._demo_t = 0.0
        self._demo_saved = self._demo_cfg.copy()

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
                timeout=SERIAL_TIMEOUT, write_timeout=1,
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
        self.adc_online = True
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
            with self._lock:
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

    def get_data(self) -> list[tuple[float, int, int, int | None]]:
        """Возвращает [(время, filtered_raw, force, unfiltered_raw), ...]."""
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
                        elif line == "STA:ADC:OFFLINE":
                            self.adc_online = False
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
            fields = line[4:].split(",")
            if len(fields) not in (2, 3):
                return
            filt, force = int(fields[0]), int(fields[1])
            raw = int(fields[2]) if len(fields) == 3 else None
            if not 0 <= force <= 65535:
                return
            self.adc_online = True
            self._data_queue.put((time.monotonic(), filt, force, raw))
        except (ValueError, IndexError):
            pass

    def _handle_demo_command(self, cmd: str) -> str:
        c = self._demo_cfg
        if cmd == "CMD:PING":
            return "RSP:PONG:SimPedal:v1.2-DEMO"
        if cmd == "CMD:GET_CONFIG":
            return "RSP:CONFIG:" + json.dumps(c)
        if cmd == "CMD:LOAD":
            c.update(self._demo_saved)
            self._demo_filter.configure(c["filter_type"], c["filter_size"], bool(c["spike_filter"]))
            return "RSP:OK:LOADED"
        if cmd == "CMD:SAVE":
            self._demo_saved = c.copy()
            return "RSP:OK:SAVED"
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
            args = cmd.split(":")[-1]
            fields = args.split(",")
            if fields[0] not in ("0", "1", "2", "3") or len(fields) not in (1, 4):
                return "RSP:ERR:CURVE"
            if len(fields) == 4:
                if not all(x.isascii() and x.isdigit() for x in fields[1:]):
                    return "RSP:ERR:CURVE_POINTS"
                points = tuple(map(int, fields[1:]))
                if not 0 <= points[0] <= points[1] <= points[2] <= 100:
                    return "RSP:ERR:CURVE_POINTS"
                c["curve_points"] = points
            c["curve_type"] = int(fields[0])
            return "RSP:OK:CURVE:" + args
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

            span = 500000
            clean = 12000 + base_norm * span

            # Реалистичный шум: белый шум + 50 Гц наводка + редкие ЭМИ-спайки
            white = random.gauss(0.0, span * 0.009)
            hum_50hz = math.sin(2.0 * math.pi * 50.0 * self._demo_t) * (span * 0.012)
            spike = (span * 0.14 * random.choice([-1, 1])) if random.random() < 0.012 else 0.0
            raw_val = clean + white + hum_50hz + spike

            with self._lock:
                c = self._demo_cfg
                filt_val = round(self._demo_filter.update(raw_val, abs(c["cal_max"] - c["cal_min"])))
                force_val = map_force(filt_val, c["cal_min"], c["cal_max"], c["deadzone"],
                                      c["curve_type"], c["inverted"], c["curve_points"])

            # Отдаём телеметрию с частотой ~100 Гц (каждый 3-й сэмпл АЦП)
            if self.streaming and (step % 3 == 0):
                self._data_queue.put((time.monotonic(), filt_val, force_val, round(raw_val)))

            self._demo_t += dt
            step += 1
            time.sleep(dt)


def read_config(link):
    reply = link.send_command("CMD:GET_CONFIG")
    if not reply or not reply.startswith("RSP:CONFIG:"):
        raise ValueError("Педаль не вернула конфигурацию")
    cfg = json.loads(reply.removeprefix("RSP:CONFIG:"))
    required = ("cal_min", "cal_max", "curve_type", "deadzone", "inverted", "filter_size")
    optional = ("filter_type", "spike_filter", "adc_ok")
    if (not isinstance(cfg, dict) or any(type(cfg.get(k)) is not int for k in required)
            or any(type(cfg[k]) is not int for k in optional if k in cfg)):
        raise ValueError("Некорректный формат конфигурации педали")
    if not (0 <= cfg["curve_type"] <= (3 if "curve_points" in cfg else 2)
            and 0 <= cfg.get("filter_type", 0) < len(FILTER_SPECS)
            and all(cfg[k] in (0, 1) for k in ("inverted", "spike_filter", "adc_ok") if k in cfg)
            and 1 <= cfg["filter_size"] <= 32 and 0 <= cfg["deadzone"] <= 10000):
        raise ValueError("Параметры устройства выходят за рабочий диапазон")
    if "curve_points" in cfg:
        p = cfg["curve_points"]
        if (not isinstance(p, list) or len(p) != 3 or not all(type(v) is int for v in p)
                or not 0 <= p[0] <= p[1] <= p[2] <= 100):
            raise ValueError("Некорректные точки кривой")
        cfg["curve_points"] = tuple(p)
    return cfg


def expect(link, command, expected):
    reply = link.send_command("CMD:" + command)
    if reply != "RSP:OK:" + expected:
        raise ValueError(f"{command} — {reply or 'нет ответа'}")


def write_settings(link, current, draft, save):
    curve = draft["curve_type"]
    if curve == 3 and "curve_points" not in current:
        raise ValueError("Для редактирования точек нужна прошивка 1.2")
    fields = (("deadzone", "DEADZONE"), ("filter_type", "FILTER_TYPE"),
              ("spike_filter", "SPIKE_FILTER"), ("filter_size", "FILTER"), ("inverted", "INVERT"))
    for key, command in fields:
        if key in current and draft[key] != current[key]:
            value = f"{command}:{draft[key]}"
            expect(link, "SET_" + value, value)
    points_changed = "curve_points" in current and current["curve_points"] != draft["curve_points"]
    if points_changed or curve != current["curve_type"]:
        values = [curve, *draft["curve_points"]] if points_changed else [curve]
        arg = ",".join(map(str, values))
        expect(link, "SET_CURVE:" + arg, "CURVE:" + arg)
    confirmed = read_config(link)
    for key, value in draft.items():
        if key in current and confirmed.get(key) != value:
            raise ValueError("Педаль вернула другое значение " + key)
    if save:
        expect(link, "SAVE", "SAVED")
    return confirmed


class PedalConfiguratorApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.minsize(MIN_WIDTH, MIN_HEIGHT)
        root.configure(bg=C_BG_APP)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        families = set(tkfont.families(root))
        self.sans = next((f for f in ("Segoe UI", "Inter", "Open Sans") if f in families), "Arial")
        self.mono = next((f for f in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono") if f in families), "Courier")
        self.serial = PedalSerial()
        self.config = {}
        self.custom_supported = True
        self.busy = False
        self.results = queue.SimpleQueue()
        self.apply_at = None
        self.last_adc_state = None
        self.history = deque()
        self.noise_window = deque(maxlen=40)
        self.peak = 0.0
        self.live = None
        self.drag_index = None
        self.points = LINEAR_POINTS
        self.var_port = tk.StringVar()
        self.var_curve = tk.StringVar(value=CURVE_NAMES[0])
        self.var_dz = tk.StringVar(value="1.00")
        self.deadzone = 100
        self.var_invert = tk.BooleanVar(value=False)
        self.var_filter = tk.StringVar(value=FILTER_SPECS[5]["name"])
        self.var_size = tk.DoubleVar(value=8)
        self.var_spike = tk.BooleanVar(value=True)
        self.var_raw = tk.BooleanVar(value=True)
        self.point_vars = [tk.StringVar(value=str(p)) for p in self.points]
        self._setup_styles()
        self._build_header()
        content = tk.Frame(root, bg=C_BG_APP)
        content.pack(fill="both", expand=True, padx=20, pady=(2, 0))
        content.columnconfigure(0, weight=5, uniform="pane")
        content.columnconfigure(1, weight=4, uniform="pane")
        content.rowconfigure(0, weight=1)
        self._build_monitor(content)
        self._build_editor(content)
        self._build_footer()
        self._refresh_ports()
        self._update_filter_info()
        self._update_controls()
        self._update_job = root.after(1, self._tick)

    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(self.sans, 10), background=C_BG_CARD, foreground=C_TEXT,
                        bordercolor=C_BORDER, focuscolor=C_ACCENT, lightcolor=C_BORDER,
                        darkcolor=C_BORDER, fieldbackground=C_BG_CANVAS)
        style.configure("TButton", padding=(11, 6), background=C_BG_ELEVATED, relief="flat", borderwidth=0)
        style.map("TButton", background=[("active", C_BORDER)], foreground=[("disabled", C_TEXT_DIM)])
        style.configure("Accent.TButton", background=C_ACCENT, foreground=C_BG_APP, font=(self.sans, 10, "bold"))
        style.map("Accent.TButton", background=[("disabled", C_BG_ELEVATED), ("active", "#e1ffab")],
                  foreground=[("disabled", C_TEXT_DIM), ("!disabled", C_BG_APP)])
        for name in ("TCombobox", "TSpinbox"):
            style.configure(name, padding=4, background=C_BG_ELEVATED, arrowcolor=C_TEXT,
                            insertcolor=C_TEXT, arrowsize=13, foreground=C_TEXT)
            style.map(name, fieldbackground=[("disabled", C_BG_CARD), ("readonly", C_BG_CANVAS)],
                      foreground=[("disabled", C_TEXT_DIM), ("!disabled", C_TEXT)],
                      selectbackground=[("readonly", C_BG_CANVAS)], selectforeground=[("readonly", C_TEXT)])
        style.configure("TCheckbutton", indicatorbackground=C_BG_ELEVATED, padding=2)
        style.map("TCheckbutton", background=[("active", C_BG_CARD)], foreground=[("disabled", C_TEXT_DIM)])
        style.configure("Horizontal.TScale", troughcolor=C_BG_CANVAS, background=C_ACCENT, sliderlength=18)
        style.configure("TNotebook", background=C_BG_CARD, borderwidth=0, tabmargins=0)
        style.configure("TNotebook.Tab", padding=(13, 7), background=C_BG_CARD, borderwidth=0,
                        font=(self.sans, 10, "bold"))
        style.map("TNotebook.Tab", foreground=[("selected", C_BG_APP), ("!selected", C_TEXT_MUTED)],
                  background=[("selected", C_ACCENT), ("active", C_BG_ELEVATED)])
        self.root.option_add("*TCombobox*Listbox.background", C_BG_ELEVATED)
        self.root.option_add("*TCombobox*Listbox.foreground", C_TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", C_BORDER)
        self.root.option_add("*TCombobox*Listbox.font", (self.sans, 10))
        style.map("TNotebook.Tab", padding=[("selected", (13, 7))], expand=[("selected", (0, 0, 0, 0))])

    def _label(self, parent, text, size=10, color=C_TEXT, bold=False, mono=False, **kwargs):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color, anchor="w",
                        bd=0, padx=0, pady=0, font=(self.mono if mono else self.sans, size,
                        "bold" if bold else "normal"), **kwargs)

    @staticmethod
    def _card(parent):
        return tk.Frame(parent, bg=C_BG_CARD, highlightthickness=1, highlightbackground=C_BORDER,
                        padx=16, pady=12)

    def _build_header(self):
        bar = tk.Frame(self.root, bg=C_BG_APP)
        bar.pack(fill="x", padx=20, pady=(12, 12))
        mark = tk.Canvas(bar, bg=C_ACCENT, width=32, height=32, bd=0, highlightthickness=0)
        mark.pack(side="left")
        mark.create_line(5, 23, 12, 23, 18, 10, 27, 10, fill=C_BG_APP, width=3)
        self._label(bar, "SimPedal", 19, bold=True).pack(side="left", padx=(9, 14))
        self.status_dot = tk.Canvas(bar, width=9, height=9, bg=C_BG_APP, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_dot.create_oval(1, 1, 8, 8, fill=C_TEXT_DIM, outline="", tags="state")
        self.status = self._label(bar, "Нет подключения", 10, C_TEXT_MUTED)
        self.status.pack(side="left")
        self.btn_demo = ttk.Button(bar, text="Демо", command=self._toggle_demo)
        self.btn_demo.pack(side="right", padx=(6, 0))
        self.btn_connect = ttk.Button(bar, text="Подключить", command=self._toggle_connection)
        self.btn_connect.pack(side="right", padx=(6, 0))
        self.combo_port = ttk.Combobox(bar, textvariable=self.var_port, state="readonly", width=13,
                                       postcommand=self._refresh_ports)
        self.combo_port.pack(side="right")
        self._label(bar, "Порт", 9, C_TEXT_MUTED).pack(side="right", padx=7)

    def _build_monitor(self, parent):
        left = tk.Frame(parent, bg=C_BG_APP)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        hero = self._card(left)
        hero.pack(fill="x")
        self._label(hero, "Усилие тормоза", 13, bold=True).pack(anchor="w")
        strip = tk.Frame(hero, bg=C_BG_CARD)
        strip.pack(fill="x")
        self.force_label = self._label(strip, "—", 36, C_ACCENT, True, True)
        self.force_label.pack(side="left")
        stats = tk.Frame(strip, bg=C_BG_CARD)
        stats.pack(side="right")
        self.hid_label = self._label(stats, "HID  —", 10, mono=True)
        self.hid_label.pack(anchor="e")
        self.peak_label = self._label(stats, "Пик  —", 9, C_TEXT_MUTED, mono=True)
        self.peak_label.pack(anchor="e")
        self.bar = tk.Canvas(hero, bg=C_BG_CARD, height=10, highlightthickness=0)
        self.bar.pack(fill="x", pady=(3, 2))
        self.bar.bind("<Configure>", lambda _: self._draw_bar())

        scope = self._card(left)
        scope.pack(fill="both", expand=True, pady=10)
        line = tk.Frame(scope, bg=C_BG_CARD)
        line.pack(fill="x")
        self._label(line, "Сигнал датчика", 13, bold=True).pack(side="left")
        ttk.Checkbutton(line, text="Вход", variable=self.var_raw,
                        command=self._draw_scope).pack(side="right")
        tk.Frame(line, bg=C_RAW, width=14, height=2).pack(side="right", padx=(9, 2))
        self._label(line, "Фильтр", 9, C_TEXT_MUTED).pack(side="right")
        tk.Frame(line, bg=C_ACCENT, width=14, height=3).pack(side="right", padx=(9, 4))
        self.graph = tk.Canvas(scope, bg=C_BG_CANVAS, highlightthickness=0, height=145, width=200)
        self.graph.pack(fill="both", expand=True, pady=(9, 7))
        self.graph.bind("<Configure>", lambda _: self._draw_scope())
        info = tk.Frame(scope, bg=C_BG_CARD)
        info.pack(fill="x")
        self.adc_label = self._label(info, "АЦП  —", 9, C_TEXT_MUTED, mono=True)
        self.adc_label.pack(side="left")
        ttk.Button(info, text="Сбросить пик", command=self._reset_peak).pack(side="right")

        noise = self._card(left)
        noise.pack(fill="x")
        self._label(noise, "Шум при удержании", 12, bold=True).pack(anchor="w")
        row = tk.Frame(noise, bg=C_BG_CARD)
        row.pack(fill="x", pady=(6, 0))
        self.noise_labels = []
        for i, title in enumerate(("До · размах", "После · размах", "Подавление · σ")):
            row.columnconfigure(i, weight=1, uniform="metric")
            self._label(row, title, 9, C_TEXT_MUTED).grid(row=0, column=i, sticky="w")
            value = self._label(row, "—", 12, C_ACCENT if i == 2 else C_TEXT, True)
            value.grid(row=1, column=i, sticky="w")
            self.noise_labels.append(value)

    def _build_editor(self, parent):
        panel = self._card(parent)
        panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._label(panel, "Настройка педали", 15, bold=True).pack(anchor="w", pady=(0, 10))
        self.tabs = ttk.Notebook(panel)
        self.tabs.pack(fill="both", expand=True)
        self.tabs.enable_traversal()
        for title, build in (("Отклик", self._curve_tab), ("Фильтр", self._filter_tab),
                             ("Калибровка", self._calibration_tab)):
            page = tk.Frame(self.tabs, bg=C_BG_CARD, pady=10)
            self.tabs.add(page, text=title)
            build(page)

    def _curve_tab(self, page):
        row = tk.Frame(page, bg=C_BG_CARD)
        row.pack(fill="x")
        self._label(row, "Кривая отклика", 11, bold=True).pack(side="left")
        self.curves = ttk.Combobox(row, textvariable=self.var_curve, values=CURVE_NAMES,
                                   state="readonly", width=17)
        self.curves.pack(side="right")
        self.curves.bind("<<ComboboxSelected>>", lambda _: self._changed())
        self.curve_graph = tk.Canvas(page, bg=C_BG_CANVAS, height=130, width=200,
                                     highlightthickness=0)
        self.curve_graph.pack(fill="both", expand=True, pady=(9, 3))
        self.curve_graph.bind("<Configure>", lambda _: self._draw_curve())
        self.curve_graph.bind("<Button-1>", self._grab_point)
        self.curve_graph.bind("<B1-Motion>", self._move_point)
        self.curve_graph.bind("<ButtonRelease-1>", self._release_point)
        self.curve_help = self._label(page, "", 9, C_TEXT_MUTED)
        self.curve_help.pack(fill="x", pady=(1, 6))
        row = tk.Frame(page, bg=C_BG_CARD)
        row.pack(fill="x")
        self.point_boxes = []
        for i, x in enumerate(LINEAR_POINTS):
            row.columnconfigure(i, weight=1, uniform="points")
            group = tk.Frame(row, bg=C_BG_CARD)
            group.grid(row=0, column=i, sticky="ew", padx=(0, 6))
            self._label(group, f"Вход {x}%", 9, C_TEXT_MUTED).pack(anchor="w")
            box = ttk.Spinbox(group, from_=0, to=100, width=5, textvariable=self.point_vars[i],
                              command=lambda i=i: self._commit_point(i))
            box.pack(fill="x", pady=(2, 0))
            box.bind("<Return>", lambda _, i=i: self._commit_point(i))
            box.bind("<FocusOut>", lambda _, i=i: self._commit_point(i))
            self.point_boxes.append(box)
        line = tk.Frame(page, bg=C_BG_CARD)
        line.pack(fill="x", pady=(11, 0))
        self._label(line, "Мёртвая зона, %", 10).pack(side="left")
        self.dz_box = ttk.Spinbox(line, from_=0, to=95, increment=0.1, format="%.2f",
                                  width=6, textvariable=self.var_dz, command=self._commit_dz)
        self.dz_box.pack(side="left", padx=6)
        self.dz_box.bind("<Return>", lambda _: self._commit_dz())
        self.dz_box.bind("<FocusOut>", lambda _: self._commit_dz())
        ttk.Checkbutton(line, text="Инверсия", variable=self.var_invert,
                        command=self._changed).pack(side="right")

    def _filter_tab(self, page):
        self._label(page, "Алгоритм", 11, bold=True).pack(anchor="w")
        self.filter_box = ttk.Combobox(page, values=[s["name"] for s in FILTER_SPECS],
                                       textvariable=self.var_filter, state="readonly")
        self.filter_box.pack(fill="x", pady=(4, 9))
        self.filter_box.bind("<<ComboboxSelected>>", lambda _: self._changed())
        self.filter_desc = self._label(page, "", 10, C_TEXT_MUTED, wraplength=330, justify="left")
        self.filter_desc.pack(fill="x")
        row = tk.Frame(page, bg=C_BG_CARD)
        row.pack(fill="x", pady=(12, 0))
        self._label(row, "Сила фильтра", 11, bold=True).pack(side="left")
        self.size_label = self._label(row, "", 11, C_ACCENT, True, True)
        self.size_label.pack(side="right")
        ttk.Scale(page, from_=1, to=32, variable=self.var_size, command=lambda _: self._changed()).pack(
            fill="x", pady=(4, 8))
        self.spike_box = ttk.Checkbutton(page, text="Подавление одиночных выбросов", variable=self.var_spike,
                                         command=self._changed)
        self.spike_box.pack(anchor="w")
        self.latency = self._label(page, "", 11, C_TEXT, True, justify="left")
        self.latency.pack(anchor="w", pady=(14, 3))
        self.filter_info = self._label(page, "", 10, C_TEXT_MUTED, justify="left", wraplength=350)
        self.filter_info.pack(anchor="w")

    def _calibration_tab(self, page):
        self.cal_labels = {}
        self.cal_buttons = []
        for key, name, action in (("min", "Отпущенная педаль", "Задать ноль"),
                                  ("max", "Максимальное рабочее усилие", "Задать максимум")):
            self._label(page, name, 11, bold=True).pack(anchor="w", pady=(5, 3))
            row = tk.Frame(page, bg=C_BG_CARD)
            row.pack(fill="x", pady=(0, 11))
            label = self._label(row, "—", 16, C_TEXT, mono=True)
            label.pack(side="left")
            self.cal_labels[key] = label
            btn = ttk.Button(row, text=action, command=lambda k=key: self._calibrate("CAL_"+k.upper()))
            btn.pack(side="right")
            self.cal_buttons.append(btn)
        self.cal_span = self._label(page, "", 10, C_TEXT_MUTED)
        self.cal_span.pack(anchor="w", pady=(0, 10))
        self.cal_reset = ttk.Button(page, text="Сбросить калибровку", command=lambda: self._calibrate("RESET_CAL"))
        self.cal_reset.pack(anchor="w")

    def _build_footer(self):
        row = tk.Frame(self.root, bg=C_BG_APP)
        row.pack(fill="x", padx=20, pady=(10, 12))
        self.notice = self._label(row, "Подключите педаль или откройте деморежим.", 10, C_TEXT_MUTED,
                                  wraplength=520, justify="left")
        self.notice.pack(side="left", fill="x", expand=True)
        self.btn_save = ttk.Button(row, text="Сохранить во Flash", style="Accent.TButton",
                                   command=lambda: self._apply(True))
        self.btn_save.pack(side="right", padx=(7, 0))
        self.btn_apply = ttk.Button(row, text="Применить", command=self._apply)
        self.btn_apply.pack(side="right", padx=(7, 0))
        self.btn_load = ttk.Button(row, text="Из Flash", command=self._restore)
        self.btn_load.pack(side="right")

    def _refresh_ports(self):
        if self.serial.connected:
            return
        try:
            ports = PedalSerial.list_ports()
            self.combo_port.configure(values=ports)
            if self.var_port.get() not in ports:
                preferred = PedalSerial.find_pedal_ports()
                self.var_port.set(next(iter(preferred or ports), ""))
        except (OSError, serial.SerialException) as exc:
            self._note(str(exc), C_RED)

    def _note(self, text, color=C_TEXT_MUTED):
        self.notice.configure(text=text, fg=color)

    def _job(self, fn, done, message, failed=None):
        if self.busy:
            return
        self.busy = True
        self._note(message)
        self._update_controls()
        def worker():
            try:
                result, error = fn(), None
            except (OSError, ValueError, RuntimeError) as exc:
                result, error = None, str(exc)
            self.results.put((done, failed, result, error))
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_connection(self):
        if self.serial.connected:
            self._disconnect()
        elif self.var_port.get():
            self._connect(self.var_port.get())
        else:
            self._refresh_ports()
            self._note("Выберите COM-порт педали.", C_YELLOW)

    def _toggle_demo(self):
        if self.serial.demo_mode:
            self._disconnect()
        else:
            self._connect(None)

    def _connect(self, port):
        link = self.serial
        def open_session():
            try:
                if port is None:
                    link.connect_demo()
                elif not link.connect(port):
                    raise ValueError("Педаль не ответила на запрос соединения")
                cfg = read_config(link)
                link.adc_online = bool(cfg["adc_ok"]) if "adc_ok" in cfg else None
                link.start_stream()
                return cfg
            except Exception:
                link.disconnect()
                raise
        def opened(cfg):
            self._show_config(cfg)
            self._update_controls()
            self._note("Изменения действуют в деморежиме." if link.demo_mode else
                       "Изменения применяются в ОЗУ. Для следующего запуска сохраните их во Flash.")
        self._job(open_session, opened, "Читаю настройки…")

    def _disconnect(self, lost=False):
        self.serial.disconnect()
        self.serial.adc_online = None
        self.config = {}
        self.custom_supported = True
        self.apply_at = None
        self.history.clear()
        self.noise_window.clear()
        self.live = None
        self.peak = 0
        self.force_label.configure(text="—")
        self.hid_label.configure(text="HID  —")
        self.adc_label.configure(text="АЦП  —")
        self.peak_label.configure(text="Пик  —")
        self._display_noise()
        self._update_controls()
        self._draw_scope()
        self._draw_curve()
        self._draw_bar()
        self._note("Соединение с педалью потеряно." if lost else "Педаль отключена.",
                   C_RED if lost else C_TEXT_MUTED)

    def _update_controls(self):
        link = self.serial
        connected = link.connected
        enabled = connected and not self.busy and bool(self.config)
        for button in (self.btn_apply, self.btn_save, self.btn_load, self.cal_reset):
            button.state(["!disabled" if enabled else "disabled"])
        for button in self.cal_buttons:
            button.state(["!disabled" if enabled and link.adc_online is not False else "disabled"])
        self.btn_connect.configure(text="Отключить" if connected and not link.demo_mode else "Подключить")
        self.btn_demo.configure(text="Выйти из демо" if link.demo_mode else "Демо")
        self.btn_connect.state(["disabled" if self.busy or link.demo_mode else "!disabled"])
        self.btn_demo.state(["disabled" if self.busy or connected and not link.demo_mode else "!disabled"])
        self.combo_port.configure(state="disabled" if connected or self.busy else "readonly")
        self.curves.configure(values=CURVE_NAMES if self.custom_supported else CURVE_NAMES[:3])
        for box in self.point_boxes:
            box.state(["!disabled" if self.custom_supported else "disabled"])
        self.filter_box.configure(state="readonly" if not self.config or "filter_type" in self.config else "disabled")
        self.spike_box.state(["!disabled" if not self.config or "spike_filter" in self.config else "disabled"])
        self.curve_help.configure(text="Перетащите точку или задайте выход в процентах." if self.custom_supported
                                  else "Для редактирования точек установите прошивку 1.2.")
        if not connected:
            label, color = "Нет подключения", C_TEXT_DIM
        elif link.demo_mode:
            label, color = "Деморежим", C_RAW
        elif link.adc_online is False:
            label, color = "АЦП недоступен", C_RED
        else:
            label, color = link.port.port if link.port else "Подключено", C_ACCENT
        self.status_dot.itemconfigure("state", fill=color)
        self.status.configure(text=label, fg=color)
        self.btn_save.configure(text="Сохранить в демо" if link.demo_mode else "Сохранить во Flash")
        self.btn_load.configure(text="Загрузить" if link.demo_mode else "Из Flash")

    def _draft(self):
        return dict(deadzone=self.deadzone, filter_type=self.filter_box.current(),
                    filter_size=max(1, min(32, round(self.var_size.get()))),
                    spike_filter=int(self.var_spike.get()), inverted=int(self.var_invert.get()),
                    curve_type=self.curves.current(), curve_points=self.points)

    def _show_config(self, cfg):
        self.config = cfg
        self.custom_supported = "curve_points" in cfg
        self.curves.configure(values=CURVE_NAMES if self.custom_supported else CURVE_NAMES[:3])
        self.curves.current(cfg["curve_type"])
        self.points = tuple(cfg.get("curve_points", LINEAR_POINTS))
        self.deadzone = cfg["deadzone"]
        self.var_dz.set(f"{self.deadzone / 100:.2f}")
        self.var_invert.set(bool(cfg["inverted"]))
        self.filter_box.current(cfg.get("filter_type", 0))
        self.var_size.set(cfg["filter_size"])
        self.var_spike.set(bool(cfg.get("spike_filter", 0)))
        for key, label in self.cal_labels.items():
            label.configure(text=f"{cfg['cal_'+key]:,}".replace(",", " "))
        self.cal_span.configure(text=f"Диапазон АЦП  {abs(cfg['cal_max']-cfg['cal_min']):,}".replace(",", " "))
        self.apply_at = None
        self._sync_points()
        self._update_filter_info()
        self._draw_curve()

    def _apply(self, save=False):
        if self.busy or not self.config:
            return
        self._commit_entries()
        self.apply_at = None
        draft = self._draft()
        current, link = self.config, self.serial
        def transact():
            # После частичного сбоя читаем действующие значения перед повтором.
            actual = read_config(link)
            return write_settings(link, actual, draft, save)
        def applied(cfg):
            self.config = cfg
            if any(current.get(k) != cfg.get(k) for k in ("filter_type", "filter_size", "spike_filter")):
                self.noise_window.clear()
            note = "Сохранено в деморежиме." if link.demo_mode else "Сохранено во Flash педали."
            self._note(note if save else "Настройки применены.", C_ACCENT)
        def resync(error):
            # Команды старых прошивок раздельные. Сохраняем введённые значения
            # в редакторе, а телеметрию привязываем к подтверждённой конфигурации.
            def refreshed(cfg):
                self.config = cfg
                self._note(error, C_RED)
            if link.connected:
                self._job(lambda: read_config(link), refreshed, error)
        self._job(transact, applied, "Сохранение…" if save else "Применение настроек…", resync)

    def _restore(self):
        self.apply_at = None
        def load():
            expect(self.serial, "LOAD", "LOADED")
            return read_config(self.serial)
        def loaded(cfg):
            self._show_config(cfg)
            self.noise_window.clear()
            self._note("Сохранённые настройки загружены.", C_ACCENT)
        self._job(load, loaded, "Загрузка настроек…")

    def _calibrate(self, name):
        def calibrate():
            reply = self.serial.send_command("CMD:"+name)
            ok = reply == "RSP:OK:RESET" if name == "RESET_CAL" else (
                reply is not None and reply.startswith("RSP:OK:"+name+":"))
            if not ok:
                raise ValueError("Калибровка: " + (reply or "нет ответа"))
            return read_config(self.serial)
        def calibrated(cfg):
            self.config.update(cal_min=cfg["cal_min"], cal_max=cfg["cal_max"])
            for k, label in self.cal_labels.items():
                label.configure(text=f"{cfg['cal_'+k]:,}".replace(",", " "))
            self.cal_span.configure(text=f"Диапазон АЦП  {abs(cfg['cal_max']-cfg['cal_min']):,}".replace(",", " "))
            self.noise_window.clear()
            self.history.clear()
            self._note("Калибровка обновлена.", C_ACCENT)
        self._job(calibrate, calibrated, "Калибровка…")

    def _changed(self):
        self._update_filter_info()
        self._sync_points()
        self._draw_curve()
        if self.config:
            self.apply_at = time.monotonic() + 0.30
            self._note("Есть изменения", C_YELLOW)

    def _update_filter_info(self):
        size = max(1, min(32, round(self.var_size.get())))
        f = max(0, self.filter_box.current())
        self.size_label.configure(text=f"{size} / 32")
        self.filter_desc.configure(text=FILTER_SPECS[f]["desc"])
        params = calculate_filter_latency(f, size, self.var_spike.get())
        delay = (f"{params['filter_dynamic_ms']:.1f}–{params['filter_ms']:.1f}"
                 if params["is_adaptive"] else f"{params['filter_ms']:.1f}")
        self.latency.configure(text=f"Задержка фильтра ≈ {delay} мс")
        self.filter_info.configure(text=f"{params['param_text']}\nДо 95% отклика ≈ {params['settle_95_ms']:.1f} мс"
                                        f"\nПолный тракт ≈ {params['pipeline_ms']:.1f} мс")

    def _shown_points(self):
        mode = self.curves.current()
        if mode == 3:
            return self.points
        return tuple(round(100*curve_output(x/100, 0, mode)) for x in LINEAR_POINTS)

    def _sync_points(self):
        for var, value in zip(self.point_vars, self._shown_points()):
            var.set(str(value))

    def _set_point(self, index, value):
        if not self.custom_supported:
            return
        points = list(self._shown_points())
        lo = points[index-1] if index else 0
        hi = points[index+1] if index < 2 else 100
        clipped = min(hi, max(lo, value))
        if points[index] == clipped:
            self.point_vars[index].set(str(clipped))
            return
        points[index] = clipped
        self.points = tuple(points)
        self.curves.current(3)
        self._changed()

    def _commit_point(self, index):
        try:
            value = int(self.point_vars[index].get())
        except ValueError:
            value = self._shown_points()[index]
        self._set_point(index, value)

    def _commit_dz(self):
        if self.var_dz.get() == f"{self.deadzone/100:.2f}":
            return
        try:
            val = float(self.var_dz.get().replace(",", "."))
            if not math.isfinite(val):
                raise ValueError
            dz = max(0, min(9500, round(val*100)))
        except ValueError:
            dz = self.deadzone
        self.var_dz.set(f"{dz/100:.2f}")
        if dz != self.deadzone:
            self.deadzone = dz
            self._changed()

    def _commit_entries(self):
        focus = self.root.focus_get()
        if focus in self.point_boxes:
            self._commit_point(self.point_boxes.index(focus))
        self._commit_dz()

    def _curve_bounds(self):
        return 38, 22, max(40, self.curve_graph.winfo_width()-15), max(24, self.curve_graph.winfo_height()-27)

    def _handles(self):
        l, t, r, b = self._curve_bounds()
        dz = min(.95, self.deadzone / 10000)
        mode = self.curves.current()
        return [(l+(dz+(1-dz)*x/100)*(r-l), b-curve_output(x/100, 0, mode, self.points)*(b-t))
                for x in LINEAR_POINTS]

    def _grab_point(self, event):
        if self.custom_supported:
            for i, (x, y) in enumerate(self._handles()):
                if math.hypot(event.x-x, event.y-y) <= 13:
                    self.drag_index = i
                    self.curve_graph.configure(cursor="sb_v_double_arrow")
                    break

    def _move_point(self, event):
        if self.drag_index is not None:
            _, top, _, bottom = self._curve_bounds()
            self._set_point(self.drag_index, round((bottom-event.y)*100/(bottom-top)))

    def _release_point(self, event):
        self._move_point(event)
        self.drag_index = None
        self.curve_graph.configure(cursor="")

    def _draw_curve(self):
        c = self.curve_graph
        c.delete("all")
        if c.winfo_width() < 50 or c.winfo_height() < 50:
            return
        l, t, r, b = self._curve_bounds()
        mode = self.curves.current()
        dz = min(.95, self.deadzone / 10000)
        self._axes(c, l, t, r, b, "Выход, %", "Усилие, %", True)
        pts = []
        for x in sorted(set([n/100 for n in range(101)] + [dz + (1-dz)*n/4 for n in range(4)])):
            pts.extend((l+x*(r-l), b-curve_output(x, self.deadzone, mode, self.points)*(b-t)))
        c.create_polygon(l,b,*pts,r,b, fill=C_FILL, outline="")
        self._grid(c,l,t,r,b)
        c.create_line(l,b,r,t,fill=C_TEXT_DIM, dash=(3, 5))
        if dz > 0:
            c.create_rectangle(l,b,l+dz*(r-l),t,fill="#323038",outline="")
        c.create_line(*pts, fill=C_ACCENT, width=2.5)
        if self.custom_supported:
            for x, y in self._handles():
                c.create_oval(x-5, y-5, x+5, y+5, fill=C_BG_CARD, outline=C_ACCENT, width=2)
        if self.live:
            x = l + self.live[0]*(r-l)
            y = b - self.live[1]*(b-t)
            c.create_oval(x-4,y-4,x+4,y+4, fill=C_RAW, outline=C_BG_CANVAS, width=1)

    def _grid(self, c, left, top, right, bottom, columns=4):
        for i in range(5):
            y = bottom - (bottom - top) * i / 4
            c.create_line(left, y, right, y, fill=C_BORDER, dash=(2, 5))
        for i in range(columns + 1):
            x = left + (right - left) * i / columns
            c.create_line(x, top, x, bottom, fill=C_BORDER, dash=(2, 5))

    def _axes(self, c, l, t, r, b, y_title, x_title, percent):
        font = (self.sans, 8)
        c.create_text(8, 4, anchor="nw", text=y_title, fill=C_TEXT_DIM, font=font)
        c.create_text(r, b+6, anchor="ne", text=x_title, fill=C_TEXT_DIM, font=font)
        for i in (0,50,100):
            c.create_text(l-5, b-(b-t)*i/100, anchor="e", text=str(i), fill=C_TEXT_DIM, font=font)
        if percent:
            for i in (0,25,50,75):
                c.create_text(l+(r-l)*i/100,b+6,anchor="n",text=str(i),fill=C_TEXT_DIM,font=font)
        else:
            for i in range(GRAPH_HISTORY_SECONDS):
                c.create_text(l+(r-l)*i/GRAPH_HISTORY_SECONDS,b+6,anchor="n",
                              text=f"−{GRAPH_HISTORY_SECONDS-i}",fill=C_TEXT_DIM,font=font)

    def _draw_scope(self):
        c = self.graph
        c.delete("all")
        l,t,r,b = 38,23,c.winfo_width()-14,c.winfo_height()-25
        if r <= l or b <= t:
            return
        self._axes(c,l,t,r,b,"Ход, %","Сейчас",False)
        self._grid(c,l,t,r,b, GRAPH_HISTORY_SECONDS)
        if not self.history:
            label = "АЦП недоступен" if self.serial.adc_online is False else "Ожидание сигнала"
            c.create_text((l+r)/2,(t+b)/2,text=label,fill=C_TEXT_MUTED,font=(self.sans,11))
            return
        start = time.monotonic() - GRAPH_HISTORY_SECONDS
        while self.history and self.history[0][0] < start:
            self.history.popleft()
        for channel, color in ((1,C_RAW), (2,C_ACCENT)):
            if channel == 1 and not self.var_raw.get():
                continue
            pts = []
            for sample in self.history:
                val = sample[channel]
                if val is None:
                    if len(pts) >= 4:
                        c.create_line(*pts,fill=color,width=1)
                    pts.clear()
                else:
                    pts.extend((l+(sample[0]-start)*(r-l)/GRAPH_HISTORY_SECONDS, b-val*(b-t)))
            if len(pts) >= 4:
                c.create_line(*pts,fill=color,width=2 if channel==2 else 1)

    def _draw_bar(self):
        self.bar.delete("all")
        width = self.bar.winfo_width()
        fill = self.live[1] if self.live else 0
        for i in range(40):
            left = width*i/40
            right = width*(i+1)/40-2
            self.bar.create_rectangle(left,0,right,8,fill=C_BORDER,outline="")
            fraction = min(1, max(0, fill*40-i))
            if fraction:
                self.bar.create_rectangle(left,0,left+fraction*(right-left),8,fill=C_ACCENT,outline="")

    def _reset_peak(self):
        self.peak = 0
        self.peak_label.configure(text="Пик  0.0%")

    def _display_noise(self):
        span = self.config.get("cal_max",0)-self.config.get("cal_min",0)
        for label, text in zip(self.noise_labels, noise_readout(self.noise_window, span)):
            label.configure(text=text)

    def _telemetry(self):
        link = self.serial
        if self.config and not link.connected and not self.busy:
            self._disconnect(lost=True)
            return
        if not self.config or not link.connected:
            return
        if self.last_adc_state != link.adc_online:
            self.last_adc_state = link.adc_online
            self._update_controls()
        data = link.get_data()
        if link.adc_online is False:
            if self.live or self.adc_label.cget("text") != "АЦП недоступен":
                self.live = None
                self.history.clear()
                self.noise_window.clear()
                self.force_label.configure(text="—")
                self.adc_label.configure(text="АЦП недоступен")
                self.hid_label.configure(text="HID  —")
                self._draw_scope()
                self._draw_bar()
                self._draw_curve()
                self._display_noise()
            return
        if not data:
            return
        cmin = self.config["cal_min"]
        span = self.config["cal_max"]-cmin
        def normalized(v):
            if not span:
                return 0.
            x = (v-cmin)/span
            return max(0., min(1., 1-x if self.config["inverted"] else x))
        for stamp, filt, hid, raw in data:
            self.history.append((stamp, normalized(raw) if raw is not None else None, normalized(filt)))
            self.noise_window.append((raw, filt))
            self.peak = max(self.peak, hid/65535)
        self.live = normalized(filt), hid/65535
        self.force_label.configure(text=f"{self.live[1]*100:.1f}%")
        self.hid_label.configure(text=f"HID  {hid} / 65535")
        self.peak_label.configure(text=f"Пик  {self.peak*100:.1f}%")
        self.adc_label.configure(text=f"АЦП после фильтра  {filt:,}".replace(",", " "))
        self._display_noise()
        self._draw_bar()
        self._draw_scope()
        if self.tabs.index("current") == 0:
            self._draw_curve()

    def _tick(self):
        while not self.results.empty():
            done, failed, result, error = self.results.get()
            self.busy = False
            if error:
                self.apply_at = None
                self._note(error, C_RED)
                if failed:
                    failed(error)
            else:
                done(result)
            self._update_controls()
        self._telemetry()
        if self.apply_at and not self.busy and self.drag_index is None and time.monotonic() >= self.apply_at:
            self._apply()
        self._update_job = self.root.after(GRAPH_UPDATE_MS, self._tick)

    def _on_close(self):
        self.root.after_cancel(self._update_job)
        self.serial.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    PedalConfiguratorApp(root)
    width = min(1200, max(MIN_WIDTH, root.winfo_screenwidth()-60))
    height = min(780, max(MIN_HEIGHT, root.winfo_screenheight()-90))
    root.geometry(f"{width}x{height}")
    root.mainloop()


if __name__ == "__main__":
    main()
