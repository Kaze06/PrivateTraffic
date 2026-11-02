#include "trend_alert.h"

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#include <windows.h>
#define WAIT_FIVE_SECONDS() Sleep(5000)
#else
#include <unistd.h>
#define WAIT_FIVE_SECONDS() sleep(5)
#endif

#ifndef PT_DATA_PATH
#define PT_DATA_PATH "data.js"
#endif

typedef struct { FILE *file; int first; } Writer;

static void usage(void) {
    puts("Usage: pt_zian <data-dir> <command> [arguments]");
    puts("  rule put <id> <followed-location> <threshold-vpm> <HH:MM> <HH:MM> <0|1>");
    puts("  rule list | rule delete <id>");
    puts("  compare <location> <from-ms> <to-ms> <from-ms> <to-ms>");
    puts("  evaluate <location> | export <location> | watch <location>");
}

static int parse_u64(const char *s, uint64_t *out) {
    char *end;
    unsigned long long x;
    if (!s || !*s || *s == '-') return -1;
    errno = 0;
    x = strtoull(s, &end, 10);
    if (errno || *end) return -1;
    *out = (uint64_t)x;
    return 0;
}

static int parse_minute(const char *s, unsigned *out) {
    unsigned hour, minute;
    char extra;
    if (sscanf(s, "%u:%u%c", &hour, &minute, &extra) != 2 ||
        hour > 23 || minute > 59) return -1;
    *out = hour * 60 + minute;
    return 0;
}

static int parse_double(const char *s, double *out) {
    char *end;
    errno = 0;
    *out = strtod(s, &end);
    return !s[0] || errno || *end || !isfinite(*out) || *out <= 0 ? -1 : 0;
}

static void json_string(FILE *f, const char *s) {
    const unsigned char *p = (const unsigned char *)s;
    fputc('"', f);
    while (*p) {
        if (*p == '"' || *p == '\\') { fputc('\\', f); fputc(*p, f); }
        else if (*p < 32) fprintf(f, "\\u%04x", *p);
        else fputc(*p, f);
        ++p;
    }
    fputc('"', f);
}

static int write_measurement(const PTMeasurement *m, void *context) {
    Writer *w = context;
    if (!w->first) fputc(',', w->file);
    w->first = 0;
    fputs("{\"id\":", w->file); json_string(w->file, m->measurement_id);
    fprintf(w->file, ",\"time\":%" PRIu64 ",\"count\":%d,\"flow\":%.6f,\"validity\":",
            m->measurement_time_ms, m->vehicle_count, m->flow_vpm);
    json_string(w->file, m->validity);
    fputc('}', w->file);
    return ferror(w->file) ? -1 : 0;
}

static int write_rule(const PTAlertRule *r, void *context) {
    Writer *w = context;
    if (!w->first) fputc(',', w->file);
    w->first = 0;
    fputs("{\"id\":", w->file); json_string(w->file, r->rule_id);
    fputs(",\"location\":", w->file); json_string(w->file, r->location_id);
    fprintf(w->file, ",\"threshold\":%.6f,\"start\":%u,\"end\":%u,\"enabled\":%s}",
            r->threshold_vpm, r->start_minute, r->end_minute, r->enabled ? "true" : "false");
    return ferror(w->file) ? -1 : 0;
}

static int write_event(const PTAlertEvent *e, void *context) {
    Writer *w = context;
    if (!w->first) fputc(',', w->file);
    w->first = 0;
    fputs("{\"rule\":", w->file); json_string(w->file, e->rule_id);
    fputs(",\"measurement\":", w->file); json_string(w->file, e->measurement_id);
    fprintf(w->file, ",\"time\":%" PRIu64 ",\"flow\":%.6f,\"threshold\":%.6f}",
            e->measurement_time_ms, e->flow_vpm, e->threshold_vpm);
    return ferror(w->file) ? -1 : 0;
}

static int export_data(PTStore *store, const char *id) {
    char temp[700];
    PTLocation location;
    Writer w;
    int result, closed;
    if (pt_get_location(store, id, &location) != 1) return -1;
    if (snprintf(temp, sizeof(temp), "%s.tmp", PT_DATA_PATH) >= (int)sizeof(temp)) return -1;
    w.file = fopen(temp, "wb");
    if (!w.file) return -1;
    fputs("window.PT_DATA={\"location\":", w.file);
    json_string(w.file, location.location_id);
    fputs(",\"name\":", w.file);
    json_string(w.file, location.display_name);
    fputs(",\"measurements\":[", w.file);
    w.first = 1;
    result = pt_query_measurements(store, id, 0, UINT64_MAX, write_measurement, &w);
    if (result >= 0) {
        fputs("],\"rules\":[", w.file); w.first = 1;
        result = pt_list_rules(store, write_rule, &w);
    }
    if (result >= 0) {
        fputs("],\"events\":[", w.file); w.first = 1;
        result = pt_list_alert_events(store, id, write_event, &w);
    }
    if (result >= 0) fputs("]};\n", w.file);
    if (ferror(w.file)) result = -1;
    closed = fclose(w.file);
    if (result < 0 || closed != 0) { remove(temp); return -1; }
#ifdef _WIN32
    if (!MoveFileExA(temp, PT_DATA_PATH, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        remove(temp); return -1;
    }
#else
    if (rename(temp, PT_DATA_PATH) != 0) { remove(temp); return -1; }
#endif
    return 0;
}

static int print_rule(const PTAlertRule *r, void *context) {
    (void)context;
    printf("%s\t%s\t%.3f vpm\t%02u:%02u-%02u:%02u\t%s\n",
           r->rule_id, r->location_id, r->threshold_vpm,
           r->start_minute / 60, r->start_minute % 60,
           r->end_minute / 60, r->end_minute % 60,
           r->enabled ? "enabled" : "disabled");
    return 0;
}

static void print_summary(const char *label, const PTTrendSummary *s) {
    printf("%s: valid=%d unavailable=%d vehicles=%" PRId64,
           label, s->valid_count, s->unavailable_count, s->total_vehicles);
    if (s->valid_count) printf(" avg=%.3f min=%.3f max=%.3f vpm\n",
                               s->average_flow_vpm, s->min_flow_vpm, s->max_flow_vpm);
    else puts(" avg=unavailable");
}

static int current_minute(unsigned *out) {
    time_t now = time(NULL);
    struct tm local;
#ifdef _WIN32
    if (localtime_s(&local, &now) != 0) return -1;
#else
    if (!localtime_r(&now, &local)) return -1;
#endif
    *out = (unsigned)local.tm_hour * 60 + (unsigned)local.tm_min;
    return 0;
}

static int evaluate_latest(PTStore *store, const char *id) {
    PTMeasurement m;
    unsigned minute;
    int found = pt_get_current_status(store, id, &m);
    if (found <= 0) return found;
    if (current_minute(&minute) != 0) return -1;
    return pt_evaluate_alerts(store, &m, minute);
}

typedef struct {
    PTStore *store;
    int skip;
    int seen;
    int alerts;
    unsigned minute;
} WatchContext;

static int evaluate_new(const PTMeasurement *m, void *context) {
    WatchContext *watch = context;
    int created;
    if (watch->seen++ < watch->skip) return 0;
    created = pt_evaluate_alerts(watch->store, m, watch->minute);
    if (created < 0) return -1;
    watch->alerts += created;
    printf("New measurement %s: %d alert(s)\n", m->measurement_id, created);
    return 0;
}

static int watch(PTStore *store, const char *id) {
    int seen = pt_query_measurements(store, id, 0, UINT64_MAX, NULL, NULL);
    if (seen < 0) return -1;
    printf("Watching %s. Open dashboard.html beside %s. Press Ctrl+C to stop.\n", id, PT_DATA_PATH);
    for (;;) {
        int count = pt_query_measurements(store, id, 0, UINT64_MAX, NULL, NULL);
        if (count < 0) return -1;
        if (count < seen) seen = 0;
        if (count > seen) {
            WatchContext context = {0};
            context.store = store;
            context.skip = seen;
            if (current_minute(&context.minute) != 0) return -1;
            count = pt_query_measurements(store, id, 0, UINT64_MAX, evaluate_new, &context);
            if (count < 0) return -1;
            seen = count;
        }
        if (export_data(store, id) != 0) return -1;
        WAIT_FIVE_SECONDS();
    }
}

int main(int argc, char **argv) {
    PTStore store;
    int result = -1;
    if (argc < 4) { usage(); return 2; }
    if (pt_store_open(&store, argv[1]) != 0) {
        fputs("Data directory missing or not writable.\n", stderr); return 1;
    }
    if (strcmp(argv[2], "rule") == 0 && strcmp(argv[3], "put") == 0 && argc == 10) {
        PTAlertRule rule = {0};
        if (snprintf(rule.rule_id, sizeof(rule.rule_id), "%s", argv[4]) >= (int)sizeof(rule.rule_id) ||
            snprintf(rule.location_id, sizeof(rule.location_id), "%s", argv[5]) >= (int)sizeof(rule.location_id) ||
            parse_double(argv[6], &rule.threshold_vpm) != 0 ||
            parse_minute(argv[7], &rule.start_minute) != 0 ||
            parse_minute(argv[8], &rule.end_minute) != 0 ||
            (strcmp(argv[9], "0") != 0 && strcmp(argv[9], "1") != 0)) {
            fputs("Invalid rule.\n", stderr); return 2;
        }
        rule.enabled = strcmp(argv[9], "1") == 0;
        result = pt_put_rule(&store, &rule);
    } else if (strcmp(argv[2], "rule") == 0 && strcmp(argv[3], "list") == 0 && argc == 4) {
        result = pt_list_rules(&store, print_rule, NULL);
    } else if (strcmp(argv[2], "rule") == 0 && strcmp(argv[3], "delete") == 0 && argc == 5) {
        result = pt_delete_rule(&store, argv[4]);
    } else if (strcmp(argv[2], "compare") == 0 && argc == 8) {
        uint64_t a, b, c, d;
        PTTrendSummary first, second;
        if (parse_u64(argv[4], &a) || parse_u64(argv[5], &b) ||
            parse_u64(argv[6], &c) || parse_u64(argv[7], &d)) return 2;
        result = pt_compare_periods(&store, argv[3], a, b, c, d, &first, &second);
        if (result == 0) { print_summary("Period A", &first); print_summary("Period B", &second); }
    } else if (strcmp(argv[2], "evaluate") == 0 && argc == 4) {
        result = evaluate_latest(&store, argv[3]);
    } else if (strcmp(argv[2], "export") == 0 && argc == 4) {
        result = export_data(&store, argv[3]);
    } else if (strcmp(argv[2], "watch") == 0 && argc == 4) {
        result = watch(&store, argv[3]);
    } else { usage(); return 2; }
    if (result < 0) { fputs("Command failed: invalid input or data I/O.\n", stderr); return 1; }
    if (result == 0 && strcmp(argv[2], "rule") == 0 &&
        strcmp(argv[3], "put") == 0) puts("No change. Follow the location first.");
    else if (result == 0 && strcmp(argv[2], "evaluate") == 0) puts("No new alert.");
    return 0;
}
