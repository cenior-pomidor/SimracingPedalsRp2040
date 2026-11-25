#include "config.h"
#include "filter.h"
#include "pico/stdlib.h"
#include "hardware/flash.h"
#include "hardware/sync.h"
#include <string.h>

// Расположение сектора настроек во Flash-памяти (последний сектор 4КБ)
#define FLASH_TARGET_OFFSET  (PICO_FLASH_SIZE_BYTES - FLASH_SECTOR_SIZE)

// Вычисление CRC-32 (IEEE 802.3)
uint32_t config_calc_crc(const pedal_config_t *cfg) {
    const uint8_t *data = (const uint8_t *)cfg;
    size_t length = sizeof(pedal_config_t) - sizeof(uint32_t);

    uint32_t crc = 0xFFFFFFFF;
    for (size_t i = 0; i < length; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++) {
            if (crc & 1) {
                crc = (crc >> 1) ^ 0xEDB88320;
            } else {
                crc >>= 1;
            }
        }
    }
    return ~crc;
}

// Заполнение настроек значениями по умолчанию
void config_set_defaults(pedal_config_t *cfg) {
    memset(cfg, 0, sizeof(pedal_config_t));
    cfg->magic = CONFIG_MAGIC;
    cfg->version = CONFIG_VERSION;
    cfg->cal_min = 0;
    cfg->cal_max = 500000;
    cfg->deadzone = 100;             // 1.00% мертвая зона
    cfg->filter_size = 8;            // Умеренная фильтрация
    cfg->curve_type = CURVE_LINEAR;  // Линейная кривая
    cfg->inverted = 0;
    cfg->filter_type = FILTER_TYPE_ONE_EURO; // Современный адаптивный 1-Euro фильтр по умолчанию
    cfg->spike_filter = 1;           // Пре-фильтр анти-глитч включен по умолчанию
    cfg->crc32 = config_calc_crc(cfg);
}

// Загрузка настроек из Flash памяти
bool config_load(pedal_config_t *cfg) {
    const uint8_t *flash_ptr = (const uint8_t *)(XIP_BASE + FLASH_TARGET_OFFSET);
    pedal_config_t loaded;
    memcpy(&loaded, flash_ptr, sizeof(pedal_config_t));

    if (loaded.magic != CONFIG_MAGIC || (loaded.version != 1 && loaded.version != CONFIG_VERSION)) {
        config_set_defaults(cfg);
        return false;
    }

    uint32_t calculated_crc = config_calc_crc(&loaded);
    if (calculated_crc != loaded.crc32) {
        config_set_defaults(cfg);
        return false;
    }

    // Мягкая миграция с конфигурации v1 (сохраняет калибровку пользователя)
    if (loaded.version == 1) {
        loaded.version = CONFIG_VERSION;
        loaded.filter_type = FILTER_TYPE_SMA;
        loaded.spike_filter = 0;
        memset(loaded.reserved, 0, sizeof(loaded.reserved));
        loaded.crc32 = config_calc_crc(&loaded);
    }

    if (loaded.filter_size < 1 || loaded.filter_size > FILTER_MAX_SIZE) loaded.filter_size = 8;
    if (loaded.deadzone > 10000) loaded.deadzone = 100;
    if (loaded.curve_type > 2) loaded.curve_type = 0;
    if (loaded.filter_type >= FILTER_TYPE_COUNT) loaded.filter_type = FILTER_TYPE_ONE_EURO;
    loaded.spike_filter = loaded.spike_filter ? 1 : 0;

    memcpy(cfg, &loaded, sizeof(pedal_config_t));
    return true;
}

// Сохранение настроек во Flash память
bool config_save(const pedal_config_t *cfg) {
    pedal_config_t to_write;
    memcpy(&to_write, cfg, sizeof(pedal_config_t));
    to_write.magic = CONFIG_MAGIC;
    to_write.version = CONFIG_VERSION;
    to_write.crc32 = config_calc_crc(&to_write);

    uint8_t page_buf[FLASH_PAGE_SIZE];
    memset(page_buf, 0xFF, sizeof(page_buf));
    memcpy(page_buf, &to_write, sizeof(pedal_config_t));

    uint32_t ints = save_and_disable_interrupts();
    flash_range_erase(FLASH_TARGET_OFFSET, FLASH_SECTOR_SIZE);
    flash_range_program(FLASH_TARGET_OFFSET, page_buf, FLASH_PAGE_SIZE);
    restore_interrupts(ints);

    const uint8_t *flash_ptr = (const uint8_t *)(XIP_BASE + FLASH_TARGET_OFFSET);
    return (memcmp(flash_ptr, &to_write, sizeof(pedal_config_t)) == 0);
}
