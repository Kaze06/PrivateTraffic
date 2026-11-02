#include "measurement.h"
#include "y4m.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void usage(const char *name) {
    fprintf(stderr,
            "Usage: %s <video.y4m> <location_id> <line_y> [t1_vpm t2_vpm]\n"
            "Output: one Measurement JSON object per line.\n", name);
}

int main(int argc, char **argv) {
    PTY4M video;
    PTProcessor processor;
    PTMeasurement measurement;
    PTConfig config;
    uint64_t timestamp;
    char json[1024];
    int status, had_error = 0;
    if (argc != 4 && argc != 6) { usage(argv[0]); return 2; }
    if (pt_y4m_open(&video, argv[1]) != 0) {
        fprintf(stderr, "Cannot open 8-bit 4:2:0 Y4M video: %s\n", argv[1]);
        return 1;
    }
    memset(&config, 0, sizeof(config));
    config.x0 = 0; config.y0 = 0;
    config.x1 = video.width; config.y1 = video.height;
    config.line_y = atoi(argv[3]);
    config.motion_threshold = 35;
    config.min_blob_pixels = 24;
    config.max_track_distance = 40;
    config.max_missed_frames = 5;
    config.t1_vpm = argc == 6 ? atoi(argv[4]) : 0;
    config.t2_vpm = argc == 6 ? atoi(argv[5]) : 0;
    config.location_id = argv[2];
    if (pt_init(&processor, video.width, video.height, &config) != 0) {
        fprintf(stderr, "Invalid ROI, line, threshold, or location ID.\n");
        pt_y4m_close(&video);
        return 2;
    }
    while ((status = pt_y4m_next(&video, &timestamp)) == 1) {
        int result = pt_process_frame(&processor, video.luma, video.luma_size,
                                      timestamp, &measurement);
        if (result < 0) { had_error = 1; break; }
        if (result == 1) {
            pt_measurement_json(&measurement, json, sizeof(json));
            puts(json);
        }
    }
    if (status < 0) had_error = 1;
    if (!had_error && pt_finish(&processor, &measurement) == 1) {
        pt_measurement_json(&measurement, json, sizeof(json));
        puts(json);
    }
    if (had_error) fprintf(stderr, "Invalid frame or timestamp; no partial result published.\n");
    pt_destroy(&processor);
    pt_y4m_close(&video);
    return had_error ? 1 : 0;
}
