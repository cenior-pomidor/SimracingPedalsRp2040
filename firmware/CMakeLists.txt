cmake_minimum_required(VERSION 3.13)

# Импорт Pico SDK
include(pico_sdk_import.cmake)

project(pedal_brake C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# Инициализация SDK
pico_sdk_init()

# Создание исполняемого файла прошивки
add_executable(pedal_brake
    src/main.c
    src/usb_descriptors.c
    src/ads1220.c
    src/filter.c
    src/config.c
    src/protocol.c
)

# Каталоги заголовков
target_include_directories(pedal_brake PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/src
)

# Подключение библиотек Pico SDK и аппаратных модулей
target_link_libraries(pedal_brake
    pico_stdlib
    hardware_spi
    hardware_flash
    hardware_sync
    tinyusb_device
    tinyusb_board
)

# Отключаем встроенный stdio вывод в USB и UART, так как используем кастомный TinyUSB стек CDC+HID
pico_enable_stdio_usb(pedal_brake 0)
pico_enable_stdio_uart(pedal_brake 0)

# Генерация выходных файлов (.uf2, .hex, .bin, .map)
pico_add_extra_outputs(pedal_brake)
