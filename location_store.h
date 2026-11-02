#ifndef PRIVATE_TRAFFIC_LOCATION_STORE_H
#define PRIVATE_TRAFFIC_LOCATION_STORE_H

#include "measurement.h"
#include <stdint.h>

typedef struct {
    char location_id[64];
    char display_name[128];
    char source_ref[256];
    char source_mode[16];     /* live or replay */
    char permission_status[16]; /* approved or pending */
} PTLocation;

typedef struct {
    char directory[512];
} PTStore;

typedef int (*PTLocationVisitor)(const PTLocation *, int followed, void *);
typedef int (*PTMeasurementVisitor)(const PTMeasurement *, void *);

/* One local user's follows. Caller supplies an existing writable directory. */
int pt_store_open(PTStore *store, const char *directory);
int pt_add_location(PTStore *store, const PTLocation *location);
int pt_get_location(PTStore *store, const char *location_id, PTLocation *out);
int pt_list_locations(PTStore *store, PTLocationVisitor visitor, void *context);
int pt_follow_location(PTStore *store, const char *location_id);
int pt_unfollow_location(PTStore *store, const char *location_id);
/* Returns 1 if followed, 0 if not followed, -1 on I/O error. */
int pt_is_followed(PTStore *store, const char *location_id);
/* Only records locations present in catalog; preserves invalid measurements. */
int pt_save_measurement(PTStore *store, const PTMeasurement *measurement);
/* Returns 1 if found, 0 if none, -1 on I/O error. Latest includes invalid status. */
int pt_get_current_status(PTStore *store, const char *location_id, PTMeasurement *out);
/* Inclusive time bounds, ordered by insertion; returns matching count or -1. */
int pt_query_measurements(PTStore *store, const char *location_id,
                          uint64_t from_ms, uint64_t to_ms,
                          PTMeasurementVisitor visitor, void *context);

#endif
