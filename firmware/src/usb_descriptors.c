#include "usb_descriptors.h"

// =============================================================================
// USB Device Descriptor
// =============================================================================
tusb_desc_device_t const desc_device = {
    .bLength            = sizeof(tusb_desc_device_t),
    .bDescriptorType    = TUSB_DESC_DEVICE,
    .bcdUSB             = 0x0200,

    // Использование Miscellaneous/IAD для составного устройства (CDC + HID)
    .bDeviceClass       = TUSB_CLASS_MISC,
    .bDeviceSubClass    = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol    = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,

    .idVendor           = 0x1209, // pid.codes open-source VID
    .idProduct          = 0xBEEF, // SimPedal Brake PID
    .bcdDevice          = 0x0100,

    .iManufacturer      = 0x01,
    .iProduct           = 0x02,
    .iSerialNumber      = 0x03,

    .bNumConfigurations = 0x01
};

uint8_t const * tud_descriptor_device_cb(void) {
    return (uint8_t const *) &desc_device;
}

// =============================================================================
// HID Report Descriptor: Игровой контроллер с осью тормоза (Simulation Controls -> Brake)
// =============================================================================
uint8_t const desc_hid_report[] = {
    0x05, 0x01,                // Usage Page (Generic Desktop Controls)
    0x09, 0x04,                // Usage (Joystick)
    0xA1, 0x01,                // Collection (Application)
        0x05, 0x02,            //   Usage Page (Simulation Controls)
        0x09, 0xC5,            //   Usage (Brake)
        0x15, 0x00,            //   Logical Minimum (0)
        0x27, 0xFF, 0xFF, 0x00, 0x00, // Logical Maximum (65535)
        0x75, 0x10,            //   Report Size (16 bits)
        0x95, 0x01,            //   Report Count (1)
        0x81, 0x02,            //   Input (Data, Var, Abs)
    0xC0                       // End Collection
};

uint8_t const * tud_hid_descriptor_report_cb(uint8_t instance) {
    (void) instance;
    return desc_hid_report;
}

// =============================================================================
// Configuration Descriptor: Composite CDC + HID
// =============================================================================
#define CONFIG_TOTAL_LEN  (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_HID_DESC_LEN)

uint8_t const desc_configuration[] = {
    // Config number, interface count, string index, total length, attribute, power in mA
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 100),

    // 1. CDC Interface (2 endpoints: Control + Data)
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 4, EPNUM_CDC_NOTIF, 8, EPNUM_CDC_OUT, EPNUM_CDC_IN, 64),

    // 2. HID Interface (1 IN endpoint, интервал 1 мс = 1000 Гц опрос)
    TUD_HID_DESCRIPTOR(ITF_NUM_HID, 5, HID_ITF_PROTOCOL_NONE, sizeof(desc_hid_report), EPNUM_HID, CFG_TUD_HID_EP_BUFSIZE, 1)
};

uint8_t const * tud_descriptor_configuration_cb(uint8_t index) {
    (void) index;
    return desc_configuration;
}

// =============================================================================
// String Descriptors
// =============================================================================
char const* string_desc_arr[] = {
    (const char[]) { 0x09, 0x04 }, // 0: Поддерживаемый язык (English 0x0409)
    "OpenSimHW",                   // 1: Производитель
    "SimPedal Brake",              // 2: Название продукта
    "SP2040-001",                  // 3: Серийный номер
    "SimPedal Config CDC",         // 4: Интерфейс CDC
    "SimPedal Brake HID"           // 5: Интерфейс HID
};

static uint16_t _desc_str[32];

uint16_t const* tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void) langid;
    uint8_t chr_count;

    if (index == 0) {
        memcpy(&_desc_str[1], string_desc_arr[0], 2);
        chr_count = 1;
    } else {
        if (index >= sizeof(string_desc_arr)/sizeof(string_desc_arr[0])) return NULL;

        const char* str = string_desc_arr[index];
        chr_count = (uint8_t) strlen(str);
        if (chr_count > 31) chr_count = 31;

        for (uint8_t i = 0; i < chr_count; i++) {
            _desc_str[1 + i] = str[i];
        }
    }

    _desc_str[0] = (TUSB_DESC_STRING << 8) | (2 * chr_count + 2);
    return _desc_str;
}

// Заглушки обратных вызовов HID (GET / SET Report)
uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type, uint8_t* buffer, uint16_t reqlen) {
    (void) instance;
    (void) report_id;
    (void) report_type;
    (void) buffer;
    (void) reqlen;
    return 0;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type, uint8_t const* buffer, uint16_t bufsize) {
    (void) instance;
    (void) report_id;
    (void) report_type;
    (void) buffer;
    (void) bufsize;
}
