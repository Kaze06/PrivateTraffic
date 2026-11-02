#include "trend_alert.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#include <direct.h>
#include <process.h>
#define MAKE_DIR(path) _mkdir(path)
#define PROCESS_ID() _getpid()
#else
#include <sys/stat.h>
#include <unistd.h>
#define MAKE_DIR(path) mkdir(path, 0700)
#define PROCESS_ID() getpid()
#endif

static int count_rules(const PTAlertRule *rule, void *context) {
    ++*(int *)context;
    assert(rule->threshold_vpm > 0);
    return 0;
}

static int count_events(const PTAlertEvent *event, void *context) {
    ++*(int *)context;
    assert(strcmp(event->location_id, "road-a") == 0);
    return 0;
}

int main(void) {
    char dir[100];
    PTStore store;
    PTLocation location = {"road-a", "Road A", "sample.y4m", "replay", "approved"};
    PTMeasurement first = {"m-1", "road-a", "replay", 60000, 0, 60, 2, 2.0, "normal", "valid"};
    PTMeasurement second = {"m-2", "road-a", "replay", 120000, 60000, 60, 8, 8.0, "congested", "valid"};
    PTMeasurement invalid = {"m-3", "road-a", "unavailable", 180000, 120000, 60, 0, 0.0, "unable_to_determine", "insufficient_window"};
    PTTrendSummary a, b;
    PTAlertRule rule = {"high-flow", "road-a", 5.0, 1320, 360, 1};
    PTAlertRule saved;
    int count = 0;
    snprintf(dir, sizeof(dir), "pt_zian_test_%ld_%ld", (long)PROCESS_ID(), (long)time(NULL));
    assert(MAKE_DIR(dir) == 0);
    assert(pt_store_open(&store, dir) == 0);
    assert(pt_add_location(&store, &location) == 1);
    assert(pt_put_rule(&store, &rule) == 0); /* Must follow first. */
    assert(pt_follow_location(&store, "road-a") == 1);
    assert(pt_put_rule(&store, &rule) == 1);
    assert(pt_put_rule(&store, &rule) == 0);
    assert(pt_get_rule(&store, "high-flow", &saved) == 1);
    assert(pt_list_rules(&store, count_rules, &count) == 1);
    assert(count == 1);
    assert(pt_save_measurement(&store, &first) == 1);
    assert(pt_save_measurement(&store, &second) == 1);
    assert(pt_save_measurement(&store, &invalid) == 1);
    assert(pt_compare_periods(&store, "road-a", 0, 60000, 120000, 180000, &a, &b) == 0);
    assert(a.valid_count == 1 && a.total_vehicles == 2 && a.average_flow_vpm == 2.0);
    assert(b.valid_count == 1 && b.unavailable_count == 1 && b.average_flow_vpm == 8.0);
    assert(pt_evaluate_alerts(&store, &first, 1380) == 0); /* Below threshold. */
    assert(pt_evaluate_alerts(&store, &second, 720) == 0); /* Outside time window. */
    assert(pt_evaluate_alerts(&store, &second, 1380) == 1);
    assert(pt_evaluate_alerts(&store, &second, 1380) == 0); /* No duplicate. */
    strcpy(second.measurement_id, "m-5");
    assert(pt_evaluate_alerts(&store, &second, 359) == 1); /* Overnight end is exclusive. */
    strcpy(second.measurement_id, "m-6");
    assert(pt_evaluate_alerts(&store, &second, 360) == 0);
    assert(pt_evaluate_alerts(&store, &invalid, 1380) == 0);
    assert(pt_list_alert_events(&store, "road-a", count_events, &count) == 2);
    assert(count == 3);
    rule.enabled = 0;
    assert(pt_put_rule(&store, &rule) == 1);
    strcpy(second.measurement_id, "m-4");
    assert(pt_evaluate_alerts(&store, &second, 1380) == 0);
    rule.enabled = 1;
    assert(pt_put_rule(&store, &rule) == 1);
    assert(pt_unfollow_location(&store, "road-a") == 1);
    assert(pt_evaluate_alerts(&store, &second, 1380) == 0);
    assert(pt_delete_rule(&store, "high-flow") == 1);
    assert(pt_get_rule(&store, "high-flow", &saved) == 0);
    assert(pt_delete_rule(&store, "high-flow") == 0);
    assert(pt_list_rules(&store, count_rules, &count) == 0);
    puts("trend and alert tests passed");
    return 0;
}
