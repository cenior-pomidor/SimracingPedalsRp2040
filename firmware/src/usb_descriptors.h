#ifndef _USB_DESCRIPTORS_H_
#define _USB_DESCRIPTORS_H_

#include "tusb.h"

#ifdef __cplusplus
extern "C" {
#endif

// Номера интерфейсов USB
enum {
    ITF_NUM_CDC = 0,
    ITF_NUM_CDC_DATA,
    ITF_NUM_HID,
    ITF_NUM_TOTAL
};

// Номера конечных точек
#define EPNUM_CDC_NOTIF   0x81
#define EPNUM_CDC_OUT     0x02
#define EPNUM_CDC_IN      0x82
#define EPNUM_HID         0x83

// Структура HID отчета педали тормоза (16 бит ось, 0..65535)
typedef struct __attribute__((packed)) {
    uint16_t brake;  // Значение тормоза от 0 до 65535
} hid_pedal_report_t;

#ifdef __cplusplus
}
#endif

#endif /* _USB_DESCRIPTORS_H_ */
