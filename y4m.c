#include "y4m.h"

#include <stdlib.h>
#include <string.h>

int pt_y4m_open(PTY4M *v, const char *path) {
    char header[1024], *tok, *save;
    int chroma_ok = 1;
    memset(v, 0, sizeof(*v));
    v->file = fopen(path, "rb");
    if (!v->file) return -1;
    if (!fgets(header, sizeof(header), v->file) ||
        strncmp(header, "YUV4MPEG2 ", 10) != 0 ||
        !strchr(header, '\n')) goto fail;
    v->fps_num = 25;
    v->fps_den = 1;
    for (tok = strtok_r(header + 10, " \r\n", &save); tok;
         tok = strtok_r(NULL, " \r\n", &save)) {
        if (tok[0] == 'W') v->width = atoi(tok + 1);
        else if (tok[0] == 'H') v->height = atoi(tok + 1);
        else if (tok[0] == 'F') {
            if (sscanf(tok + 1, "%d:%d", &v->fps_num, &v->fps_den) != 2) goto fail;
        } else if (tok[0] == 'C') {
            chroma_ok = strcmp(tok + 1, "420") == 0 ||
                        strcmp(tok + 1, "420jpeg") == 0 ||
                        strcmp(tok + 1, "420mpeg2") == 0 ||
                        strcmp(tok + 1, "420paldv") == 0;
        }
    }
    if (!chroma_ok || v->width < 2 || v->height < 2 ||
        v->width % 2 || v->height % 2 || v->width > 4096 || v->height > 2160 ||
        v->fps_num <= 0 || v->fps_den <= 0) goto fail;
    v->luma_size = (size_t)v->width * (size_t)v->height;
    v->chroma_size = v->luma_size / 2;
    v->luma = malloc(v->luma_size);
    if (!v->luma) goto fail;
    return 0;
fail:
    pt_y4m_close(v);
    return -1;
}

int pt_y4m_next(PTY4M *v, uint64_t *timestamp_ms) {
    char line[1024];
    uint8_t scratch[8192];
    size_t remaining, chunk;
    if (!fgets(line, sizeof(line), v->file)) return feof(v->file) ? 0 : -1;
    if (strncmp(line, "FRAME", 5) != 0 ||
        (line[5] != '\n' && line[5] != ' ' && line[5] != '\r') ||
        !strchr(line, '\n')) return -1;
    if (fread(v->luma, 1, v->luma_size, v->file) != v->luma_size) return -1;
    remaining = v->chroma_size;
    while (remaining) {
        chunk = remaining < sizeof(scratch) ? remaining : sizeof(scratch);
        if (fread(scratch, 1, chunk, v->file) != chunk) return -1;
        remaining -= chunk;
    }
    *timestamp_ms = v->frame_index * 1000ULL * (uint64_t)v->fps_den /
                    (uint64_t)v->fps_num;
    ++v->frame_index;
    return 1;
}

void pt_y4m_close(PTY4M *v) {
    if (v->file) fclose(v->file);
    free(v->luma);
    memset(v, 0, sizeof(*v));
}
