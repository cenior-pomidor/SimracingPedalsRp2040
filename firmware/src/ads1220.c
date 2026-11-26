#include "ads1220.h"

// Вспомогательные функции управления CS
static inline void ads1220_cs_select(ads1220_t *dev) {
    asm volatile("nop \n nop \n nop");
    gpio_put(dev->pin_cs, 0);
    asm volatile("nop \n nop \n nop");
}

static inline void ads1220_cs_deselect(ads1220_t *dev) {
    asm volatile("nop \n nop \n nop");
    gpio_put(dev->pin_cs, 1);
    asm volatile("nop \n nop \n nop");
}

// Инициализация модуля ADS1220
bool ads1220_init(ads1220_t *dev) {
    dev->spi = ADS1220_SPI_PORT;
    dev->pin_cs = ADS1220_PIN_CS;
    dev->pin_drdy = ADS1220_PIN_DRDY;

    // Инициализация шины SPI: 1 МГц, режим SPI 1 (CPOL=0, CPHA=1), 8 бит данных
    spi_init(dev->spi, 1000 * 1000);
    spi_set_format(dev->spi, 8, SPI_CPOL_0, SPI_CPHA_1, SPI_MSB_FIRST);

    // Конфигурация пинов GPIO для SPI
    gpio_set_function(ADS1220_PIN_SCK, GPIO_FUNC_SPI);
    gpio_set_function(ADS1220_PIN_MOSI, GPIO_FUNC_SPI);
    gpio_set_function(ADS1220_PIN_MISO, GPIO_FUNC_SPI);

    // Настройка вывода CS (активный низкий)
    gpio_init(dev->pin_cs);
    gpio_set_dir(dev->pin_cs, GPIO_OUT);
    gpio_put(dev->pin_cs, 1);

    // Настройка вывода DRDY (вход с подтяжкой к питанию)
    gpio_init(dev->pin_drdy);
    gpio_set_dir(dev->pin_drdy, GPIO_IN);
    gpio_pull_up(dev->pin_drdy);

    // Пауза перед сбросом для стабилизации аналогового питания
    sleep_ms(50);

    // Программный сброс микросхемы
    ads1220_reset(dev);
    sleep_ms(20);

    // Запись конфигурации регистров
    ads1220_write_reg(dev, 0, ADS1220_REG0_CONFIG);
    ads1220_write_reg(dev, 1, ADS1220_REG1_CONFIG);
    ads1220_write_reg(dev, 2, ADS1220_REG2_CONFIG);
    ads1220_write_reg(dev, 3, ADS1220_REG3_CONFIG);

    for (uint8_t reg = 0; reg < 4; reg++) {
        if (ads1220_read_reg(dev, reg) != dev->reg_shadow[reg]) return false;
    }

    // Запуск непрерывного преобразования
    ads1220_start(dev);
    return true;
}

// Сброс ADS1220
void ads1220_reset(ads1220_t *dev) {
    uint8_t cmd = ADS1220_CMD_RESET;
    ads1220_cs_select(dev);
    spi_write_blocking(dev->spi, &cmd, 1);
    ads1220_cs_deselect(dev);
    sleep_us(100);
}

// Запись одного регистра
void ads1220_write_reg(ads1220_t *dev, uint8_t reg, uint8_t value) {
    uint8_t buf[2];
    buf[0] = ADS1220_CMD_WREG(reg, 1);
    buf[1] = value;

    ads1220_cs_select(dev);
    spi_write_blocking(dev->spi, buf, 2);
    ads1220_cs_deselect(dev);

    dev->reg_shadow[reg & 0x03] = value;
    sleep_us(10);
}

// Чтение одного регистра
uint8_t ads1220_read_reg(ads1220_t *dev, uint8_t reg) {
    uint8_t cmd = ADS1220_CMD_RREG(reg, 1);
    uint8_t val = 0;

    ads1220_cs_select(dev);
    spi_write_blocking(dev->spi, &cmd, 1);
    spi_read_blocking(dev->spi, 0x00, &val, 1);
    ads1220_cs_deselect(dev);

    return val;
}

// Запуск измерений
void ads1220_start(ads1220_t *dev) {
    uint8_t cmd = ADS1220_CMD_START;
    ads1220_cs_select(dev);
    spi_write_blocking(dev->spi, &cmd, 1);
    ads1220_cs_deselect(dev);
    sleep_us(10);
}

// Проверка линии DRDY (в continuous mode низкий уровень означает готовый отсчет)
bool ads1220_data_ready(ads1220_t *dev) {
    return (gpio_get(dev->pin_drdy) == 0);
}

// Чтение 24-битного значения
bool ads1220_read_data(ads1220_t *dev, int32_t *raw_value) {
    uint8_t cmd = ADS1220_CMD_RDATA;
    uint8_t rx[3] = {0, 0, 0};

    ads1220_cs_select(dev);
    spi_write_blocking(dev->spi, &cmd, 1);
    spi_read_blocking(dev->spi, 0x00, rx, 3);
    ads1220_cs_deselect(dev);

    // Сборка 24-битного числа
    int32_t val = ((int32_t)rx[0] << 16) | ((int32_t)rx[1] << 8) | rx[2];

    // Расширение знака из 24 бит в 32 бита
    if (val & 0x00800000) {
        val |= 0xFF000000;
    }

    *raw_value = val;
    return true;
}

// Блокирующее чтение с таймаутом
bool ads1220_read_data_blocking(ads1220_t *dev, int32_t *raw_value, uint32_t timeout_ms) {
    absolute_time_t deadline = make_timeout_time_ms(timeout_ms);
    while (!ads1220_data_ready(dev)) {
        if (time_reached(deadline)) {
            return false;
        }
        tight_loop_contents();
    }
    return ads1220_read_data(dev, raw_value);
}
