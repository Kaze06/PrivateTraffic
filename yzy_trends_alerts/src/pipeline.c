#include "trend_alert.h"
#include "y4m.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#include <process.h>
#define PROCESS_ID() _getpid()
#else
#include <unistd.h>
#define PROCESS_ID() getpid()
#endif

static int local_minute(unsigned *minute) {
    time_t now = time(NULL);
    struct tm local;
#ifdef _WIN32
    if (localtime_s(&local, &now) != 0) return -1;
#else
    if (!localtime_r(&now, &local)) return -1;
#endif
    *minute = (unsigned)local.tm_hour * 60 + (unsigned)local.tm_min;
    return 0;
}

static int parse_int(const char *text, int *out) {
    char *end;
    long value = strtol(text, &end, 10);
    if (!*text || *end || value < 0 || value > 100000) return -1;
    *out = (int)value;
    return 0;
}

static int publish(PTStore *store, PTMeasurement *m, uint64_t base,
                   unsigned long run_id, int *alerts) {
    char original[64], json[1024];
    unsigned minute;
    int created;
    strcpy(original, m->measurement_id);
    if (snprintf(m->measurement_id, sizeof(m->measurement_id), "r%lu-%s",
                 run_id, original) >= (int)sizeof(m->measurement_id) ||
        UINT64_MAX - base < m->measurement_time_ms) return -1;
    m->measurement_time_ms += base;
    m->window_start_ms += base;
    if (pt_save_measurement(store, m) != 1 || local_minute(&minute) != 0) return -1;
    created = pt_evaluate_alerts(store, m, minute);
    if (created < 0) return -1;
    *alerts += created;
    pt_measurement_json(m, json, sizeof(json));
    puts(json);
    return 0;
}

int main(int argc, char **argv) {
    PTStore store;
    PTLocation location;
    PTY4M video;
    PTProcessor processor;
    PTConfig config = {0};
    PTMeasurement measurement;
    uint64_t timestamp, base;
    unsigned long run_id;
    int status, result, alerts = 0, count = 0, line_y, t1 = 0, t2 = 0;
    if (argc != 4 && argc != 6) {
        fputs("Usage: pt_pipeline <data-dir> <location-id> <line-y> [t1-vpm t2-vpm]\n", stderr);
        return 2;
    }
    if (parse_int(argv[3], &line_y) != 0 ||
        (argc == 6 && (parse_int(argv[4], &t1) != 0 || parse_int(argv[5], &t2) != 0 ||
                       t1 <= 0 || t2 <= t1))) return 2;
    if (pt_store_open(&store, argv[1]) != 0 ||
        pt_get_location(&store, argv[2], &location) != 1 ||
        strcmp(location.permission_status, "approved") != 0 ||
        strcmp(location.source_mode, "replay") != 0) {
        fputs("Location must exist and have an approved replay source.\n", stderr);
        return 1;
    }
    if (pt_y4m_open(&video, location.source_ref) != 0) {
        fputs("Cannot open the registered Y4M source.\n", stderr); return 1;
    }
    config.x1 = video.width; config.y1 = video.height;
    config.line_y = line_y;
    config.motion_threshold = 35;
    config.min_blob_pixels = 24;
    config.max_track_distance = 40;
    config.max_missed_frames = 5;
    config.t1_vpm = t1; config.t2_vpm = t2;
    config.location_id = location.location_id;
    if (pt_init(&processor, video.width, video.height, &config) != 0) {
        fputs("Invalid measurement configuration.\n", stderr);
        pt_y4m_close(&video); return 2;
    }
    base = (uint64_t)time(NULL) * 1000ULL;
    run_id = (unsigned long)time(NULL) ^ ((unsigned long)PROCESS_ID() << 12);
    while ((status = pt_y4m_next(&video, &timestamp)) == 1) {
        result = pt_process_frame(&processor, video.luma, video.luma_size,
                                  timestamp, &measurement);
        if (result < 0) break;
        if (result == 1) {
            if (publish(&store, &measurement, base, run_id, &alerts) != 0) break;
            ++count;
        }
    }
    if (status == 0 && pt_finish(&processor, &measurement) == 1) {
        if (publish(&store, &measurement, base, run_id, &alerts) != 0) status = -1;
        else ++count;
    }
    pt_destroy(&processor);
    pt_y4m_close(&video);
    if (status != 0) {
        fputs("Replay, storage, or alert evaluation failed.\n", stderr); return 1;
    }
    fprintf(stderr, "Saved %d measurement(s); created %d alert(s).\n", count, alerts);
    return 0;
}
