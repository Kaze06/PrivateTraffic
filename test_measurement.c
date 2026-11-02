#include "measurement.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#define W 80
#define H 80

static void frame_with_object(unsigned char *frame, int y) {
    int x, row;
    memset(frame, 20, W * H);
    if (y < 0) return;
    for (row = y; row < y + 8; ++row)
        for (x = 35; x < 45; ++x) frame[row * W + x] = 220;
}

int main(void) {
    PTProcessor p;
    PTMeasurement m;
    PTConfig c = {0, 0, W, H, 40, 35, 24, 20, 3, 1, 3, "road-demo"};
    unsigned char frame[W * H];
    char json[1024];
    int i;
    assert(pt_init(&p, W, H, &c) == 0);
    frame_with_object(frame, -1);
    assert(pt_process_frame(&p, frame, sizeof(frame), 0, &m) == 0);
    for (i = 1; i <= 10; ++i) {
        frame_with_object(frame, 15 + i * 4);
        assert(pt_process_frame(&p, frame, sizeof(frame), (uint64_t)i * 1000, &m) == 0);
    }
    assert(p.window_count == 1); /* Same track crossed once. */
    assert(pt_process_frame(&p, frame, sizeof(frame), 10000, &m) == -1);
    frame_with_object(frame, -1);
    assert(pt_process_frame(&p, frame, sizeof(frame), 60000, &m) == 1);
    assert(m.vehicle_count == 1);
    assert(m.window_seconds == 60);
    assert(m.flow_vpm == 1.0);
    assert(strcmp(m.traffic_level, "normal") == 0);
    assert(strcmp(m.validity, "valid") == 0);
    pt_measurement_json(&m, json, sizeof(json));
    assert(strstr(json, "\"vehicle_count\":1") != NULL);
    assert(pt_finish(&p, &m) == 0);
    pt_destroy(&p);

    c.t1_vpm = 0; c.t2_vpm = 0;
    assert(pt_init(&p, W, H, &c) == 0);
    frame_with_object(frame, -1);
    assert(pt_process_frame(&p, frame, sizeof(frame), 0, &m) == 0);
    assert(pt_process_frame(&p, frame, sizeof(frame), 60000, &m) == 1);
    assert(strcmp(m.traffic_level, "unable_to_determine") == 0);
    pt_destroy(&p);
    puts("measurement tests passed");
    return 0;
}
