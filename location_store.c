#include "location_store.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int valid_id(const char *s) {
    size_t n = 0;
    if (!s || !*s) return 0;
    while (*s) {
        if (!isalnum((unsigned char)*s) && *s != '-' && *s != '_') return 0;
        ++s; ++n;
        if (n >= 64) return 0;
    }
    return 1;
}

static int valid_field(const char *s, size_t capacity) {
    size_t n = 0;
    if (!s) return 0;
    while (n < capacity && s[n]) ++n;
    if (!n || n >= capacity) return 0;
    return strpbrk(s, "|\r\n") == NULL;
}

static int path_for(const PTStore *store, const char *name, char *out, size_t size) {
    int n;
    if (!store || !store->directory[0]) return -1;
    n = snprintf(out, size, "%s/%s", store->directory, name);
    return n > 0 && (size_t)n < size ? 0 : -1;
}

static int open_data(const PTStore *store, const char *name, const char *mode, FILE **out) {
    char path[600];
    if (path_for(store, name, path, sizeof(path)) != 0) return -1;
    *out = fopen(path, mode);
    return *out ? 0 : -1;
}

int pt_store_open(PTStore *store, const char *directory) {
    FILE *f;
    char path[600];
    if (!store || !directory || !*directory || strlen(directory) >= sizeof(store->directory)) return -1;
    memset(store, 0, sizeof(*store));
    strcpy(store->directory, directory);
    if (path_for(store, "locations.psv", path, sizeof(path)) != 0) return -1;
    f = fopen(path, "ab");
    if (!f) return -1;
    if (fclose(f) != 0) return -1;
    return 0;
}

static int parse_location(const char *line, PTLocation *out) {
    PTLocation x = {0};
    int n = sscanf(line, "%63[^|]|%127[^|]|%255[^|]|%15[^|]|%15[^\r\n]",
                   x.location_id, x.display_name, x.source_ref,
                   x.source_mode, x.permission_status);
    if (n != 5) return -1;
    *out = x;
    return 0;
}

int pt_get_location(PTStore *store, const char *id, PTLocation *out) {
    FILE *f;
    char line[600];
    PTLocation x;
    if (!store || !valid_id(id) || !out || open_data(store, "locations.psv", "rb", &f) != 0) return -1;
    while (fgets(line, sizeof(line), f)) {
        if (!strchr(line, '\n') && !feof(f)) { fclose(f); return -1; }
        if (parse_location(line, &x) != 0) { fclose(f); return -1; }
        if (strcmp(x.location_id, id) == 0) { *out = x; fclose(f); return 1; }
    }
    if (ferror(f)) { fclose(f); return -1; }
    fclose(f);
    return 0;
}

int pt_add_location(PTStore *store, const PTLocation *x) {
    PTLocation existing;
    FILE *f;
    int n;
    if (!store || !x || !valid_id(x->location_id) ||
        !valid_field(x->display_name, sizeof(x->display_name)) ||
        !valid_field(x->source_ref, sizeof(x->source_ref)) ||
        !valid_field(x->source_mode, sizeof(x->source_mode)) ||
        !valid_field(x->permission_status, sizeof(x->permission_status)) ||
        (strcmp(x->source_mode, "live") && strcmp(x->source_mode, "replay")) ||
        (strcmp(x->permission_status, "approved") && strcmp(x->permission_status, "pending"))) return -1;
    n = pt_get_location(store, x->location_id, &existing);
    if (n < 0) return -1;
    if (n == 1) return 0;
    if (open_data(store, "locations.psv", "ab", &f) != 0) return -1;
    n = fprintf(f, "%s|%s|%s|%s|%s\n", x->location_id, x->display_name,
                x->source_ref, x->source_mode, x->permission_status);
    if (fclose(f) != 0 || n < 0) return -1;
    return 1;
}

int pt_is_followed(PTStore *store, const char *id) {
    FILE *f;
    char line[128], op, found[64];
    int state = 0;
    if (!store || !valid_id(id)) return -1;
    if (open_data(store, "follows.psv", "rb", &f) != 0) {
        char path[600];
        if (path_for(store, "follows.psv", path, sizeof(path)) != 0) return -1;
        f = fopen(path, "ab");
        if (!f || fclose(f) != 0 || open_data(store, "follows.psv", "rb", &f) != 0) return -1;
    }
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "%c|%63[^\r\n]", &op, found) != 2 || (op != 'F' && op != 'U')) {
            fclose(f); return -1;
        }
        if (strcmp(id, found) == 0) state = op == 'F';
    }
    if (ferror(f)) { fclose(f); return -1; }
    fclose(f);
    return state;
}

static int set_follow(PTStore *store, const char *id, int desired) {
    PTLocation location;
    FILE *f;
    int found, current, ok;
    if (!store || !valid_id(id)) return -1;
    found = pt_get_location(store, id, &location);
    if (found != 1) return found == 0 ? 0 : -1;
    current = pt_is_followed(store, id);
    if (current < 0) return -1;
    if (current == desired) return 0;
    if (open_data(store, "follows.psv", "ab", &f) != 0) return -1;
    ok = fprintf(f, "%c|%s\n", desired ? 'F' : 'U', id) > 0;
    if (fclose(f) != 0) ok = 0;
    return ok ? 1 : -1;
}

int pt_follow_location(PTStore *store, const char *id) { return set_follow(store, id, 1); }
int pt_unfollow_location(PTStore *store, const char *id) { return set_follow(store, id, 0); }

int pt_list_locations(PTStore *store, PTLocationVisitor visitor, void *context) {
    FILE *f;
    PTLocation x;
    char line[600];
    int count = 0, followed;
    if (!store || !visitor || open_data(store, "locations.psv", "rb", &f) != 0) return -1;
    while (fgets(line, sizeof(line), f)) {
        if (parse_location(line, &x) != 0) { fclose(f); return -1; }
        followed = pt_is_followed(store, x.location_id);
        if (followed < 0 || visitor(&x, followed, context) != 0) { fclose(f); return -1; }
        ++count;
    }
    if (ferror(f)) { fclose(f); return -1; }
    fclose(f);
    return count;
}

static int valid_measurement(const PTMeasurement *m) {
    if (!m || !valid_id(m->measurement_id) || !valid_id(m->location_id) ||
        !valid_field(m->source_state, sizeof(m->source_state)) ||
        !valid_field(m->traffic_level, sizeof(m->traffic_level)) ||
        !valid_field(m->validity, sizeof(m->validity)) ||
        m->measurement_time_ms < m->window_start_ms || m->vehicle_count < 0 ||
        !isfinite(m->flow_vpm) || m->flow_vpm < 0) return 0;
    return 1;
}

int pt_save_measurement(PTStore *store, const PTMeasurement *m) {
    PTLocation location;
    FILE *f;
    int found, ok;
    if (!store || !valid_measurement(m)) return -1;
    found = pt_get_location(store, m->location_id, &location);
    if (found != 1) return found == 0 ? 0 : -1;
    if (strcmp(location.permission_status, "approved") != 0) return 0;
    if (open_data(store, "measurements.psv", "ab", &f) != 0) return -1;
    ok = fprintf(f, "%s|%s|%s|%llu|%llu|%u|%d|%.6f|%s|%s\n",
                 m->measurement_id, m->location_id, m->source_state,
                 (unsigned long long)m->measurement_time_ms,
                 (unsigned long long)m->window_start_ms, m->window_seconds,
                 m->vehicle_count, m->flow_vpm, m->traffic_level, m->validity) > 0;
    if (fclose(f) != 0) ok = 0;
    return ok ? 1 : -1;
}

static int parse_measurement(const char *line, PTMeasurement *out) {
    PTMeasurement x = {0};
    unsigned long long time, start;
    int n = sscanf(line, "%63[^|]|%63[^|]|%15[^|]|%llu|%llu|%u|%d|%lf|%23[^|]|%23[^\r\n]",
                   x.measurement_id, x.location_id, x.source_state, &time, &start,
                   &x.window_seconds, &x.vehicle_count, &x.flow_vpm,
                   x.traffic_level, x.validity);
    if (n != 10) return -1;
    x.measurement_time_ms = (uint64_t)time;
    x.window_start_ms = (uint64_t)start;
    if (!valid_measurement(&x)) return -1;
    *out = x;
    return 0;
}

static int scan_measurements(PTStore *store, const char *id, uint64_t from,
                             uint64_t to, PTMeasurementVisitor visitor,
                             void *context, PTMeasurement *latest) {
    FILE *f;
    char line[700];
    PTMeasurement x;
    int count = 0;
    if (!store || !valid_id(id) || from > to) return -1;
    if (open_data(store, "measurements.psv", "rb", &f) != 0) {
        char path[600];
        if (path_for(store, "measurements.psv", path, sizeof(path)) != 0) return -1;
        f = fopen(path, "ab");
        if (!f || fclose(f) != 0 || open_data(store, "measurements.psv", "rb", &f) != 0) return -1;
    }
    while (fgets(line, sizeof(line), f)) {
        if (parse_measurement(line, &x) != 0) { fclose(f); return -1; }
        if (strcmp(x.location_id, id) || x.measurement_time_ms < from || x.measurement_time_ms > to) continue;
        if (latest && (!count || x.measurement_time_ms >= latest->measurement_time_ms)) *latest = x;
        if (visitor && visitor(&x, context) != 0) { fclose(f); return -1; }
        ++count;
    }
    if (ferror(f)) { fclose(f); return -1; }
    fclose(f);
    return count;
}

int pt_get_current_status(PTStore *store, const char *id, PTMeasurement *out) {
    int n;
    if (!out) return -1;
    n = scan_measurements(store, id, 0, UINT64_MAX, NULL, NULL, out);
    return n < 0 ? -1 : n > 0;
}

int pt_query_measurements(PTStore *store, const char *id, uint64_t from,
                          uint64_t to, PTMeasurementVisitor visitor, void *context) {
    return scan_measurements(store, id, from, to, visitor, context, NULL);
}
