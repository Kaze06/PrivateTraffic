#ifndef PRIVATE_TRAFFIC_MEASUREMENT_H
#define PRIVATE_TRAFFIC_MEASUREMENT_H

#include <stddef.h>
#include <stdint.h>

#define PT_MAX_DETECTIONS 128
#define PT_MAX_TRACKS 128
#define PT_WINDOW_MS 60000ULL

typedef struct {
    int x0, y0, x1, y1; /* ROI, x1/y1 exclusive */
    int line_y;         /* Horizontal counting line inside ROI */
    int motion_threshold;
    int min_blob_pixels;
    int max_track_distance;
    int max_missed_frames;
    int t1_vpm, t2_vpm; /* 0 means uncalibrated */
    const char *location_id;
} PTConfig;

typedef struct {
    int x, y, pixels;
} PTDetection;

typedef struct {
    int id, x, y, side, missed, counted, matched;
} PTTrack;

typedef struct {
    char measurement_id[64];
    char location_id[64];
    char source_state[16];
    uint64_t measurement_time_ms;
    uint64_t window_start_ms;
    uint32_t window_seconds;
    int vehicle_count;
    double flow_vpm;
    char traffic_level[24];
    char validity[24];
} PTMeasurement;

typedef struct {
    PTConfig config;
    int width, height;
    uint8_t *background;
    uint8_t *mask;
    int *queue;
    PTTrack tracks[PT_MAX_TRACKS];
    int track_count, next_track_id;
    uint64_t window_start_ms, last_time_ms;
    int window_count, started, has_last_time;
    unsigned int measurement_seq;
} PTProcessor;

/* First frame establishes a static background. One crossing counts once per track. */
int pt_init(PTProcessor *p, int width, int height, const PTConfig *config);
void pt_destroy(PTProcessor *p);
/* Returns 1 when a complete 60-second Measurement is available, 0 otherwise, -1 on error. */
int pt_process_frame(PTProcessor *p, const uint8_t *luma, size_t luma_size,
                     uint64_t timestamp_ms, PTMeasurement *out);
/* Returns a partial result only when a window has started; validity is insufficient_window. */
int pt_finish(PTProcessor *p, PTMeasurement *out);
void pt_measurement_json(const PTMeasurement *m, char *buffer, size_t size);

#endif
