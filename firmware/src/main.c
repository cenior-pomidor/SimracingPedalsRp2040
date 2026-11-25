#include "pico/stdlib.h"
#include "bsp/board.h"
#include "tusb.h"

#include "usb_descriptors.h"
#include "ads1220.h"
#include "filter.h"
#include "config.h"
#include "protocol.h"

// Глобальные структуры системы
static ads1220_t adc_dev;
static pedal_config_t config;
static pedal_filter_t filter;

// Текущие измерения
static int32_t raw_adc_value = 0;
static int32_t filtered_adc_value = 0;
static uint16_t current_brake_force = 0;

// Таймер для USB HID отчетов (1000 Гц = 1 мс)
static uint32_t last_hid_send_us = 0;

int main(void) {
    // Инициализация платформы RP2040
    board_init();

    // Инициализация USB стека TinyUSB
    tusb_init();

    // Загрузка конфигурации из Flash-памяти
    if (!config_load(&config)) {
        config_set_defaults(&config);
    }

    // Инициализация системы фильтрации
    filter_init(&filter, config.filter_type, config.filter_size, config.spike_filter);

    // Инициализация протокола управления
    protocol_init();

    // Инициализация АЦП ADS1220
    bool adc_ok = ads1220_init(&adc_dev);
    (void)adc_ok;

    last_hid_send_us = time_us_32();

    while (1) {
        // 1. Фоновые задачи USB стека TinyUSB
        tud_task();

        // 2. Опрос АЦП при готовности нового измерения
        if (ads1220_data_ready(&adc_dev)) {
            int32_t new_sample = 0;
            if (ads1220_read_data(&adc_dev, &new_sample)) {
                raw_adc_value = new_sample;
                int32_t cal_span = config.cal_max - config.cal_min;
                if (cal_span < 0) cal_span = -cal_span;

                // Применение выбранного цифрового фильтра очистки от шума
                filtered_adc_value = filter_update(&filter, raw_adc_value, cal_span);

                // Маппинг в выходную ось тормоза 0..65535
                current_brake_force = map_to_output(
                    filtered_adc_value,
                    config.cal_min,
                    config.cal_max,
                    config.deadzone,
                    config.curve_type,
                    config.inverted
                );
            }
        }

        // 3. Отправка отчета HID с частотой 1000 Гц (каждые 1 мс)
        uint32_t now_us = time_us_32();
        if ((now_us - last_hid_send_us) >= 1000) {
            last_hid_send_us = now_us;

            if (tud_hid_ready()) {
                hid_pedal_report_t report = {
                    .brake = current_brake_force
                };
                tud_hid_report(0, &report, sizeof(report));
            }
        }

        // 4. Обработка команд протокола через виртуальный COM-порт и передача телеметрии
        protocol_task(raw_adc_value, filtered_adc_value, current_brake_force, &config, &filter);
    }

    return 0;
}
