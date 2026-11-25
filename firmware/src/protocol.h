#ifndef _PROTOCOL_H_
#define _PROTOCOL_H_

#include <stdint.h>
#include <stdbool.h>
#include "config.h"
#include "filter.h"

#ifdef __cplusplus
extern "C" {
#endif

// Инициализация модуля протокола
void protocol_init(void);

// Периодическая обработка команд CDC и потоковая отправка телеметрии
void protocol_task(int32_t unfiltered_raw, int32_t filtered_raw, uint16_t current_force,
                   pedal_config_t *cfg, pedal_filter_t *flt);

// Флаг активной потоковой передачи данных
extern bool protocol_stream_active;

#ifdef __cplusplus
}
#endif

#endif /* _PROTOCOL_H_ */
