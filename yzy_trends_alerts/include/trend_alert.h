#ifndef PRIVATE_TRAFFIC_TREND_ALERT_H
#define PRIVATE_TRAFFIC_TREND_ALERT_H

#include "location_store.h"

#include <stdint.h>

typedef struct {
    int valid_count;
    int unavailable_count;
    int64_t total_vehicles;
    double average_flow_vpm;
    double min_flow_vpm;
    double max_flow_vpm;
} PTTrendSummary;

typedef struct {
    char rule_id[64];
    char location_id[64];
    double threshold_vpm;
    unsigned start_minute;  /* Minutes after local midnight, 0..1439. */
    unsigned end_minute;    /* Exclusive. Equal to start means all day. */
    int enabled;
} PTAlertRule;

typedef struct {
    char rule_id[64];
    char measurement_id[64];
    char location_id[64];
    uint64_t measurement_time_ms;
    double flow_vpm;
    double threshold_vpm;
} PTAlertEvent;

typedef int (*PTAlertRuleVisitor)(const PTAlertRule *, void *);
typedef int (*PTAlertEventVisitor)(const PTAlertEvent *, void *);

/* Only validity="valid" contributes to numeric trend values. */
int pt_trend_summary(PTStore *store, const char *location_id,
                     uint64_t from_ms, uint64_t to_ms, PTTrendSummary *out);
int pt_compare_periods(PTStore *store, const char *location_id,
                       uint64_t first_from, uint64_t first_to,
                       uint64_t second_from, uint64_t second_to,
                       PTTrendSummary *first, PTTrendSummary *second);

/* Rules require a followed location. Return 1 changed, 0 unchanged/missing, -1 error. */
int pt_put_rule(PTStore *store, const PTAlertRule *rule);
int pt_get_rule(PTStore *store, const char *rule_id, PTAlertRule *out);
int pt_delete_rule(PTStore *store, const char *rule_id);
int pt_list_rules(PTStore *store, PTAlertRuleVisitor visitor, void *context);

/* Call after saving each fresh measurement. minute_of_day is the user's local time. */
/* Returns the count of new events. Re-evaluating the same rule/measurement is safe. */
int pt_evaluate_alerts(PTStore *store, const PTMeasurement *measurement,
                       unsigned minute_of_day);
int pt_list_alert_events(PTStore *store, const char *location_id,
                         PTAlertEventVisitor visitor, void *context);

#endif
