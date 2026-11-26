// cc -std=c11 -Ifirmware/src tests/test_filter.c firmware/src/filter.c -lm -o /tmp/test_filter
#include <assert.h>
#include "filter.h"

int main(void) {
    pedal_filter_t filter;
    filter_init(&filter, FILTER_TYPE_BIQUAD, 1, 0);
    assert(filter_update(&filter, 250000, 500000) == 250000);
    const uint8_t sizes[] = {32, 2, 1, 16};
    for (unsigned i = 0; i < sizeof(sizes); i++) {
        filter_set_size(&filter, sizes[i]);
        for (int j = 0; j < 100; j++) {
            assert(filter_update(&filter, 250000, 500000) == 250000);
        }
    }

    const uint8_t points[] = {5, 30, 90};
    assert(map_to_output(-1, 0, 1000, 0, CURVE_CUSTOM, 0, points) == 0);
    assert(map_to_output(1001, 0, 1000, 0, CURVE_CUSTOM, 0, points) == 65535);
    assert(map_to_output(0, 0, 1000, 0, CURVE_CUSTOM, 1, points) == 65535);
    assert(map_to_output(100, 0, 1000, 1000, CURVE_CUSTOM, 0, points) == 0);
    assert(map_to_output(550, 0, 1000, 1000, CURVE_CUSTOM, 0, points) == 19661);
    assert(map_to_output(450, 1000, 0, 1000, CURVE_CUSTOM, 0, points) == 19661);
    int previous = 0;
    for (int i = 0; i <= 1000; i++) {
        int output = map_to_output(i, 0, 1000, 1000, CURVE_CUSTOM, 0, points);
        assert(output >= previous);
        previous = output;
    }
    return 0;
}
