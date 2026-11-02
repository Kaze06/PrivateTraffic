#include "location_store.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <direct.h>
#define MAKE_DIR(path) _mkdir(path)
#else
#include <sys/stat.h>
#define MAKE_DIR(path) mkdir(path, 0700)
#endif

static void usage(void) {
    puts("Usage: pt_locations <data-dir> <command> [arguments]");
    puts("Commands:");
    puts("  add <id> <name> <source-ref> <live|replay> <approved|pending>");
    puts("  list");
    puts("  follow <id> | unfollow <id>");
    puts("  current <id>");
    puts("  history <id> <from-ms> <to-ms>");
}

static int print_location(const PTLocation *x, int followed, void *context) {
    (void)context;
    printf("%s\t%s\t%s\t%s\t%s\t%s\n", x->location_id,
           x->display_name, x->source_ref, x->source_mode,
           x->permission_status, followed ? "followed" : "not_followed");
    return 0;
}

static void print_measurement(const PTMeasurement *m) {
    printf("%s\t%s\t%" PRIu64 "\t%d\t%.3f\t%s\t%s\t%s\n",
           m->measurement_id, m->location_id, m->measurement_time_ms,
           m->vehicle_count, m->flow_vpm, m->traffic_level,
           m->validity, m->source_state);
}

static int measurement_visitor(const PTMeasurement *m, void *context) {
    (void)context;
    print_measurement(m);
    return 0;
}

static int parse_time(const char *text, uint64_t *out) {
    char *end;
    unsigned long long value;
    if (!text || !*text || *text == '-') return -1;
    errno = 0;
    value = strtoull(text, &end, 10);
    if (errno || *end) return -1;
    *out = (uint64_t)value;
    return 0;
}

int main(int argc, char **argv) {
    PTStore store;
    int result = -1;
    if (argc < 3) { usage(); return 2; }
    if (MAKE_DIR(argv[1]) != 0 && errno != EEXIST) {
        perror("data directory"); return 1;
    }
    if (pt_store_open(&store, argv[1]) != 0) {
        fputs("Cannot open data directory.\n", stderr); return 1;
    }
    if (strcmp(argv[2], "add") == 0 && argc == 8) {
        PTLocation x = {0};
        if (snprintf(x.location_id, sizeof(x.location_id), "%s", argv[3]) >= (int)sizeof(x.location_id) ||
            snprintf(x.display_name, sizeof(x.display_name), "%s", argv[4]) >= (int)sizeof(x.display_name) ||
            snprintf(x.source_ref, sizeof(x.source_ref), "%s", argv[5]) >= (int)sizeof(x.source_ref) ||
            snprintf(x.source_mode, sizeof(x.source_mode), "%s", argv[6]) >= (int)sizeof(x.source_mode) ||
            snprintf(x.permission_status, sizeof(x.permission_status), "%s", argv[7]) >= (int)sizeof(x.permission_status)) {
            fputs("Field too long.\n", stderr); return 2;
        }
        result = pt_add_location(&store, &x);
    } else if (strcmp(argv[2], "list") == 0 && argc == 3) {
        result = pt_list_locations(&store, print_location, NULL);
    } else if (strcmp(argv[2], "follow") == 0 && argc == 4) {
        result = pt_follow_location(&store, argv[3]);
    } else if (strcmp(argv[2], "unfollow") == 0 && argc == 4) {
        result = pt_unfollow_location(&store, argv[3]);
    } else if (strcmp(argv[2], "current") == 0 && argc == 4) {
        PTMeasurement m;
        result = pt_get_current_status(&store, argv[3], &m);
        if (result == 1) print_measurement(&m);
    } else if (strcmp(argv[2], "history") == 0 && argc == 6) {
        uint64_t from, to;
        if (parse_time(argv[4], &from) != 0 || parse_time(argv[5], &to) != 0) {
            fputs("Invalid timestamp.\n", stderr); return 2;
        }
        result = pt_query_measurements(&store, argv[3], from, to,
                                       measurement_visitor, NULL);
    } else {
        usage(); return 2;
    }
    if (result < 0) { fputs("Invalid input or data I/O error.\n", stderr); return 1; }
    if (result == 0) puts("No change or no matching record.");
    return 0;
}
