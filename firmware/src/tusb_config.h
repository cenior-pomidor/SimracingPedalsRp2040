#ifndef _TUSB_CONFIG_H_
#define _TUSB_CONFIG_H_

#ifdef __cplusplus
extern "C" {
#endif

// Выбор порта контроллера (Pico SDK сам устанавливает OPT_OS_PICO)
#ifndef CFG_TUSB_RHPORT0_MODE
#define CFG_TUSB_RHPORT0_MODE       (OPT_MODE_DEVICE)
#endif

#ifndef CFG_TUSB_OS
#define CFG_TUSB_OS                 (OPT_OS_PICO)
#endif

#ifndef CFG_TUD_ENDPOINT0_SIZE
#define CFG_TUD_ENDPOINT0_SIZE      64
#endif

// Включенные USB классы
#define CFG_TUD_CDC                 (1)  // Виртуальный COM-порт для конфигуратора
#define CFG_TUD_HID                 (1)  // Игровой контроллер HID (ось тормоза)
#define CFG_TUD_MSC                 (0)
#define CFG_TUD_MIDI                (0)
#define CFG_TUD_VENDOR              (0)

// Настройки CDC (Serial)
#define CFG_TUD_CDC_RX_BUFSIZE      (256)
#define CFG_TUD_CDC_TX_BUFSIZE      (256)
#define CFG_TUD_CDC_EP_BUFSIZE      (64)

// Настройки HID
#define CFG_TUD_HID_EP_BUFSIZE      (16)

#ifdef __cplusplus
}
#endif

#endif /* _TUSB_CONFIG_H_ */
