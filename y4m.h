#ifndef PRIVATE_TRAFFIC_Y4M_H
#define PRIVATE_TRAFFIC_Y4M_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef struct {
    FILE *file;
    int width, height, fps_num, fps_den;
    uint64_t frame_index;
    size_t luma_size, chroma_size;
    uint8_t *luma;
} PTY4M;

/* Supports planar 8-bit 4:2:0 Y4M. Returns 0 on success. */
int pt_y4m_open(PTY4M *video, const char *path);
/* Returns 1 for a frame, 0 at clean EOF, -1 for malformed/truncated input. */
int pt_y4m_next(PTY4M *video, uint64_t *timestamp_ms);
void pt_y4m_close(PTY4M *video);

#endif
