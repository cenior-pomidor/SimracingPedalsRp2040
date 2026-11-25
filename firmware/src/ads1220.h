#ifndef _ADS1220_H_
#define _ADS1220_H_

#include "pico/stdlib.h"
#include "hardware/spi.h"
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Назначение выводов RP2040-Zero для связи с модулем ADS1220
#define ADS1220_SPI_PORT    spi0
#define ADS1220_PIN_SCK     2   // GP2: SPI0 SCK
#define ADS1220_PIN_MOSI    3   // GP3: SPI0 TX
#define ADS1220_PIN_MISO    4   // GP4: SPI0 RX
#define ADS1220_PIN_CS      5   // GP5: Chip Select (активный 0)
#define ADS1220_PIN_DRDY    6   // GP6: Data Ready (активный 0)

// Команды управления ADS1220
#define ADS1220_CMD_RESET      0x06  // Сброс устройства
#define ADS1220_CMD_START      0x08  // Запуск/синхронизация преобразований
#define ADS1220_CMD_POWERDOWN  0x02  // Переход в режим пониженного энергопотребления
#define ADS1220_CMD_RDATA      0x10  // Чтение данных по SPI

// Макросы генерации команд чтения/записи регистров (0..3)
#define ADS1220_CMD_RREG(reg, count) (0x20 | (((reg) & 0x03) << 2) | (((count) - 1) & 0x03))
#define ADS1220_CMD_WREG(reg, count) (0x40 | (((reg) & 0x03) << 2) | (((count) - 1) & 0x03))

// Настройки регистров по умолчанию для 100 кг тензодатчика (полный мост)
// Регистр 0: MUX = AIN0-AIN1 (дифференциальный), GAIN = 128, PGA = включен (0x0E)
#define ADS1220_REG0_CONFIG    0x0E

// Регистр 1: DR = 330 SPS, Normal mode, Continuous conversion (0x84)
#define ADS1220_REG1_CONFIG    0x84

// Регистр 2: VREF = AVDD/AGND (ратиометрический режим), FIR = 50/60Hz, PSW = open, IDAC = off (0xD0)
#define ADS1220_REG2_CONFIG    0xD0

// Регистр 3: IDAC отключен, DRDY на выделенном выводе (0x00)
#define ADS1220_REG3_CONFIG    0x00

// Структура драйвера ADS1220
typedef struct {
    spi_inst_t *spi;
    uint pin_cs;
    uint pin_drdy;
    uint8_t reg_shadow[4];
} ads1220_t;

// Инициализация периферии SPI и конфигурация ADS1220
bool ads1220_init(ads1220_t *dev);

// Аппаратный/программный сброс АЦП
void ads1220_reset(ads1220_t *dev);

// Запись одного регистра
void ads1220_write_reg(ads1220_t *dev, uint8_t reg, uint8_t value);

// Чтение одного регистра
uint8_t ads1220_read_reg(ads1220_t *dev, uint8_t reg);

// Отправка команды START / SYNC
void ads1220_start(ads1220_t *dev);

// Проверка готовности данных (линия DRDY в нуле)
bool ads1220_data_ready(ads1220_t *dev);

// Чтение 24-битного результата (возвращает 32-битное число со знаком)
bool ads1220_read_data(ads1220_t *dev, int32_t *raw_value);

// Блокирующее ожидание и чтение очередного отсчета
bool ads1220_read_data_blocking(ads1220_t *dev, int32_t *raw_value, uint32_t timeout_ms);

#ifdef __cplusplus
}
#endif

#endif /* _ADS1220_H_ */
