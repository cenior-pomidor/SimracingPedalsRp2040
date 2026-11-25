#include "protocol.h"
#include "tusb.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#define RX_BUFFER_SIZE  256
#define TX_BUFFER_SIZE  320

static char rx_line[RX_BUFFER_SIZE];
static uint16_t rx_index = 0;
bool protocol_stream_active = false;
static uint32_t last_stream_time_ms = 0;

// Отправка строки в виртуальный COM-порт
static void cdc_send_string(const char *str) {
    if (!tud_cdc_connected()) return;
    uint32_t len = (uint32_t)strlen(str);
    tud_cdc_write(str, len);
    tud_cdc_write_flush();
}

void protocol_init(void) {
    rx_index = 0;
    protocol_stream_active = false;
    last_stream_time_ms = 0;
}

static void parse_command(char *cmd, int32_t unfiltered_raw, int32_t filtered_raw,
                          uint16_t current_force, pedal_config_t *cfg, pedal_filter_t *flt) {
    char tx_buf[TX_BUFFER_SIZE];

    size_t len = strlen(cmd);
    while (len > 0 && (cmd[len - 1] == '\r' || cmd[len - 1] == '\n' || cmd[len - 1] == ' ')) {
        cmd[--len] = '\0';
    }
    if (len == 0) return;

    // CMD:PING
    if (strcmp(cmd, "CMD:PING") == 0) {
        cdc_send_string("RSP:PONG:SimPedal:v1.1\r\n");
    }
    // CMD:GET_RAW
    else if (strcmp(cmd, "CMD:GET_RAW") == 0) {
        snprintf(tx_buf, sizeof(tx_buf), "RSP:RAW:%ld\r\n", (long)filtered_raw);
        cdc_send_string(tx_buf);
    }
    // CMD:GET_FORCE
    else if (strcmp(cmd, "CMD:GET_FORCE") == 0) {
        snprintf(tx_buf, sizeof(tx_buf), "RSP:FORCE:%u\r\n", (unsigned int)current_force);
        cdc_send_string(tx_buf);
    }
    // CMD:GET_CONFIG
    else if (strcmp(cmd, "CMD:GET_CONFIG") == 0) {
        snprintf(tx_buf, sizeof(tx_buf),
                 "RSP:CONFIG:{\"cal_min\":%ld,\"cal_max\":%ld,\"deadzone\":%u,"
                 "\"filter_size\":%u,\"filter_type\":%u,\"spike_filter\":%u,"
                 "\"curve_type\":%u,\"inverted\":%u,\"sample_rate\":330,\"version\":%u}\r\n",
                 (long)cfg->cal_min,
                 (long)cfg->cal_max,
                 (unsigned int)cfg->deadzone,
                 (unsigned int)cfg->filter_size,
                 (unsigned int)cfg->filter_type,
                 (unsigned int)cfg->spike_filter,
                 (unsigned int)cfg->curve_type,
                 (unsigned int)cfg->inverted,
                 (unsigned int)cfg->version);
        cdc_send_string(tx_buf);
    }
    // CMD:CAL_MIN (калибровка минимума по сглаженному значению)
    else if (strcmp(cmd, "CMD:CAL_MIN") == 0) {
        cfg->cal_min = filtered_raw;
        snprintf(tx_buf, sizeof(tx_buf), "RSP:OK:CAL_MIN:%ld\r\n", (long)cfg->cal_min);
        cdc_send_string(tx_buf);
    }
    // CMD:CAL_MAX (калибровка максимума по сглаженному значению)
    else if (strcmp(cmd, "CMD:CAL_MAX") == 0) {
        cfg->cal_max = filtered_raw;
        snprintf(tx_buf, sizeof(tx_buf), "RSP:OK:CAL_MAX:%ld\r\n", (long)cfg->cal_max);
        cdc_send_string(tx_buf);
    }
    // CMD:RESET_CAL
    else if (strcmp(cmd, "CMD:RESET_CAL") == 0) {
        cfg->cal_min = 0;
        cfg->cal_max = 500000;
        cdc_send_string("RSP:OK:RESET\r\n");
    }
    // CMD:SET_DEADZONE:<value> (0..10000)
    else if (strncmp(cmd, "CMD:SET_DEADZONE:", 17) == 0) {
        int dz = atoi(cmd + 17);
        if (dz < 0) dz = 0;
        if (dz > 10000) dz = 10000;
        cfg->deadzone = (uint16_t)dz;
        snprintf(tx_buf, sizeof(tx_buf), "RSP:OK:DEADZONE:%u\r\n", cfg->deadzone);
        cdc_send_string(tx_buf);
    }
    // CMD:SET_FILTER:<value> (1..32)
    else if (strncmp(cmd, "CMD:SET_FILTER:", 15) == 0) {
        int fl = atoi(cmd + 15);
        if (fl < 1) fl = 1;
        if (fl > FILTER_MAX_SIZE) fl = FILTER_MAX_SIZE;
        cfg->filter_size = (uint8_t)fl;
        filter_configure(flt, cfg->filter_type, cfg->filter_size, cfg->spike_filter);
        snprintf(tx_buf, sizeof(tx_buf), "RSP:OK:FILTER:%u\r\n", cfg->filter_size);
        cdc_send_string(tx_buf);
    }
    // CMD:SET_FILTER_TYPE:<value> (0..5)
    else if (strncmp(cmd, "CMD:SET_FILTER_TYPE:", 20) == 0) {
        int ft = atoi(cmd + 20);
        if (ft < 0) ft = 0;
        if (ft >= FILTER_TYPE_COUNT) ft = FILTER_TYPE_SMA;
        cfg->filter_type = (uint8_t)ft;
        filter_configure(flt, cfg->filter_type, cfg->filter_size, cfg->spike_filter);
        snprintf(tx_buf, sizeof(tx_buf), "RSP:OK:FILTER_TYPE:%u\r\n", cfg->filter_type);
        cdc_send_string(tx_buf);
    }
    // CMD:SET_SPIKE_FILTER:<0|1>
    else if (strncmp(cmd, "CMD:SET_SPIKE_FILTER:", 21) == 0) {
        int sf = atoi(cmd + 21);
        cfg->spike_filter = (sf != 0) ? 1 : 0;
        filter_configure(flt, cfg->filter_type, cfg->filter_size, cfg->spike_filter);
        snprintf(tx_buf, sizeof(tx_buf), "RSP:OK:SPIKE_FILTER:%u\r\n", cfg->spike_filter);
        cdc_send_string(tx_buf);
    }
    // CMD:SET_CURVE:<value> (0..2)
    else if (strncmp(cmd, "CMD:SET_CURVE:", 14) == 0) {
        int cv = atoi(cmd + 14);
        if (cv < 0) cv = 0;
        if (cv > 2) cv = 2;
        cfg->curve_type = (uint8_t)cv;
        snprintf(tx_buf, sizeof(tx_buf), "RSP:OK:CURVE:%u\r\n", cfg->curve_type);
        cdc_send_string(tx_buf);
    }
    // CMD:SET_INVERT:<value> (0 или 1)
    else if (strncmp(cmd, "CMD:SET_INVERT:", 15) == 0) {
        int inv = atoi(cmd + 15);
        cfg->inverted = (inv != 0) ? 1 : 0;
        snprintf(tx_buf, sizeof(tx_buf), "RSP:OK:INVERT:%u\r\n", cfg->inverted);
        cdc_send_string(tx_buf);
    }
    // CMD:SAVE
    else if (strcmp(cmd, "CMD:SAVE") == 0) {
        if (config_save(cfg)) {
            cdc_send_string("RSP:OK:SAVED\r\n");
        } else {
            cdc_send_string("RSP:ERR:SAVE_FAILED\r\n");
        }
    }
    // CMD:LOAD
    else if (strcmp(cmd, "CMD:LOAD") == 0) {
        if (config_load(cfg)) {
            filter_configure(flt, cfg->filter_type, cfg->filter_size, cfg->spike_filter);
            cdc_send_string("RSP:OK:LOADED\r\n");
        } else {
            cdc_send_string("RSP:ERR:LOAD_FAILED\r\n");
        }
    }
    // CMD:STREAM_ON
    else if (strcmp(cmd, "CMD:STREAM_ON") == 0) {
        protocol_stream_active = true;
    }
    // CMD:STREAM_OFF
    else if (strcmp(cmd, "CMD:STREAM_OFF") == 0) {
        protocol_stream_active = false;
    }
    else {
        snprintf(tx_buf, sizeof(tx_buf), "RSP:ERR:UNKNOWN_CMD\r\n");
        cdc_send_string(tx_buf);
    }

    (void)unfiltered_raw;
}

void protocol_task(int32_t unfiltered_raw, int32_t filtered_raw, uint16_t current_force,
                   pedal_config_t *cfg, pedal_filter_t *flt) {
    // 1. Обработка входящих команд CDC
    while (tud_cdc_available()) {
        char ch = (char)tud_cdc_read_char();
        if (ch == '\n' || ch == '\r') {
            if (rx_index > 0) {
                rx_line[rx_index] = '\0';
                parse_command(rx_line, unfiltered_raw, filtered_raw, current_force, cfg, flt);
                rx_index = 0;
            }
        } else {
            if (rx_index < RX_BUFFER_SIZE - 1) {
                rx_line[rx_index++] = ch;
            } else {
                rx_index = 0;
            }
        }
    }

    // 2. Потоковая передача телеметрии ~100 Гц: DAT:<filtered_raw>,<force>,<unfiltered_raw>
    if (protocol_stream_active && tud_cdc_connected()) {
        uint32_t now_ms = to_ms_since_boot(get_absolute_time());
        if (now_ms - last_stream_time_ms >= 10) {
            last_stream_time_ms = now_ms;
            char stream_buf[80];
            int n = snprintf(stream_buf, sizeof(stream_buf), "DAT:%ld,%u,%ld\r\n",
                             (long)filtered_raw, (unsigned int)current_force, (long)unfiltered_raw);
            if (n > 0 && tud_cdc_write_available() >= (uint32_t)n) {
                tud_cdc_write(stream_buf, (uint32_t)n);
                tud_cdc_write_flush();
            }
        }
    }
}
