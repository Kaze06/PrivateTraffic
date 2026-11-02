#include "location_store.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#ifdef _WIN32
#include <direct.h>
#include <process.h>
#define MAKE_DIR(path) _mkdir(path)
#define PROCESS_ID() _getpid()
#else
#include <unistd.h>
#define MAKE_DIR(path) mkdir(path, 0700)
#define PROCESS_ID() getpid()
#endif

static int location_count(const PTLocation *location, int followed, void *context) {
    int *count = context;
    assert(location->location_id[0]);
    assert(followed == 0 || followed == 1);
    ++*count;
    return 0;
}

static int measurement_count(const PTMeasurement *m, void *context) {
    int *count = context;
    assert(m->measurement_time_ms >= 60000);
    ++*count;
    return 0;
}

int main(void) {
    char dir[100];
    PTStore store;
    PTLocation location = {"road-demo", "Test road", "sample/road_demo.y4m", "replay", "approved"};
    PTLocation pending = {"road-pending", "Pending road", "sample/pending.y4m", "replay", "pending"};
    PTLocation found;
    PTMeasurement m = {"measurement-1", "road-demo", "replay", 60000, 0, 60, 1, 1.0, "normal", "valid"};
    PTMeasurement current;
    int count = 0;
    snprintf(dir, sizeof(dir), "pt_location_test_%ld_%ld", (long)PROCESS_ID(), (long)time(NULL));
    assert(MAKE_DIR(dir) == 0);
    assert(pt_store_open(&store, dir) == 0);
    assert(pt_add_location(&store, &location) == 1);
    assert(pt_add_location(&store, &location) == 0);
    assert(pt_add_location(&store, &pending) == 1);
    assert(pt_get_location(&store, "road-demo", &found) == 1);
    assert(strcmp(found.display_name, "Test road") == 0);
    assert(pt_list_locations(&store, location_count, &count) == 2);
    assert(count == 2);
    assert(pt_follow_location(&store, "road-demo") == 1);
    assert(pt_follow_location(&store, "road-demo") == 0);
    assert(pt_is_followed(&store, "road-demo") == 1);
    assert(pt_unfollow_location(&store, "road-demo") == 1);
    assert(pt_is_followed(&store, "road-demo") == 0);
    assert(pt_save_measurement(&store, &m) == 1);
    assert(pt_get_current_status(&store, "road-demo", &current) == 1);
    assert(current.vehicle_count == 1);
    m.measurement_time_ms = 120000;
    m.window_start_ms = 60000;
    strcpy(m.measurement_id, "measurement-2");
    strcpy(m.validity, "insufficient_window");
    strcpy(m.traffic_level, "unable_to_determine");
    assert(pt_save_measurement(&store, &m) == 1);
    assert(pt_get_current_status(&store, "road-demo", &current) == 1);
    assert(strcmp(current.validity, "insufficient_window") == 0);
    count = 0;
    assert(pt_query_measurements(&store, "road-demo", 60000, 120000,
                                 measurement_count, &count) == 2);
    assert(count == 2);
    assert(pt_query_measurements(&store, "road-demo", 70000, 110000, NULL, NULL) == 0);
    strcpy(m.location_id, "road-pending");
    assert(pt_save_measurement(&store, &m) == 0);
    puts("location store tests passed");
    return 0;
}
