# Standard pico_sdk_import.cmake for Raspberry Pi Pico SDK
# This can be used in projects outside of the SDK directory.

if (DEFINED ENV{PICO_SDK_PATH} AND (NOT PICO_SDK_PATH))
    set(PICO_SDK_PATH $ENV{PICO_SDK_PATH})
    message("Using PICO_SDK_PATH from environment ('${PICO_SDK_PATH}')")
endif ()

if (DEFINED PICO_SDK_PATH)
    # Check that directory exists
    if (NOT EXISTS "${PICO_SDK_PATH}")
        message(FATAL_ERROR "Directory specified by PICO_SDK_PATH '${PICO_SDK_PATH}' does not exist")
    endif ()
    set(PICO_SDK_PATH "${PICO_SDK_PATH}" CACHE PATH "Path to the Raspberry Pi Pico SDK")
else ()
    # Check parent and current directories
    if (EXISTS "${CMAKE_CURRENT_LIST_DIR}/pico-sdk")
        set(PICO_SDK_PATH "${CMAKE_CURRENT_LIST_DIR}/pico-sdk" CACHE PATH "Path to the Raspberry Pi Pico SDK")
    elseif (EXISTS "${CMAKE_CURRENT_LIST_DIR}/../pico-sdk")
        set(PICO_SDK_PATH "${CMAKE_CURRENT_LIST_DIR}/../pico-sdk" CACHE PATH "Path to the Raspberry Pi Pico SDK")
    else ()
        message(FATAL_ERROR "PICO_SDK_PATH is not defined. Please set PICO_SDK_PATH environment variable or specify -DPICO_SDK_PATH=...")
    endif ()
endif ()

get_filename_component(PICO_SDK_PATH "${PICO_SDK_PATH}" REALPATH BASE_DIR "${CMAKE_BINARY_DIR}")
if (NOT EXISTS "${PICO_SDK_PATH}")
    message(FATAL_ERROR "Directory specified by PICO_SDK_PATH '${PICO_SDK_PATH}' does not exist")
endif ()

set(PICO_SDK_INIT_CMAKE_FILE "${PICO_SDK_PATH}/pico_sdk_init.cmake")
if (NOT EXISTS "${PICO_SDK_INIT_CMAKE_FILE}")
    message(FATAL_ERROR "Directory '${PICO_SDK_PATH}' does not appear to contain the Raspberry Pi Pico SDK")
endif ()

include(${PICO_SDK_INIT_CMAKE_FILE})
