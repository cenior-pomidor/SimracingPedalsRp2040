#ifndef _FILTER_H_
#define _FILTER_H_

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FILTER_MAX_SIZE       32
#define FILTER_MEDIAN_MAX     15
#define ADC_SAMPLE_RATE_HZ    330.0f

// Типы кривых отклика педали
typedef enum {
    CURVE_LINEAR = 0,       // Линейная зависимость
    CURVE_PROGRESSIVE = 1,  // Прогрессивная (квадратичная, мягкое начало)
    CURVE_DEGRESSIVE = 2    // Дегрессивная (корень квадратный, быстрый начальный отклик)
} pedal_curve_t;

// Типы фильтров очистки сигнала от шума
typedef enum {
    FILTER_TYPE_SMA = 0,        // Скользящее среднее (Moving Average)
    FILTER_TYPE_EMA = 1,        // Экспоненциальный ФНЧ 1-го порядка (Low-Pass EMA)
    FILTER_TYPE_MEDIAN = 2,     // Медианный фильтр (подавление импульсных выбросов)
    FILTER_TYPE_BIQUAD = 3,     // Фильтр Баттерворта 2-го порядка (спад -40 дБ/дек)
    FILTER_TYPE_KALMAN = 4,     // Одномерный фильтр Калмана (1D Kalman)
    FILTER_TYPE_ONE_EURO = 5,   // Адаптивный 1-Euro фильтр (мин. шум в покое / мин. задержка в движении)
    FILTER_TYPE_COUNT
} filter_type_t;

// Комплексная структура фильтрации сигнала педали
typedef struct {
    // Настройки
    uint8_t type;          // filter_type_t (0..5)
    uint8_t size;          // Сила фильтрации (1..32, где 1 = без фильтра)
    uint8_t spike_filter;  // 0 = выкл, 1 = пре-фильтр медиана x3 против импульсных выбросов

    // Состояние пре-фильтра анти-глитч (медиана x3)
    int32_t spike_buf[3];
    uint8_t spike_idx;
    uint8_t spike_count;

    // Буфер для SMA и Медианного фильтра
    int32_t buffer[FILTER_MAX_SIZE];
    uint8_t index;
    uint8_t count;
    int64_t sum;

    // Состояние EMA / Kalman / One-Euro
    bool    initialized;
    double  state_y;
    double  last_output;

    // Состояние Biquad (Direct Form II Transposed)
    double  bq_b0, bq_b1, bq_b2;
    double  bq_a1, bq_a2;
    double  bq_z1, bq_z2;

    // Состояние Kalman 1D
    double  kalman_p;
    double  kalman_q;
    double  kalman_r;

    // Состояние One-Euro
    double  oe_prev_x;
    double  oe_dx;
    double  oe_fmin;
    double  oe_beta;
} pedal_filter_t;

// Инициализация фильтра с заданными типом, силой и пре-фильтром анти-глитч
void filter_init(pedal_filter_t *f, uint8_t type, uint8_t size, uint8_t spike_filter);

// Сброс накопленного состояния фильтра
void filter_reset(pedal_filter_t *f);

// Изменение параметров фильтра без резкого сброса выходного значения
void filter_configure(pedal_filter_t *f, uint8_t type, uint8_t size, uint8_t spike_filter);

// Обратная совместимость: изменение размера фильтра
void filter_set_size(pedal_filter_t *f, uint8_t size);

// Добавление нового отсчета АЦП и вычисление отфильтрованного значения
// cal_span — рабочий размах калибровки |cal_max - cal_min| (для адаптивного 1-Euro фильтра)
int32_t filter_update(pedal_filter_t *f, int32_t raw_val, int32_t cal_span);

// Маппинг значения АЦП в диапазон HID 0..65535 с учетом калибровки, мертвой зоны и кривой
uint16_t map_to_output(int32_t raw_val, int32_t cal_min, int32_t cal_max,
                       uint16_t deadzone, uint8_t curve_type, uint8_t inverted);

#ifdef __cplusplus
}
#endif

#endif /* _FILTER_H_ */
