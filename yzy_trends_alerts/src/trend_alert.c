#include "trend_alert.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#define MAX_RULES 128

typedef struct {
    PTAlertRule value;
    int exists;
} RuleSlot;

static int valid_id(const char *s) {
    size_t n = 0;
    if (!s || !*s) return 0;
    while (n < 64 && s[n]) {
        if (!isalnum((unsigned char)s[n]) && s[n] != '-' && s[n] != '_') return 0;
        ++n;
    }
    return n > 0 && n < 64 && s[n] == '\0';
}

static int data_path(const PTStore *store, const char *name, char *out, size_t size) {
    int n;
    if (!store || !store->directory[0]) return -1;
    n = snprintf(out, size, "%s/%s", store->directory, name);
    return n > 0 && (size_t)n < size ? 0 : -1;
}

static int open_file(const PTStore *store, const char *name, const char *mode, FILE **out) {
    char path[600];
    if (data_path(store, name, path, sizeof(path)) != 0) return -1;
    *out = fopen(path, mode);
    return *out ? 0 : -1;
}

static int read_optional(const PTStore *store, const char *name, FILE **out) {
    char path[600];
    if (data_path(store, name, path, sizeof(path)) != 0) return -1;
    *out = fopen(path, "rb");
    if (*out) return 0;
    *out = fopen(path, "ab");
    if (!*out || fclose(*out) != 0) return -1;
    return open_file(store, name, "rb", out);
}

static int summary_visitor(const PTMeasurement *m, void *context) {
    PTTrendSummary *s = context;
    if (strcmp(m->validity, "valid") != 0) {
        ++s->unavailable_count;
        return 0;
    }
    if (s->valid_count == 0 || m->flow_vpm < s->min_flow_vpm) s->min_flow_vpm = m->flow_vpm;
    if (s->valid_count == 0 || m->flow_vpm > s->max_flow_vpm) s->max_flow_vpm = m->flow_vpm;
    ++s->valid_count;
    s->total_vehicles += m->vehicle_count;
    s->average_flow_vpm += m->flow_vpm;
    return 0;
}

int pt_trend_summary(PTStore *store, const char *id, uint64_t from,
                     uint64_t to, PTTrendSummary *out) {
    int result;
    if (!store || !valid_id(id) || !out || from > to) return -1;
    memset(out, 0, sizeof(*out));
    result = pt_query_measurements(store, id, from, to, summary_visitor, out);
    if (result < 0) return -1;
    if (out->valid_count) out->average_flow_vpm /= out->valid_count;
    return 0;
}

int pt_compare_periods(PTStore *store, const char *id,
                       uint64_t first_from, uint64_t first_to,
                       uint64_t second_from, uint64_t second_to,
                       PTTrendSummary *first, PTTrendSummary *second) {
    if (!first || !second || first == second) return -1;
    if (pt_trend_summary(store, id, first_from, first_to, first) != 0) return -1;
    return pt_trend_summary(store, id, second_from, second_to, second);
}

static int find_slot(RuleSlot *slots, int count, const char *id) {
    int i;
    for (i = 0; i < count; ++i) {
        if (strcmp(slots[i].value.rule_id, id) == 0) return i;
    }
    return -1;
}

static int load_rules(PTStore *store, RuleSlot *slots, int *count) {
    FILE *f;
    char line[256], op;
    PTAlertRule x;
    int index, n = 0;
    if (read_optional(store, "alert_rules.psv", &f) != 0) return -1;
    while (fgets(line, sizeof(line), f)) {
        if (!strchr(line, '\n') && !feof(f)) { fclose(f); return -1; }
        memset(&x, 0, sizeof(x));
        if (sscanf(line, "%c|%63[^|]|%63[^|]|%lf|%u|%u|%d", &op,
                   x.rule_id, x.location_id, &x.threshold_vpm,
                   &x.start_minute, &x.end_minute, &x.enabled) != 7 ||
            !valid_id(x.rule_id) || !valid_id(x.location_id) ||
            !isfinite(x.threshold_vpm) || x.threshold_vpm <= 0 ||
            x.start_minute >= 1440 || x.end_minute >= 1440 ||
            (x.enabled != 0 && x.enabled != 1) || (op != 'P' && op != 'D')) {
            fclose(f); return -1;
        }
        index = find_slot(slots, n, x.rule_id);
        if (index < 0) {
            if (n == MAX_RULES) { fclose(f); return -1; }
            index = n++;
        }
        slots[index].value = x;
        slots[index].exists = op == 'P';
    }
    if (ferror(f)) { fclose(f); return -1; }
    if (fclose(f) != 0) return -1;
    *count = n;
    return 0;
}

static int append_rule(PTStore *store, const PTAlertRule *x, char op) {
    FILE *f;
    int written, closed;
    if (open_file(store, "alert_rules.psv", "ab", &f) != 0) return -1;
    written = fprintf(f, "%c|%s|%s|%.6f|%u|%u|%d\n", op,
                      x->rule_id, x->location_id, x->threshold_vpm,
                      x->start_minute, x->end_minute, x->enabled);
    closed = fclose(f);
    return written > 0 && closed == 0 ? 0 : -1;
}

int pt_put_rule(PTStore *store, const PTAlertRule *rule) {
    RuleSlot slots[MAX_RULES] = {0};
    PTLocation location;
    int count, index, followed;
    if (!store || !rule || !valid_id(rule->rule_id) || !valid_id(rule->location_id) ||
        !isfinite(rule->threshold_vpm) || rule->threshold_vpm <= 0 ||
        rule->start_minute >= 1440 || rule->end_minute >= 1440 ||
        (rule->enabled != 0 && rule->enabled != 1)) return -1;
    count = pt_get_location(store, rule->location_id, &location);
    if (count <= 0) return count;
    followed = pt_is_followed(store, rule->location_id);
    if (followed < 0) return -1;
    if (!followed) return 0;
    if (load_rules(store, slots, &count) != 0) return -1;
    index = find_slot(slots, count, rule->rule_id);
    if (index < 0 && count == MAX_RULES) return -1;
    if (index >= 0 && slots[index].exists) {
        PTAlertRule *old = &slots[index].value;
        if (strcmp(old->location_id, rule->location_id) == 0 &&
            old->threshold_vpm == rule->threshold_vpm &&
            old->start_minute == rule->start_minute &&
            old->end_minute == rule->end_minute && old->enabled == rule->enabled) return 0;
    }
    return append_rule(store, rule, 'P') == 0 ? 1 : -1;
}

int pt_get_rule(PTStore *store, const char *id, PTAlertRule *out) {
    RuleSlot slots[MAX_RULES] = {0};
    int count, index;
    if (!store || !valid_id(id) || !out || load_rules(store, slots, &count) != 0) return -1;
    index = find_slot(slots, count, id);
    if (index < 0 || !slots[index].exists) return 0;
    *out = slots[index].value;
    return 1;
}

int pt_delete_rule(PTStore *store, const char *id) {
    PTAlertRule rule;
    int found = pt_get_rule(store, id, &rule);
    if (found <= 0) return found;
    return append_rule(store, &rule, 'D') == 0 ? 1 : -1;
}

int pt_list_rules(PTStore *store, PTAlertRuleVisitor visitor, void *context) {
    RuleSlot slots[MAX_RULES] = {0};
    int count, i, active = 0;
    if (!store || !visitor || load_rules(store, slots, &count) != 0) return -1;
    for (i = 0; i < count; ++i) {
        if (!slots[i].exists) continue;
        if (visitor(&slots[i].value, context) != 0) return -1;
        ++active;
    }
    return active;
}

static int parse_event(const char *line, PTAlertEvent *out) {
    PTAlertEvent x = {0};
    unsigned long long time;
    if (sscanf(line, "%63[^|]|%63[^|]|%63[^|]|%llu|%lf|%lf",
               x.rule_id, x.measurement_id, x.location_id, &time,
               &x.flow_vpm, &x.threshold_vpm) != 6 ||
        !valid_id(x.rule_id) || !valid_id(x.measurement_id) ||
        !valid_id(x.location_id) || !isfinite(x.flow_vpm) ||
        !isfinite(x.threshold_vpm)) return -1;
    x.measurement_time_ms = (uint64_t)time;
    *out = x;
    return 0;
}

int pt_list_alert_events(PTStore *store, const char *id,
                         PTAlertEventVisitor visitor, void *context) {
    FILE *f;
    char line[350];
    PTAlertEvent event;
    int count = 0;
    if (!store || (id && !valid_id(id)) || !visitor ||
        read_optional(store, "alert_events.psv", &f) != 0) return -1;
    while (fgets(line, sizeof(line), f)) {
        if (parse_event(line, &event) != 0) { fclose(f); return -1; }
        if (id && strcmp(id, event.location_id) != 0) continue;
        if (visitor(&event, context) != 0) { fclose(f); return -1; }
        ++count;
    }
    if (ferror(f)) { fclose(f); return -1; }
    fclose(f);
    return count;
}

typedef struct { const char *rule_id; const char *measurement_id; int found; } EventMatch;

static int match_event(const PTAlertEvent *event, void *context) {
    EventMatch *m = context;
    if (strcmp(event->rule_id, m->rule_id) == 0 &&
        strcmp(event->measurement_id, m->measurement_id) == 0) m->found = 1;
    return 0;
}

static int in_window(const PTAlertRule *rule, unsigned minute) {
    if (rule->start_minute == rule->end_minute) return 1;
    if (rule->start_minute < rule->end_minute)
        return minute >= rule->start_minute && minute < rule->end_minute;
    return minute >= rule->start_minute || minute < rule->end_minute;
}

int pt_evaluate_alerts(PTStore *store, const PTMeasurement *m, unsigned minute) {
    RuleSlot slots[MAX_RULES] = {0};
    EventMatch match;
    FILE *f;
    int count, i, created = 0, listed, written, closed;
    if (!store || !m || !valid_id(m->measurement_id) || !valid_id(m->location_id) ||
        !isfinite(m->flow_vpm) || m->flow_vpm < 0 || minute >= 1440) return -1;
    if (strcmp(m->validity, "valid") != 0) return 0;
    listed = pt_is_followed(store, m->location_id);
    if (listed <= 0) return listed;
    if (load_rules(store, slots, &count) != 0) return -1;
    for (i = 0; i < count; ++i) {
        PTAlertRule *r = &slots[i].value;
        if (!slots[i].exists || !r->enabled || strcmp(r->location_id, m->location_id) != 0 ||
            !in_window(r, minute) || m->flow_vpm < r->threshold_vpm) continue;
        match.rule_id = r->rule_id;
        match.measurement_id = m->measurement_id;
        match.found = 0;
        listed = pt_list_alert_events(store, m->location_id, match_event, &match);
        if (listed < 0) return -1;
        if (match.found) continue;
        if (open_file(store, "alert_events.psv", "ab", &f) != 0) return -1;
        written = fprintf(f, "%s|%s|%s|%llu|%.6f|%.6f\n", r->rule_id,
                          m->measurement_id, m->location_id,
                          (unsigned long long)m->measurement_time_ms,
                          m->flow_vpm, r->threshold_vpm);
        closed = fclose(f);
        if (written <= 0 || closed != 0) return -1;
        ++created;
    }
    return created;
}
