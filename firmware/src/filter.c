#include "filter.h"
#include <math.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// Быстрая медиана из 3 элементов
static inline int32_t median3(int32_t a, int32_t b, int32_t c) {
    if (a > b) { int32_t t = a; a = b; b = t; }
    if (b > c) { b = c; }
    if (a > b) { return a; }
    return b;
}

// Сортировка вставками небольшого массива и возврат медианы
static int32_t calc_median_window(const int32_t *buf, uint8_t len) {
    int32_t tmp[FILTER_MEDIAN_MAX];
    if (len > FILTER_MEDIAN_MAX) len = FILTER_MEDIAN_MAX;
    for (uint8_t i = 0; i < len; i++) {
        tmp[i] = buf[i];
    }
    for (uint8_t i = 1; i < len; i++) {
        int32_t key = tmp[i];
        int j = (int)i - 1;
        while (j >= 0 && tmp[j] > key) {
            tmp[j + 1] = tmp[j];
            j--;
        }
        tmp[j + 1] = key;
    }
    return tmp[len / 2];
}

// Размер нечетного окна медианного фильтра по силе 1..32 -> 1, 3, 5, ..., 15
static uint8_t get_median_window_size(uint8_t size) {
    if (size <= 1) return 1;
    uint8_t w = (uint8_t)(3 + 2 * ((size - 2) / 5));
    if (w > FILTER_MEDIAN_MAX) w = FILTER_MEDIAN_MAX;
    return w;
}

// Расчет коэффициентов фильтра Баттерворта 2-го порядка (Low-Pass, Q = 1/sqrt(2))
static void update_biquad_coeffs(pedal_filter_t *f) {
    if (f->size <= 1) {
        f->bq_b0 = 1.0; f->bq_b1 = 0.0; f->bq_b2 = 0.0;
        f->bq_a1 = 0.0; f->bq_a2 = 0.0;
        return;
    }
    // Логарифмическое изменение частоты среза от 80 Гц (size=2) до 4 Гц (size=32)
    double t = (double)(f->size - 2) / 30.0;
    double fc = 80.0 * pow(4.0 / 80.0, t);
    double w0 = 2.0 * M_PI * fc / (double)ADC_SAMPLE_RATE_HZ;
    double cos_w0 = cos(w0);
    double sin_w0 = sin(w0);
    double q = 0.7071067811865475; // 1 / sqrt(2) — характеристика Баттерворта
    double alpha = sin_w0 / (2.0 * q);
    double a0 = 1.0 + alpha;

    f->bq_b0 = ((1.0 - cos_w0) * 0.5) / a0;
    f->bq_b1 = (1.0 - cos_w0) / a0;
    f->bq_b2 = f->bq_b0;
    f->bq_a1 = (-2.0 * cos_w0) / a0;
    f->bq_a2 = (1.0 - alpha) / a0;
}

// Обновление расчетных коэффициентов выбранного фильтра
static void recalculate_params(pedal_filter_t *f) {
    update_biquad_coeffs(f);

    // Параметры Калмана 1D: Q=1.0, R масштабируется от 0.37 (size=2) до ~164 (size=32)
    f->kalman_q = 1.0;
    f->kalman_r = 0.08 * pow((double)f->size, 2.2);

    // Параметры One-Euro: f_min от 25 Гц (size=2) до 1.5 Гц (size=32)
    if (f->size <= 1) {
        f->oe_fmin = 165.0;
        f->oe_beta = 0.0;
    } else {
        double t = (double)(f->size - 2) / 30.0;
        f->oe_fmin = 25.0 * pow(1.5 / 25.0, t);
        f->oe_beta = 60.0;
    }
}

void filter_init(pedal_filter_t *f, uint8_t type, uint8_t size, uint8_t spike_filter) {
    memset(f, 0, sizeof(pedal_filter_t));
    if (type >= FILTER_TYPE_COUNT) type = FILTER_TYPE_SMA;
    if (size < 1) size = 1;
    if (size > FILTER_MAX_SIZE) size = FILTER_MAX_SIZE;

    f->type = type;
    f->size = size;
    f->spike_filter = spike_filter ? 1 : 0;
    recalculate_params(f);
}

void filter_reset(pedal_filter_t *f) {
    uint8_t type = f->type;
    uint8_t size = f->size;
    uint8_t spike = f->spike_filter;
    filter_init(f, type, size, spike);
}

void filter_configure(pedal_filter_t *f, uint8_t type, uint8_t size, uint8_t spike_filter) {
    if (type >= FILTER_TYPE_COUNT) type = FILTER_TYPE_SMA;
    if (size < 1) size = 1;
    if (size > FILTER_MAX_SIZE) size = FILTER_MAX_SIZE;
    uint8_t sf = spike_filter ? 1 : 0;

    bool biquad_changed = type == FILTER_TYPE_BIQUAD && (f->type != type || f->size != size);
    bool need_buffer_reset = (f->type != type) || (f->size != size && (type == FILTER_TYPE_SMA || type == FILTER_TYPE_MEDIAN));
    bool reset_spike = (f->spike_filter != sf);

    f->type = type;
    f->size = size;
    f->spike_filter = sf;

    if (reset_spike) {
        f->spike_idx = 0;
        f->spike_count = 0;
    }

    if (need_buffer_reset) {
        // Плавная инициализация буфера последним известным значением, чтобы не было скачка
        double seed = f->initialized ? f->last_output : 0.0;
        f->index = 0;
        f->count = 0;
        f->sum = 0;
        for (int i = 0; i < FILTER_MAX_SIZE; i++) {
            f->buffer[i] = (int32_t)seed;
        }
        if (f->initialized) {
            f->state_y = seed;
            f->oe_prev_x = seed;
            f->oe_dx = 0.0;
            f->kalman_p = f->kalman_r;
        }
    }

    recalculate_params(f);

    if (f->initialized && biquad_changed) {
        f->bq_z1 = f->last_output * (1.0 - f->bq_b0);
        f->bq_z2 = f->last_output * (f->bq_b2 - f->bq_a2);
    }
}

void filter_set_size(pedal_filter_t *f, uint8_t size) {
    filter_configure(f, f->type, size, f->spike_filter);
}

// Расчет коэффициента сглаживания alpha для заданной частоты среза
static inline double one_euro_alpha(double cutoff_hz) {
    double r = 2.0 * M_PI * cutoff_hz / (double)ADC_SAMPLE_RATE_HZ;
    return r / (r + 1.0);
}

int32_t filter_update(pedal_filter_t *f, int32_t raw_val, int32_t cal_span) {
    int32_t input_val = raw_val;

    // 1. Опциональный пре-фильтр от импульсных выбросов (медиана x3)
    if (f->spike_filter) {
        f->spike_buf[f->spike_idx] = raw_val;
        f->spike_idx = (f->spike_idx + 1) % 3;
        if (f->spike_count < 3) {
            f->spike_count++;
        }
        if (f->spike_count == 3) {
            input_val = median3(f->spike_buf[0], f->spike_buf[1], f->spike_buf[2]);
        }
    }

    // При силе фильтра = 1 основной фильтр выключен (bypass)
    if (f->size <= 1) {
        f->initialized = true;
        f->last_output = (double)input_val;
        f->state_y = f->last_output;
        f->oe_prev_x = f->last_output;
        return input_val;
    }

    double result = (double)input_val;

    switch (f->type) {
        case FILTER_TYPE_SMA: {
            if (f->count < f->size) {
                f->buffer[f->index] = input_val;
                f->sum += input_val;
                f->count++;
                f->index = (f->index + 1) % f->size;
                result = (double)f->sum / (double)f->count;
            } else {
                f->sum -= f->buffer[f->index];
                f->buffer[f->index] = input_val;
                f->sum += input_val;
                f->index = (f->index + 1) % f->size;
                result = (double)f->sum / (double)f->size;
            }
            break;
        }

        case FILTER_TYPE_EMA: {
            double alpha = 2.0 / ((double)f->size + 1.0);
            if (!f->initialized) {
                f->state_y = (double)input_val;
            } else {
                f->state_y = alpha * (double)input_val + (1.0 - alpha) * f->state_y;
            }
            result = f->state_y;
            break;
        }

        case FILTER_TYPE_MEDIAN: {
            uint8_t win = get_median_window_size(f->size);
            f->buffer[f->index] = input_val;
            f->index = (f->index + 1) % win;
            if (f->count < win) {
                f->count++;
            }
            result = (double)calc_median_window(f->buffer, f->count);
            break;
        }

        case FILTER_TYPE_BIQUAD: {
            double x = (double)input_val;
            if (!f->initialized) {
                f->bq_z1 = x * (1.0 - f->bq_b0);
                f->bq_z2 = x * (f->bq_b2 - f->bq_a2);
                result = x;
            } else {
                double y = f->bq_b0 * x + f->bq_z1;
                f->bq_z1 = f->bq_b1 * x - f->bq_a1 * y + f->bq_z2;
                f->bq_z2 = f->bq_b2 * x - f->bq_a2 * y;
                result = y;
            }
            break;
        }

        case FILTER_TYPE_KALMAN: {
            double z = (double)input_val;
            if (!f->initialized) {
                f->state_y = z;
                f->kalman_p = f->kalman_r;
            } else {
                f->kalman_p += f->kalman_q;
                double k = f->kalman_p / (f->kalman_p + f->kalman_r);
                f->state_y = f->state_y + k * (z - f->state_y);
                f->kalman_p = (1.0 - k) * f->kalman_p;
            }
            result = f->state_y;
            break;
        }

        case FILTER_TYPE_ONE_EURO: {
            double x = (double)input_val;
            if (!f->initialized) {
                f->state_y = x;
                f->oe_prev_x = x;
                f->oe_dx = 0.0;
            } else {
                double span = (cal_span > 1000) ? (double)cal_span : 500000.0;
                // Скорость в долях полного рабочего хода педали в секунду
                double dx = ((x - f->oe_prev_x) * (double)ADC_SAMPLE_RATE_HZ) / span;
                f->oe_prev_x = x;

                double a_d = one_euro_alpha(1.0); // Частота среза производной 1.0 Гц
                f->oe_dx = a_d * dx + (1.0 - a_d) * f->oe_dx;

                double cutoff = f->oe_fmin + f->oe_beta * fabs(f->oe_dx);
                if (cutoff > 160.0) cutoff = 160.0;
                double a = one_euro_alpha(cutoff);
                f->state_y = a * x + (1.0 - a) * f->state_y;
            }
            result = f->state_y;
            break;
        }

        default:
            result = (double)input_val;
            break;
    }

    f->initialized = true;
    f->last_output = result;
    return (int32_t)llround(result);
}

// Маппинг сырого значения АЦП в диапазон HID 0..65535 с учетом калибровки, мертвой зоны и кривой
uint16_t map_to_output(int32_t raw_val, int32_t cal_min, int32_t cal_max,
                       uint16_t deadzone, uint8_t curve_type, uint8_t inverted,
                       const uint8_t points[3]) {
    if (cal_max == cal_min) {
        return 0;
    }

    float norm = (float)(raw_val - cal_min) / (float)(cal_max - cal_min);
    if (inverted) {
        norm = 1.0f - norm;
    }

    if (norm < 0.0f) norm = 0.0f;
    if (norm > 1.0f) norm = 1.0f;

    float dz = (float)deadzone / 10000.0f;
    if (dz > 0.95f) dz = 0.95f;

    if (norm <= dz) {
        return 0;
    }

    float effective = (norm - dz) / (1.0f - dz);
    if (effective < 0.0f) effective = 0.0f;
    if (effective > 1.0f) effective = 1.0f;

    float shaped = effective;
    switch (curve_type) {
        case CURVE_PROGRESSIVE:
            shaped = effective * effective;
            break;
        case CURVE_DEGRESSIVE:
            shaped = sqrtf(effective);
            break;
        case CURVE_CUSTOM: {
            float position = effective * 4.0f;
            int segment = (int)position;
            if (segment >= 4) return 65535;
            float lo = segment == 0 ? 0 : points[segment - 1];
            float hi = segment == 3 ? 100 : points[segment];
            shaped = (lo + (hi - lo) * (position - segment)) / 100.0f;
            break;
        }
        case CURVE_LINEAR:
        default:
            shaped = effective;
            break;
    }

    int32_t out = (int32_t)(shaped * 65535.0f + 0.5f);
    if (out < 0) out = 0;
    if (out > 65535) out = 65535;

    return (uint16_t)out;
}
