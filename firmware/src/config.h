#ifndef _CONFIG_H_
#define _CONFIG_H_

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CONFIG_MAGIC    0xBEEF1234
#define CONFIG_VERSION  2

// Структура конфигурации педали, сохраняемая во Flash память (ровно 32 байта)
typedef struct __attribute__((packed)) {
    uint32_t magic;         // Магическое число 0xBEEF1234
    uint32_t version;       // Версия структуры конфигурации (2)
    int32_t  cal_min;       // Значение АЦП при отпущенной педали
    int32_t  cal_max;       // Значение АЦП при максимально нажатой педали
    uint16_t deadzone;      // Мертвая зона: 0..10000 (0.00%..100.00%)
    uint8_t  filter_size;   // Сила фильтрации: 1..32 (1 = выкл)
    uint8_t  curve_type;    // 0 = линейная, 1 = прогрессивная, 2 = дегрессивная
    uint8_t  inverted;      // 0 = нормальная, 1 = инвертированная
    uint8_t  filter_type;   // 0=SMA, 1=EMA, 2=Median, 3=Biquad, 4=Kalman, 5=OneEuro
    uint8_t  spike_filter;  // 0 = выкл, 1 = пре-фильтр анти-глитч (медиана x3)
    uint8_t  reserved[5];   // Резерв для выравнивания
    uint32_t crc32;         // Контрольная сумма CRC-32
} pedal_config_t;

// Загрузка конфигурации из Flash-памяти
bool config_load(pedal_config_t *cfg);

// Сохранение конфигурации во Flash-память
bool config_save(const pedal_config_t *cfg);

// Установка параметров по умолчанию
void config_set_defaults(pedal_config_t *cfg);

// Расчет CRC32 для структуры конфигурации
uint32_t config_calc_crc(const pedal_config_t *cfg);

#ifdef __cplusplus
}
#endif

#endif /* _CONFIG_H_ */
