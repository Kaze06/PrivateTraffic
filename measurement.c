#include "measurement.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int side_of_line(int y, int line) { return y < line ? -1 : 1; }

int pt_init(PTProcessor *p, int width, int height, const PTConfig *c) {
    size_t pixels;
    const char *id;
    if (!p || !c || !c->location_id || !c->location_id[0] ||
        width < 2 || height < 2 || c->x0 < 0 || c->y0 < 0 ||
        c->x1 > width || c->y1 > height || c->x0 >= c->x1 || c->y0 >= c->y1 ||
        c->line_y <= c->y0 || c->line_y >= c->y1 ||
        c->motion_threshold < 1 || c->motion_threshold > 255 ||
        c->min_blob_pixels < 1 || c->max_track_distance < 1 ||
        c->max_missed_frames < 0 || strlen(c->location_id) >= 64 ||
        c->t1_vpm < 0 || c->t2_vpm < 0 ||
        (c->t1_vpm && c->t2_vpm && c->t1_vpm >= c->t2_vpm)) return -1;
    for (id = c->location_id; *id; ++id)
        if (!isalnum((unsigned char)*id) && *id != '-' && *id != '_') return -1;
    memset(p, 0, sizeof(*p));
    pixels = (size_t)width * (size_t)height;
    p->background = malloc(pixels);
    p->mask = calloc(pixels, 1);
    p->queue = malloc(pixels * sizeof(*p->queue));
    if (!p->background || !p->mask || !p->queue) {
        pt_destroy(p);
        return -1;
    }
    p->config = *c;
    p->width = width;
    p->height = height;
    p->next_track_id = 1;
    return 0;
}

void pt_destroy(PTProcessor *p) {
    if (!p) return;
    free(p->background);
    free(p->mask);
    free(p->queue);
    memset(p, 0, sizeof(*p));
}

/* This motion detector is a replaceable baseline, not a vehicle classifier. */
static int detect(PTProcessor *p, const uint8_t *frame, PTDetection *out) {
    const PTConfig *c = &p->config;
    int x, y, n = 0;
    memset(p->mask, 0, (size_t)p->width * (size_t)p->height);
    for (y = c->y0; y < c->y1; ++y) for (x = c->x0; x < c->x1; ++x) {
        int i = y * p->width + x;
        int diff = (int)frame[i] - (int)p->background[i];
        if (diff < 0) diff = -diff;
        p->mask[i] = (uint8_t)(diff >= c->motion_threshold);
    }
    for (y = c->y0; y < c->y1; ++y) for (x = c->x0; x < c->x1; ++x) {
        int head = 0, tail = 0, pixels = 0;
        int64_t sx = 0, sy = 0;
        int start = y * p->width + x;
        if (p->mask[start] != 1) continue;
        p->mask[start] = 2;
        p->queue[tail++] = start;
        while (head < tail) {
            int i = p->queue[head++], px = i % p->width, py = i / p->width;
            int neighbours[4] = {i - 1, i + 1, i - p->width, i + p->width};
            int k;
            ++pixels; sx += px; sy += py;
            for (k = 0; k < 4; ++k) {
                int nx = px + (k == 0 ? -1 : k == 1 ? 1 : 0);
                int ny = py + (k == 2 ? -1 : k == 3 ? 1 : 0);
                if (nx < c->x0 || nx >= c->x1 || ny < c->y0 || ny >= c->y1) continue;
                if (p->mask[neighbours[k]] == 1) {
                    p->mask[neighbours[k]] = 2;
                    p->queue[tail++] = neighbours[k];
                }
            }
        }
        if (pixels >= c->min_blob_pixels && n < PT_MAX_DETECTIONS) {
            out[n].x = (int)(sx / pixels);
            out[n].y = (int)(sy / pixels);
            out[n].pixels = pixels;
            ++n;
        }
    }
    return n;
}

static void update_tracks(PTProcessor *p, PTDetection *detections, int count) {
    int i, j;
    for (i = 0; i < p->track_count; ++i) p->tracks[i].matched = 0;
    for (j = 0; j < count; ++j) {
        int best = -1, best_dist = p->config.max_track_distance * p->config.max_track_distance;
        for (i = 0; i < p->track_count; ++i) {
            int dx, dy, dist;
            if (p->tracks[i].matched) continue;
            dx = p->tracks[i].x - detections[j].x;
            dy = p->tracks[i].y - detections[j].y;
            dist = dx * dx + dy * dy;
            if (dist <= best_dist) { best = i; best_dist = dist; }
        }
        if (best >= 0) {
            PTTrack *t = &p->tracks[best];
            int new_side = side_of_line(detections[j].y, p->config.line_y);
            if (!t->counted && t->side != new_side) {
                ++p->window_count;
                t->counted = 1;
            }
            t->x = detections[j].x;
            t->y = detections[j].y;
            t->side = new_side;
            t->missed = 0;
            t->matched = 1;
        } else if (p->track_count < PT_MAX_TRACKS) {
            PTTrack *t = &p->tracks[p->track_count++];
            *t = (PTTrack){p->next_track_id++, detections[j].x, detections[j].y,
                           side_of_line(detections[j].y, p->config.line_y), 0, 0, 1};
        }
    }
    for (i = 0; i < p->track_count;) {
        if (!p->tracks[i].matched) ++p->tracks[i].missed;
        if (p->tracks[i].missed > p->config.max_missed_frames) {
            p->tracks[i] = p->tracks[--p->track_count];
        } else ++i;
    }
}

static void make_measurement(PTProcessor *p, uint64_t end_ms,
                             int complete, PTMeasurement *out) {
    uint64_t duration = end_ms - p->window_start_ms;
    memset(out, 0, sizeof(*out));
    snprintf(out->measurement_id, sizeof(out->measurement_id), "measurement-%u",
             ++p->measurement_seq);
    snprintf(out->location_id, sizeof(out->location_id), "%s", p->config.location_id);
    snprintf(out->source_state, sizeof(out->source_state), "replay");
    out->measurement_time_ms = end_ms;
    out->window_start_ms = p->window_start_ms;
    out->window_seconds = (uint32_t)(duration / 1000);
    out->vehicle_count = p->window_count;
    out->flow_vpm = duration ? (double)p->window_count * 60000.0 / duration : 0.0;
    if (!complete) {
        snprintf(out->validity, sizeof(out->validity), "insufficient_window");
        snprintf(out->traffic_level, sizeof(out->traffic_level), "unable_to_determine");
    } else {
        snprintf(out->validity, sizeof(out->validity), "valid");
        if (!p->config.t1_vpm || !p->config.t2_vpm)
            snprintf(out->traffic_level, sizeof(out->traffic_level), "unable_to_determine");
        else if (out->flow_vpm < p->config.t1_vpm)
            snprintf(out->traffic_level, sizeof(out->traffic_level), "clear");
        else if (out->flow_vpm < p->config.t2_vpm)
            snprintf(out->traffic_level, sizeof(out->traffic_level), "normal");
        else snprintf(out->traffic_level, sizeof(out->traffic_level), "congested");
    }
}

int pt_process_frame(PTProcessor *p, const uint8_t *frame, size_t size,
                     uint64_t time_ms, PTMeasurement *out) {
    PTDetection detections[PT_MAX_DETECTIONS];
    int result = 0, n;
    if (!p || !frame || !out || !p->background ||
        size != (size_t)p->width * (size_t)p->height ||
        (p->has_last_time && time_ms <= p->last_time_ms)) return -1;
    if (!p->started) {
        memcpy(p->background, frame, size);
        p->window_start_ms = time_ms;
        p->started = 1;
    } else {
        if (time_ms >= p->window_start_ms + PT_WINDOW_MS) {
            make_measurement(p, p->window_start_ms + PT_WINDOW_MS, 1, out);
            p->window_start_ms += PT_WINDOW_MS;
            p->window_count = 0;
            result = 1;
        }
        n = detect(p, frame, detections);
        update_tracks(p, detections, n);
    }
    p->last_time_ms = time_ms;
    p->has_last_time = 1;
    return result;
}

int pt_finish(PTProcessor *p, PTMeasurement *out) {
    if (!p || !out || !p->started || p->last_time_ms <= p->window_start_ms) return 0;
    make_measurement(p, p->last_time_ms, 0, out);
    p->started = 0;
    return 1;
}

void pt_measurement_json(const PTMeasurement *m, char *buf, size_t size) {
    if (!m || !buf || !size) return;
    snprintf(buf, size,
             "{\"measurement_id\":\"%s\",\"location_id\":\"%s\","
             "\"source_state\":\"%s\",\"measurement_time\":%llu,"
             "\"window_start_ms\":%llu,\"window_seconds\":%u,"
             "\"vehicle_count\":%d,\"flow_vpm\":%.3f,"
             "\"traffic_level\":\"%s\",\"validity\":\"%s\"}",
             m->measurement_id, m->location_id, m->source_state,
             (unsigned long long)m->measurement_time_ms,
             (unsigned long long)m->window_start_ms, m->window_seconds,
             m->vehicle_count, m->flow_vpm, m->traffic_level, m->validity);
}
